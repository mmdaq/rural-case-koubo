"""案例真伪核查：入库编号格式 / 裁判文书号格式 / 字段完整性 / 官方可查锚点 / 多源交叉验证

"真实可查"判定（满足其一即可推送）：
1. 官方链接：source_urls / official_link 中含人民法院案例库、最高法官网或 *.gov.cn 官方域名；
2. 编号+文书号：入库编号格式合法 且 裁判文书号格式合法（两者均可在官方渠道检索核实）；
3. 编号+多源交叉：入库编号格式合法 且 至少 2 个独立域名转载来源内容一致。
"""
import re
from urllib.parse import urlparse

# 入库编号格式：YYYY-XX-X-XXX-XXX，如 2024-07-2-044-005
RULE_CODE_RE = re.compile(r"^\d{4}-\d{2}-\d{1,2}-\d{3}-\d{3}$")

# 官方可查域名：人民法院案例库 / 最高法官网 / 政府与法院系统官网
OFFICIAL_DOMAINS = (
    "rmfyalk.court.gov.cn",
    "court.gov.cn",
)

# 裁判文书号格式：如 （2019）闽07民终1227号 / (2020)苏0923民初2646号
DOC_NO_RE = re.compile(
    r"[（(]\s*\d{4}\s*[)）][\u4e00-\u9fa5A-Za-z]{0,12}\d{0,6}(?:民|行|刑)(?:初|终|再终|再提|再申|提|申|监|再)?字?\s*第?\s*\d+\s*号?"
)

# 案例必填字段
REQUIRED_FIELDS = ["rule_code", "title", "facts", "reasoning", "gist", "source_urls"]

# 内容要素（用于判断"像不像真案例"）
CONTENT_HINTS = ["人民法院", "判决", "征收", "补偿", "集体经济组织", "成员资格"]

# ---------------- 农村集体资产主题过滤 ----------------
# 防止"案例库真实但主题跑题"的案例混入（如刑事、劳动、环保、行政案件被误当农村集体资产案例）。
# 判定优先级：标题命中（最严格）→ 正文强主题词+辅助词组合（兜底）。

# 标题命中即放行：案件类型/客体直接指向农村集体资产
TITLE_TOPIC_HINTS = [
    "侵害集体经济组织成员权益",
    "土地承包经营权",
    "农村土地承包",
    "集体经济组织",
    "集体资产",
    "集体收益",
    "征地补偿",
    "征收补偿",
    "宅基地",
    "集体土地",
    "成员资格",
    "村集体",
    "承包地",
]

# 标题命中即拒绝：已知的跑题类别（防止"村民委员会"等字眼误放行非资产类纠纷）
NEGATIVE_TITLE_HINTS = [
    "假冒注册商标",
    "开设赌场",
    "侵犯公民个人信息",
    "确认劳动关系",
    "生态环境保护民事公益诉讼",
    "公安行政登记",
    "客运人力三轮车经营权",
    "违反安全保障义务",
    "机动车交通事故",
    "民间借贷",
    "买卖合同",
    "婚姻家庭纠纷",
    "劳动争议",
    "侵犯著作权",
]

# 正文强主题词：命中任一即与农村集体资产强相关
STRONG_TOPIC_HINTS = [
    "集体经济组织成员",
    "集体经济组织",
    "集体收益",
    "集体资产",
    "征地补偿",
    "征收补偿",
    "土地补偿款",
    "土地补偿费",
    "安置补助",
    "土地征收",
    "外嫁女",
    "成员资格",
    "承包经营权",
    "承包地",
    "承包合同",
    "村规民约",
    "收益分配",
    "股权证",
    "青苗补偿",
    "入赘",
    "宅基地",
    "责任田",
    "村民小组",
    "村委会",
    "农村集体产权",
]

# 正文辅助主题词（必须与强主题词组合使用，避免"分红/征地/经营权"等泛词误判）
AUX_TOPIC_HINTS = [
    "分红",
    "征地",
    "征收",
    "承包",
    "补偿",
    "村民",
    "农村",
    "集体",
    "经营权",
    "民主议定",
    "集体经营性建设用地",
    "成员权益",
    "集体经济",
    "承包金",
    "入市",
]


def check_rule_code(rule_code: str) -> bool:
    """入库编号格式校验"""
    return bool(RULE_CODE_RE.match(rule_code or ""))


def is_rural_collective_theme(case: dict) -> bool:
    """判断案例是否属于"农村集体资产"主题（标题优先，正文强相关兜底）。"""
    title = (case.get("title") or "").strip()
    if any(n in title for n in NEGATIVE_TITLE_HINTS):
        return False
    if any(k in title for k in TITLE_TOPIC_HINTS):
        return True
    blob = "".join(str(case.get(k, "")) for k in ("title", "facts", "gist"))
    strong = [k for k in STRONG_TOPIC_HINTS if k in blob]
    if not strong:
        return False
    aux = [k for k in AUX_TOPIC_HINTS if k in blob]
    return len(strong) + len(aux) >= 3


def check_doc_no(doc_no: str) -> bool:
    """裁判文书号格式校验（允许为空，空则跳过）"""
    if not doc_no:
        return True
    return bool(DOC_NO_RE.search(doc_no))


def missing_fields(case: dict, skip: tuple = ()) -> list:
    """返回缺失的必填字段名列表"""
    missing = []
    for f in REQUIRED_FIELDS:
        if f in skip:
            continue
        v = case.get(f)
        if isinstance(v, str):
            if not v.strip():
                missing.append(f)
        elif not v:  # None / 空 list 等
            missing.append(f)
    return missing


def content_plausible(case: dict, official_anchor_ok: bool = False) -> bool:
    """正文是否包含案例要素关键词（粗筛，防无关内容混入）。

    已具备官方可查锚点（入库编号+裁判文书号/官方链接）的真实案例，
    只需 1 个要素关键词即可放行，避免误杀提取不完整的真实案例。
    """
    blob = "".join(
        str(case.get(k, "")) for k in ("facts", "reasoning", "gist", "title")
    )
    hits = sum(h in blob for h in CONTENT_HINTS)
    if hits >= 2:
        return True
    return official_anchor_ok and hits >= 1


def domain(url: str) -> str:
    return urlparse(url).netloc or ""


def is_official_url(url: str) -> bool:
    """是否官方可查链接（案例库/最高法官网/政府法院系统官网）"""
    d = domain(url).lower()
    if not d:
        return False
    if d in OFFICIAL_DOMAINS or d.endswith(".court.gov.cn"):
        return True
    # 政府/法院系统域名（*.gov.cn）视为官方渠道
    return d.endswith(".gov.cn")


def independent_sources(case: dict) -> int:
    """同一案例出现在几个独立域名来源（用于交叉验证真伪）"""
    return len({domain(u) for u in case.get("source_urls", []) if domain(u)})


def official_anchor(case: dict) -> tuple[bool, str]:
    """返回 (是否具备官方可查锚点, 锚点类型说明)"""
    urls = list(case.get("source_urls") or [])
    if case.get("official_link"):
        urls.append(case["official_link"])
    if any(is_official_url(u) for u in urls):
        return True, "官方链接"

    code_ok = check_rule_code(case.get("rule_code", ""))
    doc_no = (case.get("doc_no") or "").strip()
    if code_ok and doc_no and check_doc_no(doc_no):
        return True, "入库编号+裁判文书号"

    if code_ok and independent_sources(case) >= 2:
        return True, "入库编号+多源交叉"

    return False, ""


def verify_case(case: dict, min_sources: int = 1, require_official_anchor: bool = True) -> dict:
    """综合核查，返回 {ok, issues}"""
    issues = []

    code_ok = check_rule_code(case.get("rule_code", ""))
    has_anchor, anchor_kind = official_anchor(case)
    if not code_ok and not (case.get("official_link") or "").strip():
        issues.append(f"入库编号格式非法: {case.get('rule_code')}")
    if not check_doc_no(case.get("doc_no", "")):
        issues.append(f"裁判文书号格式非法: {case.get('doc_no')}")

    # 有官方链接可查时，允许无入库编号（如最高院典型案例仅给官方链接）
    has_official_link = bool((case.get("official_link") or "").strip())
    skip = ("rule_code",) if not code_ok and has_official_link else ()
    missing = missing_fields(case, skip=skip)
    if missing:
        issues.append(f"缺少必填字段: {missing}")
    if not content_plausible(case, official_anchor_ok=has_anchor):
        issues.append("正文缺少案例要素关键词，疑似非案例内容")

    n_src = independent_sources(case)
    if n_src < min_sources:
        issues.append(f"独立来源仅 {n_src} 个，低于阈值 {min_sources}")

    if require_official_anchor:
        if not has_anchor:
            issues.append(
                "缺少官方可查锚点：需提供官方链接（案例库/最高院/政府法院官网），"
                "或 入库编号+裁判文书号，或 ≥2 个独立域名交叉来源"
            )

    return {"ok": not issues, "issues": issues, "independent_sources": n_src}
