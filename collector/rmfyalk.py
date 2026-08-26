"""人民法院案例库官方检索源（https://rmfyalk.court.gov.cn）

官网数据权威且结构完整（入库编号/基本案情/裁判理由/裁判要旨齐全），
是最优质的案例来源。接口需要登录 Token：

Token 获取：浏览器登录案例库 → F12 → 网络 → 发起一次检索 →
复制请求头 `faxin-cpws-al-token` 的值 → 配置到环境变量 RMFYALK_TOKEN
（本地 .env / GitHub Secrets）。Token 失效时本源自动跳过（不影响其他源）。

说明：官网登录使用阿里云滑块验证码 + RSA 加密，无法可靠地自动登录，
因此采用"人工登录一次、长期复用 Token"的方式。
"""
import os
import time

import requests

from .models import Case
from utils.logger import get_logger

log = get_logger("rmfyalk")

SEARCH_API = "https://rmfyalk.court.gov.cn/cpws_al_api/api/cpwsAl/search"
CONTENT_API = "https://rmfyalk.court.gov.cn/cpws_al_api/api/cpwsAl/content"
DETAIL_URL = "https://rmfyalk.court.gov.cn/view/content.html?id={}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Content-Type": "application/json;charset=UTF-8",
    "Referer": "https://rmfyalk.court.gov.cn/view/list.html",
    "Origin": "https://rmfyalk.court.gov.cn",
}


class RmfyalkUnavailable(Exception):
    """官网服务不可用（Token 失效/接口异常/网络失败）"""


# 最近一次不可用的原因（供 pipeline 判断是否需要在邮件中提醒刷新 Token）。
# 空字符串 = 本轮官库采集正常或未触发。
LAST_UNAVAILABLE_REASON = ""


def get_token() -> str:
    return os.getenv("RMFYALK_TOKEN", "").strip()


def _auth_headers(token: str) -> dict:
    headers = dict(HEADERS)
    headers["faxin-cpws-al-token"] = token
    return headers


def _search(keyword: str, token: str, page: int = 1, size: int = 20,
            timeout: int = 20) -> dict:
    """全文检索（与官网前端一致：selectValue=qw + keyTitle=关键词）"""
    body = {
        "page": page,
        "size": size,
        "lib": "qb",
        "searchParams": {
            "userSearchType": 1,
            "isAdvSearch": "0",
            "selectValue": ["qw"],
            "keyTitle": [keyword],
            "lib": "cpwsAl_qb",
            "sort_field": "",
        },
    }
    try:
        resp = requests.post(SEARCH_API, json=body, headers=_auth_headers(token), timeout=timeout)
        data = resp.json()
    except requests.RequestException as e:
        raise RmfyalkUnavailable(f"检索接口网络异常: {e}") from e
    if data.get("code") == 401:
        raise RmfyalkUnavailable(f"案例库 Token 无效或已过期: {data.get('msg')}")
    if data.get("code") != 0:
        raise RmfyalkUnavailable(f"检索接口异常: code={data.get('code')} msg={data.get('msg')}")
    return data.get("data") or {}


def _content(cpws_al_id: str, token: str, timeout: int = 20) -> dict:
    """取案例正文（基本案情 jbaq / 裁判理由 cply / 裁判要旨 cpyz 等）

    与官网前端一致：POST {gid: <搜索返回的已编码id>}，不要二次编码
    """
    try:
        resp = requests.post(
            CONTENT_API, json={"gid": cpws_al_id},
            headers=_auth_headers(token), timeout=timeout,
        )
        data = resp.json()
    except requests.RequestException as e:
        raise RmfyalkUnavailable(f"详情接口网络异常: {e}") from e
    if data.get("code") == 401:
        raise RmfyalkUnavailable(f"案例库 Token 无效或已过期: {data.get('msg')}")
    if data.get("code") != 0:
        raise RmfyalkUnavailable(f"详情接口异常: code={data.get('code')} msg={data.get('msg')}")
    outer = data.get("data") or {}
    # 官网返回结构：{code, msg, data: {isCanBrowse, data: {案例正文字段...}}}
    if isinstance(outer, dict) and isinstance(outer.get("data"), dict):
        return outer["data"]
    return outer


def _pick(d: dict, *keys: str) -> str:
    """按候选键名取第一个非空字符串字段"""
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _clean(text: str) -> str:
    """去掉检索高亮 <em> 标签与 HTML 标记，保留正文"""
    import re
    text = re.sub(r"</?(?:em|span|b|p)[^>]*>", "", text or "")
    return text.strip()


def _strip_html(text: str) -> str:
    """详情接口返回的正文带 <p> 段落标签，转纯文本"""
    import re
    text = re.sub(r"<[^>]+>", "\n", text or "")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "".join(lines) if lines else ""


def harvest_rmfyalk(keywords: list[str], crawled=None, max_fetch: int = 60,
                    pages_per_keyword: int = 2, token: str | None = None,
                    title_filter=None, delay: float = 0.6) -> list[Case]:
    """按主题关键词检索官方案例库，返回结构化案例列表。

    - keywords：已按日轮换好的关键词子集；
    - crawled：可选，跳过已入库过的官方链接；
    - title_filter：可选，标题预过滤函数（复用转载源的涉农标题闸门）；
    - Token 失效抛 RmfyalkUnavailable，由调用方决定降级策略。
    """
    token = (token or get_token()).strip()
    if not token:
        log.info("未配置 RMFYALK_TOKEN，跳过官方案例库源")
        return []
    global LAST_UNAVAILABLE_REASON
    LAST_UNAVAILABLE_REASON = ""
    found: list[Case] = []
    fetched = 0
    seen_ids: set[str] = set()
    try:
        for kw in keywords:
            if fetched >= max_fetch:
                break
            total_items = 0
            for page in range(1, pages_per_keyword + 1):
                payload = _search(kw, token, page=page)
                datas = payload.get("datas") or []
                total_items += len(datas)
                if not datas:
                    break
                for item in datas:
                    if fetched >= max_fetch:
                        break
                    # 注意：搜索返回的 id 已是 URL 编码形态，原样使用
                    cid = _pick(item, "cpws_al_id", "id")
                    code = _pick(item, "cpws_al_no")
                    title = _clean(_pick(item, "cpws_al_title", "title", "cpws_al_name"))
                    if not cid or cid in seen_ids:
                        continue
                    if title_filter and title and not title_filter(title):
                        continue
                    detail_url = DETAIL_URL.format(cid)
                    if crawled and crawled.is_crawled(detail_url):
                        continue
                    seen_ids.add(cid)

                    d = _content(cid, token)
                    time.sleep(delay)
                    facts = _strip_html(_pick(d, "cpws_al_jbaq", "jbaq"))
                    gist = _strip_html(_pick(d, "cpws_al_cpyz", "cpyz") or _pick(item, "cpws_al_cpyz"))
                    reasoning = _strip_html(_pick(d, "cpws_al_cply", "cply"))
                    result = _strip_html(_pick(d, "cpws_al_cpjg", "cpjg"))
                    if not code:
                        code = _pick(d, "cpws_al_no")
                    if not title:
                        title = _clean(_pick(d, "cpws_al_title", "title", "cpws_al_name"))
                    case = Case(
                        rule_code=code,
                        title=title,
                        keywords=[],
                        court=_pick(item, "cpws_al_slfy_name"),
                        doc_no=_pick(item, "cpws_al_ajzh"),
                        province=_pick(item, "cpws_al_slfy_sf_name"),
                        facts=facts or result[:600],
                        gist=gist,
                        reasoning=reasoning,
                        result=result,
                        source_urls=[detail_url],
                        official_link=detail_url,
                        case_source="人民法院案例库",
                        source_names=["人民法院案例库"],
                    )
                    found.append(case)
                    fetched += 1
                    if crawled:
                        crawled.mark_crawled(detail_url)
                    log.info("官方库提取成功: %s | %s", code, (title or "")[:40])
                time.sleep(delay)
            log.info("官方库关键词【%s】命中 %d 条（前 %d 页）", kw, total_items, pages_per_keyword)
    except RmfyalkUnavailable as e:
        # Token 失效等：保留已收获部分，记录原因供 pipeline 提醒刷新
        LAST_UNAVAILABLE_REASON = str(e)
        log.warning("官方案例库不可用，本源提前收工: %s", e)
    return found
