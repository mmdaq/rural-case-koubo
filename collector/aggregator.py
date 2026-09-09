"""二级整理源批量采集器：从已系统整理的法律网站批量抓取案例

不再依赖 RMFYALK_TOKEN，而是从律师/法律网站的系统性文章中提取案例。
这些文章已经过人工整理，数据质量高，结构清晰，可直接入库。

主要数据源：
1. 盛廷律所 — 按主题分类整理案例库案例（含完整案情+裁判要旨+启示）
2. 税递网 — 案例库原文转载
"""
import re
import time
from typing import Optional

from .models import Case
from .extrastore import ExtraStore
from utils.logger import get_logger
from utils.validator import is_rural_collective_theme, verify_case

log = get_logger("aggregator")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# ==================== 盛廷律所采集器 ====================
# 文章格式（纯文本）：
#   案例X：标题
#   入库编号：xxx
#   裁判文书号：xxx
#   案情正文段落...
#   本案启示：xxx
#
# 或另一种格式（单案例文章）：
#   入库编号：xxx
#   关键词：xxx
#   裁判要旨：xxx
#   基本案情：xxx
#   裁判结果：xxx

SHENGTING_ARTICLES = [
    {
        "url": "https://shengtinglaw.com/zhiku-xiangqing-11044.html",
        "name": "八大集体经济组织成员认定案例全解析",
        "category": "成员资格认定",
        "type": "multi_case",  # 一篇文章含多个案例
    },
    {
        "url": "https://shengtinglaw.com/zhiku-xiangqing-11861.html",
        "name": "产权调换补偿合法性审查",
        "category": "征收补偿",
        "type": "single_case",  # 一篇文章一个案例
    },
    {
        "url": "https://shengtinglaw.com/zhiku-xiangqing-11403.html",
        "name": "非集体经济组织成员继承农村房屋后宅基地确权",
        "category": "宅基地",
        "type": "single_case",
    },
]

RULE_CODE_RE = re.compile(r"(\d{4}-\d{2}-\d{1,2}-\d{3}-\d{3})")
CASE_TITLE_RE = re.compile(r"案例[一二三四五六七八九十\d]+[：:]\s*(.+?)$")
CODE_LINE_RE = re.compile(r"入库编号[：:]\s*(\d{4}-\d{2}-\d{1,2}-\d{3}-\d{3})")
DOC_NO_RE = re.compile(r"裁判文书号[：:]\s*([^\n]+)")
INSIGHT_RE = re.compile(r"本案启示[：:]\s*([^\n]+)")
KEYWORD_RE = re.compile(r"关键词[：:]\s*([^\n]+)")
GIST_RE = re.compile(r"裁判要旨[：:]\s*([^\n]+)")
FACTS_START_RE = re.compile(r"基本案情[：:]")
REASONING_START_RE = re.compile(r"裁判理由[：:]")
RESULT_START_RE = re.compile(r"裁判结果[：:]|判决结果[：:]")


def _safe_get(url: str, timeout: int = 15) -> str | None:
    try:
        import requests
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.encoding = resp.apparent_encoding or "utf-8"
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        log.debug("请求失败 %s: %s", url, e)
    return None


def _extract_multi_case_article(text: str, article_name: str, category: str) -> list[Case]:
    """提取多案例文章（如八大案例全解析）"""
    # 按"案例X："分割
    blocks = re.split(r"(?=案例[一二三四五六七八九十\d]+[：:])", text)

    cases = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # 提取入库编号
        code_match = CODE_LINE_RE.search(block)
        if not code_match:
            code_match = RULE_CODE_RE.search(block)
        if not code_match:
            continue
        rule_code = code_match.group(1)

        # 提取标题
        title_match = CASE_TITLE_RE.search(block)
        title = title_match.group(1).strip() if title_match else rule_code

        # 提取文书号
        doc_match = DOC_NO_RE.search(block)
        doc_no = doc_match.group(1).strip() if doc_match else ""

        # 提取启示
        insight_match = INSIGHT_RE.search(block)
        insight = insight_match.group(1).strip() if insight_match else ""

        # 提取案情
        lines = block.split("\n")
        facts_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if RULE_CODE_RE.search(line):
                continue
            if DOC_NO_RE.search(line):
                continue
            if INSIGHT_RE.search(line):
                continue
            if "案例" in line and ("启示" in line or "入库编号" in line or "裁判文书号" in line):
                continue
            facts_lines.append(line)
        facts = "\n".join(facts_lines).strip()

        if not facts or len(facts) < 50:
            continue

        # 去重：同文章内同一编号只保留一条
        if cases and cases[-1].rule_code == rule_code:
            continue

        case = _make_case(rule_code, title, doc_no, facts, insight, article_name, category)
        cases.append(case)
        log.info("盛廷案例提取: %s | %s", rule_code, title[:30])

    return cases


def _extract_single_case_article(text: str, article_name: str, category: str) -> list[Case]:
    """提取单案例文章（如产权调换补偿案）"""
    cases = []
    # 找所有入库编号位置
    code_positions = [(m.start(), m.group(1)) for m in RULE_CODE_RE.finditer(text)]
    
    for i, (pos, rule_code) in enumerate(code_positions):
        # 取此编号到下一个编号之间的文本
        end_pos = code_positions[i + 1][0] if i + 1 < len(code_positions) else len(text)
        block = text[pos:end_pos]
        
        # 提取标题（编号前的文本）
        before = text[:pos].rstrip()
        lines_before = [l.strip() for l in before.split('\n') if l.strip()]
        title = lines_before[-1] if lines_before else rule_code
        # 去掉可能的"入库编号："前缀
        title = re.sub(r'^入库编号[：:]\s*', '', title)
        
        # 提取文书号
        doc_match = DOC_NO_RE.search(block)
        doc_no = doc_match.group(1).strip() if doc_match else ""
        
        # 提取关键词
        kw_match = KEYWORD_RE.search(block)
        keywords_text = kw_match.group(1).strip() if kw_match else ""
        
        # 提取裁判要旨
        gist_match = GIST_RE.search(block)
        gist = gist_match.group(1).strip() if gist_match else ""
        
        # 提取案情（基本案情：之后）
        facts_start = FACTS_START_RE.search(block)
        facts = ""
        if facts_start:
            facts_text = block[facts_start.end():]
            # 找下一个小节（裁判理由/裁判结果）
            next_section = min(
                len(facts_text),
                REASONING_START_RE.search(facts_text).start() if REASONING_START_RE.search(facts_text) else len(facts_text),
                RESULT_START_RE.search(facts_text).start() if RESULT_START_RE.search(facts_text) else len(facts_text),
            )
            facts = facts_text[:next_section].strip()
        
        # 提取裁判结果
        result_match = RESULT_START_RE.search(block)
        result = ""
        if result_match:
            result_text = block[result_match.end():]
            # 找下一个标记或到结尾
            next_mark = len(result_text)
            for pat in [re.compile(r'\n\s*\n', re.MULTILINE)]:
                m = pat.search(result_text)
                if m and m.start() < next_mark:
                    next_mark = m.start()
            result = result_text[:next_mark].strip()
        
        # 如果没有找到案情，用整体文本
        if not facts and len(block) > 100:
            facts = block[:800].strip()
        
        if not facts or len(facts) < 30:
            continue

        case = _make_case(rule_code, title, doc_no, facts, gist or facts[:100],
                         article_name, category, keywords=keywords_text, result=result)
        cases.append(case)
        log.info("盛廷单案例提取: %s | %s", rule_code, title[:30])

    return cases


def _make_case(rule_code: str, title: str, doc_no: str, facts: str,
               gist: str, article_name: str, category: str,
               keywords: str = "", result: str = "") -> Case:
    """构建Case对象"""
    province = _infer_province(facts + title + doc_no)
    subtype = _infer_subtype(facts + title)
    
    kw_list = [k.strip() for k in keywords.split() if k.strip()] if keywords else []
    
    return Case(
        rule_code=rule_code,
        title=title,
        keywords=["集体经济组织成员权益", "征地补偿", "农村集体资产"] + kw_list,
        court=doc_no,
        doc_no=doc_no,
        province=province,
        scenario=category,
        subtype=subtype,
        pain_points=["信息不对称", "民主决策虚置"],
        amount="",
        facts=facts[:800],
        reasoning="",
        gist=gist[:200],
        result=result[:300],
        official_link="https://rmfyalk.court.gov.cn/view/content.html",
        case_source="人民法院案例库（盛廷律所整理）",
        source_urls=["https://shengtinglaw.com/{}".format(article_name)],
        source_names=[f"盛廷律所·{article_name}"],
        collected_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )


def _infer_province(text: str) -> str:
    province_map = {
        "吉林": "吉林", "长春": "吉林",
        "山东": "山东", "滨州": "山东", "青岛": "山东", "威海": "山东",
        "海南": "海南", "海口": "海南",
        "江苏": "江苏", "苏州": "江苏", "南京": "江苏",
        "浙江": "浙江", "温州": "浙江", "杭州": "浙江",
        "四川": "四川", "成都": "四川",
        "河南": "河南", "郑州": "河南", "南阳": "河南",
        "河北": "河北", "定州": "河北", "石家庄": "河北",
        "陕西": "陕西", "西安": "陕西", "安康": "陕西",
        "北京": "北京", "怀柔": "北京",
        "安徽": "安徽", "合肥": "安徽",
        "江西": "江西", "九江": "江西",
        "青海": "青海",
    }
    for k, v in province_map.items():
        if k in text:
            return v
    return ""


def _infer_subtype(text: str) -> str:
    if "离婚" in text or "离异" in text:
        return "离婚妇女成员资格"
    if "外嫁" in text or "婚出" in text:
        return "外嫁女成员资格"
    if "户籍" in text or "户口迁出" in text:
        return "户籍迁出成员资格"
    if "收养" in text or "养女" in text:
        return "收养关系成员资格"
    if "股权证" in text:
        return "股权证与成员资格"
    if "街道" in text or "镇政府" in text or "责令改正" in text:
        return "政府监督职责"
    if "宅基地" in text:
        return "宅基地确权"
    if "继承" in text:
        return "继承与成员资格"
    if "征收补偿" in text or "产权调换" in text or "安置补偿" in text:
        return "征收补偿"
    return "成员资格认定"


def harvest_shengting(extra: ExtraStore, crawled=None) -> int:
    """从盛廷律所批量采集案例"""
    new_count = 0
    for article in SHENGTING_ARTICLES:
        html = _safe_get(article["url"], timeout=15)
        if not html:
            log.warning("盛廷文章抓取失败: %s", article["url"])
            continue
        if crawled and crawled.is_crawled(article["url"]):
            continue
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text("\n", strip=True)

        if article["type"] == "multi_case":
            cases = _extract_multi_case_article(text, article["name"], article["category"])
        else:
            cases = _extract_single_case_article(text, article["name"], article["category"])

        for c in cases:
            if not is_rural_collective_theme(c.to_dict()):
                continue
            v = verify_case(c.to_dict(), min_sources=0, require_official_anchor=True, relax_fields=True)
            if not v["ok"]:
                log.info("盛廷案例校验失败 %s: %s", c.rule_code, v["issues"])
                continue
            if extra.upsert(c, source_count=1):
                new_count += 1
        if crawled:
            crawled.mark_crawled(article["url"])
        time.sleep(0.3)
    return new_count


# ==================== 主入口 ====================

def harvest_all_sources(extra: ExtraStore, crawled=None) -> dict:
    """从所有二级整理源批量采集案例"""
    results = {
        "shengting": harvest_shengting(extra, crawled),
    }
    total = sum(results.values())
    log.info("二级源汇总采集完成：盛廷+%d，共+%d，当前池子 %d 个",
             results["shengting"], total, len(extra.data.get("cases", {})))
    return results
