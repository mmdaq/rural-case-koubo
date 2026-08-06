"""案例提取器：从转载页全文提取结构化案例字段

支持两种页面形态：
- 模式A（案例库原文转载站，如税递网）：含"入库编号/关键词/基本案情/裁判理由/裁判要旨"分节
- 模式B（法院官网/新闻稿）：自然叙事文本，仅能提取编号+正文，置信度较低

提取结果必须通过 utils.validator 校验（编号格式、内容要素、字段完整性）才可入库。
"""
import re

from .models import Case
from utils.logger import get_logger
from utils.validator import DOC_NO_RE

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
    ("征地补偿", ["征地补偿", "征收补偿", "安置补助", "土地征收"]),
    ("承包地纠纷", ["土地承包经营权合同", "承包经营权合同纠纷", "承包地", "土地承包"]),
    ("分配方案", ["收益分配方案", "分配方案", "入市"]),
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


def _extract_title(text: str, html_title: str = "") -> str:
    """案名：优先编号后紧跟的案名，其次'xxx诉xxx案'，页面标题兜底"""
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
    m = re.search(r"([\u4e00-\u9fa5]{2,20}?(?:高级|中级|基层)?人民法院)", text)
    return m.group(1) if m else ""


def _extract_doc_no(text: str) -> str:
    m = DOC_NO_RE.search(text)
    return m.group(0) if m else ""


def _extract_from_text(text: str, html_title: str = "", url: str = "", source_name: str = "") -> Case | None:
    """从单案例文本块提取 Case（供单页/多页页面复用）"""
    code_m = RULE_CODE_RE.search(text)
    if not code_m:
        return None
    rule_code = code_m.group(0)

    title = _extract_title(text, html_title)
    keywords = _find_section(text, "关键词", _SECTION_ENDS["keywords"])
    facts = _find_section(text, "基本案情", _SECTION_ENDS["facts"])
    reasoning = _find_section(text, "裁判理由", _SECTION_ENDS["reasoning"])
    gist = _find_section(text, "裁判要旨", _SECTION_ENDS["gist"])

    if not facts:
        facts = text[:600]
    if not gist:
        gist = text[-300:]

    court = _extract_court(text)
    doc_no = _extract_doc_no(text)
    scenario = _extract_scenario(text)
    province = _extract_province(text, court)
    amount = _extract_amount(facts)

    if len(facts) < 50:
        log.info("文本块过短，无法提取有效案情: %s", url)
        return None

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
        start = max(0, pos - 120)  # 编号前留足空间以包含案名
        block = text[start:end]
        case = _extract_from_text(block, html_title, url, source_name)
        if case:
            cases.append(case)
    return cases
