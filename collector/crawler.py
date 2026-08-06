"""案例采集器：多源抓取 + 容错降级

采集策略（任一环节失败不阻塞整体）：
1. rmfyalk   —— 人民法院案例库官网（需登录态，多数环境不可用）
2. court_gov —— 最高人民法院官网涉农典型案例
3. search_web—— 搜索引擎检索"人民法院案例库 + 关键词"的转载页面，从中抽取入库编号
4. fallback  —— 内置种子案例（真实已核实案例），保证每日任务可产出
"""
import random
import re
import time

import requests
from bs4 import BeautifulSoup

from .fallback import SEED_CASES
from .models import Case
from .sources import RMFYALK_SEARCH, COURT_GOV_AGRICULTURE, SEARCH_ENGINES
from utils.logger import get_logger

log = get_logger("collector")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}

RULE_CODE_RE = re.compile(r"\d{4}-\d{2}-\d{1,2}-\d{3}-\d{3}")


def _safe_get(url: str, timeout: int = 15) -> str | None:
    try:
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
            amount="1651.42万元",
            facts=text[:600],
            reasoning="分配方案占用全体成员共有的集体收益，损害无地或少地成员权益，依法应予撤销。",
            gist="集体收益分配方案不得损害无地、少地成员的合法权益。",
            source_urls=[COURT_GOV_AGRICULTURE],
            source_names=["最高人民法院官网"],
        )
    ]


def _extract_codes(html: str) -> list[str]:
    """从页面文本抽取入库编号"""
    return list(dict.fromkeys(RULE_CODE_RE.findall(html)))


def fetch_search_web(keywords: list, max_pages: int = 3) -> list[Case]:
    """搜索引擎检索案例库转载页，抽取入库编号（不保证完整案情，仅作线索）"""
    found: list[Case] = []
    for kw in random.sample(keywords, min(len(keywords), max_pages)):
        for engine, tmpl in SEARCH_ENGINES.items():
            html = _safe_get(tmpl.format(q=requests.utils.quote(kw)))
            if not html:
                continue
            codes = _extract_codes(html)
            for code in codes[:5]:
                found.append(
                    Case(
                        rule_code=code,
                        title=f"线索案例（待补全）{code}",
                        keywords=[kw],
                        facts="线索来源：" + engine,
                        source_urls=[],
                        source_names=[engine],
                    )
                )
            time.sleep(1)
    return found


def collect(keywords: list, use_fallback: bool = True, max_cases: int = 20) -> list[Case]:
    """主采集入口：按源顺序尝试，汇总去重后返回"""
    all_cases: dict[str, Case] = {}

    for fetcher in (fetch_rmfyalk, fetch_court_gov, fetch_search_web):
        try:
            cases = fetcher(keywords) if fetcher is fetch_search_web else fetcher()
            for c in cases:
                if c.rule_code:
                    all_cases.setdefault(c.rule_code, c)
        except Exception as e:
            log.warning("采集源 %s 异常: %s", fetcher.__name__, e)

    log.info("网络采集到 %d 个案例", len(all_cases))

    if use_fallback:
        for seed in SEED_CASES:
            # 种子案例为人工核实过的干净数据，优先于网络线索（覆盖同编号）
            all_cases[seed["rule_code"]] = Case.from_dict(seed)
        log.info("合并内置种子案例，共 %d 个", len(all_cases))

    return list(all_cases.values())[:max_cases]
