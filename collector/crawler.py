"""案例采集器：多源抓取 + 容错降级 + 自我扩充

采集策略（任一环节失败不阻塞整体）：
1. rmfyalk   —— 人民法院案例库官网（需登录态，多数环境不可用，仅探测）
2. court_gov —— 最高人民法院官网涉农典型案例（固定栏目）
3. discover  —— 【自我扩充核心】三层探索：
                 a. 预置转载链接池（人工核实的案例库原文页，含一页多案例专题）
                 b. 转载源列表翻页（税递网等，标题预过滤）
                 c. 搜索引擎检索（辅助）
                 → extractor 提取结构化案例 → 主题过滤 → 校验 → 写入本地扩展案例库
4. fallback  —— 内置种子案例（真实已核实案例），保证每日任务可产出
"""
import random
import re
import time
from urllib.parse import urljoin

from .extractor import extract_case, extract_cases_multi
from .fallback import SEED_CASES
from .extrastore import ExtraStore, CrawlStore
from .models import Case
from .sources import RMFYALK_SEARCH, COURT_GOV_AGRICULTURE, SEARCH_ENGINES
from utils.logger import get_logger
from utils.validator import verify_case, is_official_url, is_rural_collective_theme
from generator.painpoints import enrich_case

log = get_logger("collector")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}

RULE_CODE_RE = re.compile(r"\d{4}-\d{2}-\d{1,2}-\d{3}-\d{3}")

# 农村/集体资产主题关键词（提取结果必须命中，防止无关案例混入）
TOPIC_HINTS = [
    "集体经济组织", "征地", "征收补偿", "土地承包", "集体收益", "分红",
    "成员资格", "宅基地", "村规民约", "外嫁女", "村民小组", "村委会",
    "股权证", "承包地", "安置补助", "青苗", "入市", "集体资产",
    "责任田", "入赘", "收益分配", "经营权", "承包金", "成员权益",
]


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


def fetch_rmfyalk(keywords: list) -> list[Case]:
    """人民法院案例库官网检索（登录墙，通常失败，仅探测）"""
    try:
        html = _safe_get(RMFYALK_SEARCH, timeout=8)
        if not html:
            log.info("案例库官网不可达（需登录），跳过")
            return []
    except Exception:
        pass
    return []


def fetch_court_gov(**kwargs) -> list[Case]:
    """最高人民法院官网涉农民事典型案例"""
    from bs4 import BeautifulSoup
    html = _safe_get(COURT_GOV_AGRICULTURE)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    if "蒋某某" not in text:
        return []
    return [
        Case(
            rule_code="2024-07-2-044-003",
            title="蒋某某诉某社区第一居民组侵害集体经济组织成员权益纠纷案",
            court="最高人民法院发布涉农民事典型案例（案例4）",
            province="某试点地区",
            scenario="分配方案",
            subtype="集体收益分配方案（无地/少地成员）",
            pain_points=["民主决策虚置", "信息不对称"],
            amount="1651.42万元",
            facts=text[:600],
            reasoning="分配方案占用全体成员共有的集体收益，损害无地或少地成员权益，依法应予撤销。",
            gist="集体收益分配方案不得损害无地、少地成员的合法权益。",
            official_link=COURT_GOV_AGRICULTURE,
            case_source="最高院典型案例",
            source_urls=[COURT_GOV_AGRICULTURE],
            source_names=["最高人民法院官网"],
        )
    ]


# ---------------- 预置转载链接池 ----------------

# 人工核实的案例库原文/专题页面（multi=True 表示一页含多个案例，如澎湃"一网一库"专题）
SEED_LINKS = [
    {"url": "https://m.thepaper.cn/newsDetail_forward_32405836", "name": "澎湃·一网一库专题第61期", "multi": True},
    {"url": "https://www.zhongliaolvshi.com/zhongliaoshuofa/3526.html", "name": "中辽律师·成员资格认定案例解析", "multi": True},
    {"url": "https://www.zhongliaolvshi.com/zhongliaoshuofa/3528.html", "name": "中辽律师·成员资格认定案例解析", "multi": True},
    {"url": "http://jingsongls.com/jsyw/1226.html", "name": "京讼律师·户籍非唯一因素案例", "multi": False},
    {"url": "https://www.taxdy.cn/h-nd-293634.html", "name": "税递网案例库原文", "multi": False},
    {"url": "https://www.taxdy.cn/h-nd-294941.html", "name": "税递网案例库原文", "multi": False},
    {"url": "https://www.taxdy.cn/h-nd-295450.html", "name": "税递网案例库原文", "multi": False},
    {"url": "https://www.taxdy.cn/h-nd-295577.html", "name": "税递网案例库原文", "multi": False},
    {"url": "https://m.055110.com/fl/3/6279.html", "name": "安徽律师网案例库原文", "multi": False},
    {"url": "https://shengtinglaw.com/qita-xiangqing-11044.html", "name": "圣廷律师案例解析", "multi": True},
]


def fetch_seed_links(crawled: CrawlStore | None = None) -> list[Case]:
    """抓预置链接池（含一页多案例页面）"""
    found: list[Case] = []
    for item in SEED_LINKS:
        try:
            if crawled and crawled.is_crawled(item["url"]):
                continue
            html = _safe_get(item["url"], timeout=15)
            if not html:
                continue
            if crawled:
                crawled.mark_crawled(item["url"])
            if item.get("multi"):
                cases = extract_cases_multi(html, item["url"], item["name"])
            else:
                case = extract_case(html, item["url"], item["name"])
                cases = [case] if case else []
            for c in cases:
                found.append(c)
                log.info("预置链接提取: %s | %s", c.rule_code, (c.title or "")[:40])
            time.sleep(0.4)
        except Exception as e:
            log.warning("预置链接 %s 异常: %s", item["url"], e)
    return found


# ---------------- 转载源列表翻页 ----------------

# 已知的"人民法院案例库"转载栏目（可自行追加新源）
FEED_SOURCES = [
    {
        "name": "税递网·人民法院案例库栏目",
        "url": "https://www.taxdy.cn/h-nr--0_865_520.html",
        "link_pattern": "h-nd-",
        "page_param": "m31pageno",
        "max_pages": 60,
        # 该栏目全部为人民法院案例库内容，无需标题预过滤，扩大案例池
        "prefilter": False,
    },
    {
        "name": "安徽律师网·民事参考案例",
        "url": "https://m.055110.com/fl/3/",
        "link_pattern": "fl/3/",
        "page_param": None,
        "max_pages": 3,
        "prefilter": True,
    },
]


def _extract_detail_links(list_html: str, pattern: str | None, base_url: str = "") -> list[tuple[str, str]]:
    """从列表页提取详情链接（支持相对路径），返回 [(url, 标题文本)]"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(list_html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("javascript", "#", "mailto:")):
            continue
        if pattern and pattern not in href:
            continue
        full = urljoin(base_url, href)
        if not full.startswith("http"):
            continue
        title = a.get_text(strip=True)
        links.append((full, title))
    return list(dict.fromkeys(links))


def _list_title_relevant(title: str) -> bool:
    """列表项标题预过滤：标题含涉农关键词才值得抓详情"""
    if not title:
        return True
    return any(k in title for k in TOPIC_HINTS)


def fetch_feeds(crawled: CrawlStore | None = None, max_fetch: int = 150) -> list[Case]:
    """抓转载源列表页（支持分页）→遍历全部链接做标题预过滤→详情页→提取案例

    翻页收集大量链接，按源配置决定是否标题预过滤；max_fetch 控制每源抓取上限，
    已抓过的详情页（crawled 记录）跳过，聚焦新内容。
    """
    found: list[Case] = []
    seen_urls: set[str] = set()
    for src in FEED_SOURCES:
        max_pages = int(src.get("max_pages", 1))
        page_param = src.get("page_param")
        prefilter = bool(src.get("prefilter", True))
        try:
            detail_links: list[tuple[str, str]] = []
            for page_no in range(1, max_pages + 1):
                url = src["url"]
                if page_param and page_no > 1:
                    sep = "&" if "?" in url else "?"
                    url = f"{url}{sep}{page_param}={page_no}"
                list_html = _safe_get(url, timeout=15)
                if not list_html:
                    continue
                detail_links.extend(_extract_detail_links(list_html, src.get("link_pattern"), base_url=src["url"]))
                time.sleep(0.3)
            log.info("转载源【%s】翻页获取 %d 个详情链接", src["name"], len(detail_links))
            fetched = 0
            for url, title in detail_links:
                if url in seen_urls:
                    continue
                if crawled and crawled.is_crawled(url):
                    continue
                if prefilter and not _list_title_relevant(title):
                    continue
                if fetched >= max_fetch:
                    break
                seen_urls.add(url)
                fetched += 1
                html = _safe_get(url, timeout=15)
                if not html:
                    continue
                if crawled:
                    crawled.mark_crawled(url)
                case = extract_case(html, url, source_name=src["name"])
                if case:
                    found.append(case)
                    log.info("提取成功: %s | %s", case.rule_code, (case.title or "")[:40])
                time.sleep(0.4)
        except Exception as e:
            log.warning("转载源 %s 异常: %s", src.get("name"), e)
    return found


# ---------------- 搜索引擎检索（辅助源） ----------------

def _search_result_links(query: str, max_links: int = 6) -> list[str]:
    """搜索引擎检索，返回结果页链接（过滤搜索引擎自身域名）"""
    import requests
    from bs4 import BeautifulSoup
    html = _safe_get(SEARCH_ENGINES["bing"].format(q=requests.utils.quote(query)))
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.select("li.b_algo h2 a, h2 a"):
        href = (a.get("href") or "").strip()
        if href.startswith("http") and "bing.com" not in href and "microsoft.com" not in href:
            links.append(href)
    return list(dict.fromkeys(links))[:max_links]


def fetch_search_web(
    keywords: list,
    crawled: CrawlStore | None = None,
    max_pages: int = 2,
    per_page: int = 5,
) -> list[Case]:
    """搜索引擎检索→抓全文→提取案例（结果质量依赖网络环境）"""
    found: list[Case] = []
    seen_urls: set[str] = set()
    for kw in random.sample(keywords, min(len(keywords), max_pages)):
        links = _search_result_links(kw, per_page)
        for url in links:
            if url in seen_urls:
                continue
            if crawled and crawled.is_crawled(url):
                continue
            seen_urls.add(url)
            html = _safe_get(url, timeout=12)
            if not html:
                continue
            if crawled:
                crawled.mark_crawled(url)
            case = extract_case(html, url, source_name="搜索引擎")
            if case:
                found.append(case)
            time.sleep(0.4)
    return found


# ---------------- 自我扩充主入口 ----------------

def discover_new_cases(
    extra: ExtraStore,
    keywords: list,
    crawled: CrawlStore | None = None,
    max_new: int = 10,
) -> list[Case]:
    """探索并入库新案例：预置链接 → 转载源翻页 → 搜索引擎 → 主题过滤 → 校验 → 写扩展库

    返回本次【新入库】的案例列表（已存在扩展库的编号只累计来源数）。
    crawled 记录已抓过的页面，每日只抓新内容，持续扩容。
    """
    discovered: list[Case] = []
    try:
        discovered += fetch_seed_links(crawled)
    except Exception as e:
        log.warning("预置链接采集异常: %s", e)
    try:
        discovered += fetch_feeds(crawled)
    except Exception as e:
        log.warning("转载源采集异常: %s", e)
    try:
        discovered += fetch_search_web(keywords, crawled)
    except Exception as e:
        log.warning("搜索引擎采集异常: %s", e)

    new_cases: list[Case] = []
    seen_codes: set[str] = set()
    for c in discovered:
        if c.rule_code in seen_codes:
            continue
        seen_codes.add(c.rule_code)
        # 官方链接回填：来源为官方域名时，自动作为官方可查链接
        if not c.official_link:
            for u in c.source_urls:
                if is_official_url(u):
                    c.official_link = u
                    break
        # 主题过滤（严格）：必须与农村/集体资产相关，防止刑事/劳动/环保等跑题案例混入
        if not is_rural_collective_theme(c.to_dict()):
            log.info("非农村集体资产主题，丢弃 %s | %s", c.rule_code, (c.title or "")[:30])
            continue
        # 严格校验：必须满足"官方可查锚点"（官方链接 / 编号+文书号 / 多源交叉）
        v = verify_case(c.to_dict(), min_sources=0, require_official_anchor=True)
        if not v["ok"]:
            log.info("提取案例无可查锚点/未通过校验，不入库 %s: %s", c.rule_code, v["issues"])
            continue
        # 回填痛点与细分类型
        c = Case.from_dict(enrich_case(c.to_dict()))
        changed = extra.upsert(c)
        if changed:
            new_cases.append(c)
    log.info(
        "本次探索：发现 %d 个、新入库 %d 个，扩展库累计 %d 个",
        len(discovered), len(new_cases), extra.stats()["total"],
    )
    return new_cases[:max_new]


# ---------------- 主采集入口 ----------------

def collect(
    keywords: list,
    extra: ExtraStore | None = None,
    crawled: CrawlStore | None = None,
    use_fallback: bool = True,
    max_cases: int = 20,
) -> list[Case]:
    """主采集：探索新案例 → 扩展库 → 种子，汇总去重返回

    优先级：种子（人工核实） > 扩展库（自动发现+校验） > 官网固定源
    """
    all_cases: dict[str, Case] = {}

    # 1. 官网/最高院固定源（尽力而为）
    for fetcher in (fetch_rmfyalk, fetch_court_gov):
        try:
            cases = fetcher(keywords=keywords)
            for c in cases:
                if c.rule_code:
                    all_cases.setdefault(c.rule_code, c)
        except Exception as e:
            log.warning("采集源 %s 异常: %s", fetcher.__name__, e)

    # 2. 内置种子兜底（人工核实，先占位，保证案例池截断时种子不被挤出）
    if use_fallback:
        for seed in SEED_CASES:
            all_cases[seed["rule_code"]] = Case.from_dict(seed)

    # 3. 自我扩充：探索新案例并写入扩展库
    if extra is not None:
        try:
            discover_new_cases(extra, keywords, crawled)
        except Exception as e:
            log.warning("案例探索异常（不影响主流程）: %s", e)
        # 扩展库全部案例参与候选（含历史累积）；严格主题过滤 + 官方可查锚点双闸门
        for d in extra.all_cases():
            if not is_rural_collective_theme(d):
                log.info("扩展库案例非农村集体资产主题，跳过: %s %s", d.get("rule_code"), (d.get("title") or "")[:30])
                continue
            v = verify_case(d, min_sources=0, require_official_anchor=True)
            if not v["ok"]:
                log.info("扩展库案例缺少可查锚点，跳过: %s %s", d.get("rule_code"), v["issues"])
                continue
            all_cases.setdefault(d["rule_code"], Case.from_dict(enrich_case(d)))

    cases = list(all_cases.values())
    log.info(
        "候选案例池共 %d 个（扩展库 %d 个）",
        len(cases), extra.stats()["total"] if extra else 0,
    )
    return cases[:max_cases]
