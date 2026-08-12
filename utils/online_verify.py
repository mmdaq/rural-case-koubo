"""人民法院案例库官网在线核对（入库编号可查性验证）

用法：用登录 Token 调官网检索接口，按"案例编号"精确检索：
- 查得到  → 返回官方详情链接，标记 verified，允许推送；
- 查不到  → 判定不可查，拦截推送（防止编号杜撰/抄错）；
- Token 失效或网络异常 → 抛 OnlineVerifyUnavailable，由调用方决定策略。

Token 获取：浏览器登录 https://rmfyalk.court.gov.cn 后，F12 → 网络 →
发起一次检索，复制请求头 `faxin-cpws-al-token` 的值。
"""
import os
import time

import requests

from .logger import get_logger

log = get_logger("online_verify")

SEARCH_API = "https://rmfyalk.court.gov.cn/cpws_al_api/api/cpwsAl/search"
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


class OnlineVerifyUnavailable(Exception):
    """在线核对服务不可用（Token 失效/接口异常/网络异常）"""


def _search_body(rule_code: str) -> dict:
    """按案例编号精确检索的请求体"""
    return {
        "page": 1,
        "size": 10,
        "lib": "qb",
        "searchParams": {
            "userSearchType": 2,
            "isAdvSearch": "1",
            "selectValue": ["albh"],
            "lib": "cpwsAl_qb",
            "sort_field": "",
            "cpws_al_no": rule_code,
        },
    }


def verify_rule_code(rule_code: str, token: str, timeout: int = 20) -> dict:
    """核对单个入库编号。

    返回 {"found": bool, "official_no": str, "official_title": str, "official_url": str}。
    服务不可用（401/接口异常/网络失败）抛 OnlineVerifyUnavailable。
    """
    if not rule_code:
        return {"found": False, "official_no": "", "official_title": "", "official_url": ""}
    headers = dict(HEADERS)
    headers["faxin-cpws-al-token"] = token
    try:
        resp = requests.post(SEARCH_API, json=_search_body(rule_code), headers=headers, timeout=timeout)
        data = resp.json()
    except requests.RequestException as e:
        raise OnlineVerifyUnavailable(f"官网核对网络异常: {e}") from e
    except ValueError as e:
        raise OnlineVerifyUnavailable(f"官网核对响应解析失败: {e}") from e

    code = data.get("code")
    if code == 401:
        raise OnlineVerifyUnavailable(f"案例库 Token 无效或已过期: {data.get('msg')}")
    if code != 0:
        raise OnlineVerifyUnavailable(f"官网检索接口异常: code={code} msg={data.get('msg')}")

    payload = data.get("data") or {}
    total = int(payload.get("totalCount") or 0)
    datas = payload.get("datas") or []
    if total <= 0 or not datas:
        return {"found": False, "official_no": "", "official_title": "", "official_url": ""}

    first = datas[0]
    cid = first.get("cpws_al_id") or first.get("id") or ""
    return {
        "found": True,
        "official_no": first.get("cpws_al_no") or rule_code,
        "official_title": (first.get("title") or first.get("cpws_al_name") or "")[:80],
        "official_url": DETAIL_URL.format(cid) if cid else "",
    }


def verify_cases(cases: list, token: str, delay: float = 0.5, timeout: int = 20) -> tuple:
    """批量核对，返回 (passed_cases, rejected: list[dict], unavailable: bool)

    - passed_cases：查得到的案例（已回填 official_link / official_no / official_title）
    - rejected：查不到的案例（rule_code, title, 原因）
    - unavailable：服务整体不可用（Token 失效/接口异常/网络失败）时为 True
    """
    passed: list = []
    rejected: list = []
    unavailable = False
    for c in cases:
        code = c.get("rule_code", "")
        try:
            r = verify_rule_code(code, token, timeout=timeout)
        except OnlineVerifyUnavailable as e:
            log.error("在线核对服务不可用，本次核对中断: %s", e)
            unavailable = True
            break
        if r["found"]:
            c["official_verified"] = True
            c["official_verify_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            if r["official_url"]:
                c["official_link"] = r["official_url"]
            if r["official_no"]:
                c["official_no"] = r["official_no"]
            if r["official_title"]:
                c["official_title"] = r["official_title"]
            passed.append(c)
            log.info("官网核对通过: %s | %s", code, r["official_title"] or "")
        else:
            rejected.append({"rule_code": code, "title": c.get("title", ""), "reason": "官网案例库检索不到该入库编号"})
            log.warning("官网核对未通过，拦截: %s | %s", code, (c.get("title") or "")[:40])
        time.sleep(delay)
    return passed, rejected, unavailable


def get_token() -> str:
    """取案例库 Token（环境变量 RMFYALK_TOKEN 优先，其次 online_verify.token 配置由调用方注入）"""
    return os.getenv("RMFYALK_TOKEN", "").strip()
