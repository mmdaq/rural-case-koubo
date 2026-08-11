"""案例提取器：从转载页全文提取结构化案例字段

支持两种页面形态：
- 模式A（案例库原文转载站，如税递网）：含"入库编号/关键词/基本案情/裁判理由/裁判要旨"分节
- 模式B（法院官网/新闻稿）：自然叙事文本，仅能提取编号+正文，置信度较低

提取结果必须通过 utils.validator 校验（编号格式、内容要素、字段完整性）才可入库。
"""
import re

from .models import Case
from utils.logger import get_logger
from utils.validator import DOC_NO_RE, is_official_url

log = get_logger("extractor")

# 无锚点版本：用于在全文任意位置检索入库编号（validator 中的版本带 ^$ 锚点，仅适合整串校验）
RULE_CODE_RE = re.compile(r"\d{4}-\d{2}-\d{1,2}-\d{3}-\d{3}")

# 分节结束标志（按出现顺序取最近者）
_SECTION_ENDS = {
    "keywords": ["基本案情", "裁判理由", "裁判要旨", "关联索引"],
    "facts": ["裁判理由", "本院认为", "法院经审理", "裁判要旨"],
    "reasoning": ["裁判要旨", "案例编写", "入库日期"],
    "gist": ["关联索引", "案例编写", "入库日期", "生效裁判"],
}

# 场景自动打标关键词（顺序即优先级：更具体的主题放前面）
SCENARIO_RULES = [
    ("外嫁女·股权证", ["股权证", "产权制度改革"]),
    ("外嫁女·分红", ["分红", "婚出姑娘", "外嫁"]),
    ("离婚妇女", ["离婚", "离异", "解除婚姻"]),
    ("养女资格", ["养女", "收养", "抱养"]),
    ("承包方消亡继承", ["去世", "死亡", "逝世", "继承", "青苗补偿"]),
    ("户籍迁出", ["户籍迁出", "户口迁出", "非本村村民"]),
    ("加入取得·入赘", ["入赘", "招婿", "上门女婿", "婚入", "与女方家庭共同生活"]),
    ("分配方案", ["收益分配方案", "分配方案", "入市"]),
    ("征地补偿", ["征地补偿", "征收补偿", "安置补助", "土地征收"]),
    ("承包地纠纷", ["土地承包经营权合同", "承包经营权合同纠纷", "承包地", "土地承包"]),
    ("村务公开", ["村务公开", "知情权", "查账", "公开集体资产"]),
    ("资金侵占", ["侵占集体", "挪用集体", "贪污", "白条入账"]),
    ("问题合同", ["超长期", "超低价", "民主议定程序", "补充协议书", "越权签订"]),
    ("集体资产租赁", ["租金", "厂房", "商铺", "租赁合同", "转租"]),
]

# 省级行政区（用于从法院名推断省份）
PROVINCES = [
    "北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江",
    "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
    "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "台湾",
    "内蒙古", "广西", "西藏", "宁夏", "新疆", "香港", "澳门",
]

AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万)?\s*元")


def _find_section(text: str, start_kw: str, end_kws: list) -> str:
    """提取 start_kw 到最近 end_kw 之间的文本"""
    i = text.find(start_kw)
    if i < 0:
        return ""
    j = len(text)
    for kw in end_kws:
        k = text.find(kw, i + len(start_kw))
        if 0 < k < j:
            j = k
    seg = text[i + len(start_kw):j].strip()
    # 压缩空白
    return re.sub(r"\s+", "", seg)


def _extract_title(text: str, html_title: str = "", code_pos: int = -1) -> str:
    """案名：优先取编号前最近的'xxx诉xxx案'（页面标题区），其次编号后，页面标题兜底"""
    before = text[:code_pos] if code_pos >= 0 else text
    # 编号前取最后一个'xxx诉xxx纠纷案'（即本案例标题区）
    m = None
    for mm in re.finditer(r"([\u4e00-\u9fa5A-Za-z]{2,10}?诉[^，。\n]{4,50}?纠纷案)", before):
        m = mm
    if m:
        return m.group(1)
    m = re.search(r"入库编号\s*\d{4}-\d{2}-\d{1,2}-\d{3}-\d{3}\s*([^\s，。]{5,80}?案)", text)
    if m:
        return m.group(1)
    m = re.search(r"([\u4e00-\u9fa5A-Za-z]{2,10}?诉[^，。\n]{4,50}?纠纷案)", text)
    if m:
        return m.group(1)
    if html_title and ("诉" in html_title or "纠纷" in html_title):
        t = html_title.split("_")[-1].split("-")[-1].strip()
        if len(t) > 4:
            return t
    return ""


def _extract_scenario(text: str) -> str:
    for scenario, kws in SCENARIO_RULES:
        if any(k in text for k in kws):
            return scenario
    return ""


def _extract_province(text: str, court: str) -> str:
    for p in PROVINCES:
        if p in (court or ""):
            return p
    for p in PROVINCES:
        if p in (text or ""):
            return p
    return ""


def _extract_amount(text: str) -> str:
    """取案情中金额最大的一个，如 '26911.07元' / '26万元'"""
    best = ""
    best_val = 0.0
    for m in AMOUNT_RE.finditer(text):
        v = float(m.group(1)) * (10000 if m.group(2) else 1)
        if v > best_val:
            best_val = v
            best = m.group(0).replace(" ", "")
    return best


def _extract_court(text: str) -> str:
    """提取法院名：优先带审级的全称，过滤'请求人民法院'等非法院名匹配"""
    cands = re.findall(r"([\u4e00-\u9fa5]{2,20}?(?:高级|中级|基层)?人民法院)", text)
    valid = [c for c in cands if not c.startswith(("请求", "向"))]
    for pref in ("高级", "中级", "基层"):
        for c in valid:
            if pref in c:
                return c
    return valid[0] if valid else ""


def _extract_doc_no(text: str) -> str:
    m = DOC_NO_RE.search(text)
    return m.group(0) if m else ""


def _extract_from_text(text: str, html_title: str = "", url: str = "", source_name: str = "") -> Case | None:
    """从单案例文本块提取 Case（供单页/多页页面复用）"""
    code_m = RULE_CODE_RE.search(text)
    if not code_m:
        return None
    rule_code = code_m.group(0)
    # 编号前为本案例标题区，编号后为正文区（法院/文书号/案情只从正文区提取，防止串案）
    body_text = text[code_m.end():]

    title = _extract_title(text, html_title, code_pos=code_m.start())
    keywords = _find_section(body_text, "关键词", _SECTION_ENDS["keywords"])
    facts = _find_section(body_text, "基本案情", _SECTION_ENDS["facts"])
    reasoning = _find_section(body_text, "裁判理由", _SECTION_ENDS["reasoning"])
    gist = _find_section(body_text, "裁判要旨", _SECTION_ENDS["gist"])
    result = ""
    for kw in ("裁判结果", "生效裁判", "判决结果"):
        result = _find_section(body_text, kw, _SECTION_ENDS["gist"])
        if result:
            break

    if not facts:
        facts = body_text[:600]
    if not gist:
        gist = body_text[-300:]

    court = _extract_court(body_text)
    doc_no = _extract_doc_no(body_text)
    scenario = _extract_scenario(title + body_text)
    province = _extract_province(body_text, court)
    amount = _extract_amount(facts)

    if len(facts) < 50:
        log.info("文本块过短，无法提取有效案情: %s", url)
        return None

    official_link = url if is_official_url(url) else ""
    if "典型案例" in (html_title + title):
        case_source = "最高院典型案例"
    elif "案例库" in (source_name + html_title) or "rmfyalk" in (url or ""):
        case_source = "人民法院案例库"
    else:
        case_source = "网络转载"

    return Case(
        rule_code=rule_code,
        title=title or f"案例{rule_code}",
        keywords=[k for k in keywords.split() if k][:10],
        court=court,
        doc_no=doc_no,
        province=province,
        scenario=scenario,
        amount=amount,
        facts=facts[:2000],
        reasoning=reasoning[:1000] if reasoning else "",
        gist=gist[:800] if gist else "",
        result=result[:500] if result else "",
        official_link=official_link,
        case_source=case_source,
        source_urls=[url] if url else [],
        source_names=[source_name] if source_name else ["网络转载"],
    )


def extract_case(html: str, url: str = "", source_name: str = "") -> Case | None:
    """从单个案例页面提取。返回 None 表示无法提取/不完整。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    html_title = (soup.title.get_text(strip=True) if soup.title else "") or ""
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", "", text)
    return _extract_from_text(text, html_title, url, source_name)


def extract_cases_multi(html: str, url: str = "", source_name: str = "") -> list[Case]:
    """从一页含多个案例的页面提取（如澎湃'一网一库专题'，按入库编号切块）

    单案例页面会退化为 1 个结果。
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    html_title = (soup.title.get_text(strip=True) if soup.title else "") or ""
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", "", text)

    positions = [m.start() for m in RULE_CODE_RE.finditer(text)]
    if not positions:
        return []
    if len(positions) == 1:
        case = _extract_from_text(text, html_title, url, source_name)
        return [case] if case else []

    cases = []
    for i, pos in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(text)
        # 编号前留标题区（案名在编号前），正文区自编号起，避免串案
        start = max(0, pos - 160)
        block = text[start:end]
        case = _extract_from_text(block, html_title, url, source_name)
        if case:
            cases.append(case)
    return cases
