"""案例真伪核查：入库编号格式 / 裁判文书号格式 / 字段完整性 / 多源交叉验证"""
import re
from urllib.parse import urlparse

# 入库编号格式：YYYY-XX-X-XXX-XXX，如 2024-07-2-044-005
RULE_CODE_RE = re.compile(r"^\d{4}-\d{2}-\d{1,2}-\d{3}-\d{3}$")

# 裁判文书号格式：如 （2019）闽07民终1227号 / (2020)苏0923民初2646号
DOC_NO_RE = re.compile(
    r"[（(]\s*\d{4}\s*[)）][\u4e00-\u9fa5A-Za-z]{0,12}\d{0,6}(?:民|行|刑)(?:初|终|再终|再提|再申|提|申|监|再)?字?\s*第?\s*\d+\s*号?"
)

# 案例必填字段
REQUIRED_FIELDS = ["rule_code", "title", "facts", "reasoning", "gist", "source_urls"]

# 内容要素（用于判断"像不像真案例"）
CONTENT_HINTS = ["人民法院", "判决", "征收", "补偿", "集体经济组织", "成员资格"]


def check_rule_code(rule_code: str) -> bool:
    """入库编号格式校验"""
    return bool(RULE_CODE_RE.match(rule_code or ""))


def check_doc_no(doc_no: str) -> bool:
    """裁判文书号格式校验（允许为空，空则跳过）"""
    if not doc_no:
        return True
    return bool(DOC_NO_RE.search(doc_no))


def missing_fields(case: dict) -> list:
    """返回缺失的必填字段名列表"""
    missing = []
    for f in REQUIRED_FIELDS:
        v = case.get(f)
        if isinstance(v, str):
            if not v.strip():
                missing.append(f)
        elif not v:  # None / 空 list 等
            missing.append(f)
    return missing


def content_plausible(case: dict) -> bool:
    """正文是否包含案例要素关键词（粗筛，防无关内容混入）"""
    blob = "".join(
        str(case.get(k, "")) for k in ("facts", "reasoning", "gist", "title")
    )
    return sum(h in blob for h in CONTENT_HINTS) >= 2


def domain(url: str) -> str:
    return urlparse(url).netloc or ""


def independent_sources(case: dict) -> int:
    """同一案例出现在几个独立域名来源（用于交叉验证真伪）"""
    return len({domain(u) for u in case.get("source_urls", []) if domain(u)})


def verify_case(case: dict, min_sources: int = 1) -> dict:
    """综合核查，返回 {ok, issues}"""
    issues = []

    if not check_rule_code(case.get("rule_code", "")):
        issues.append(f"入库编号格式非法: {case.get('rule_code')}")
    if not check_doc_no(case.get("doc_no", "")):
        issues.append(f"裁判文书号格式非法: {case.get('doc_no')}")

    missing = missing_fields(case)
    if missing:
        issues.append(f"缺少必填字段: {missing}")
    if not content_plausible(case):
        issues.append("正文缺少案例要素关键词，疑似非案例内容")

    n_src = independent_sources(case)
    if n_src < min_sources:
        issues.append(f"独立来源仅 {n_src} 个，低于阈值 {min_sources}")

    return {"ok": not issues, "issues": issues, "independent_sources": n_src}
