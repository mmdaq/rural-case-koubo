"""核心编排：采集 → 核查 → 去重 → 生成 → 输出/发送

流程说明：
1. collect()       多源采集（官网/最高院/搜索/内置种子兜底）
2. verify          真伪核查（入库编号格式、字段完整性、内容要素、多源交叉）
3. dedup           持久化查重，过滤已推送案例
4. generate        每案例生成 1 篇口播文案（LLM 优先，模板兜底）
5. render/output   渲染 markdown，落盘 data/output
6. send            发邮件（dry_run 时跳过）
"""
import os
from datetime import datetime

from collector.crawler import collect
from collector.extrastore import ExtraStore, CrawlStore
from collector.models import Case
from generator.llm import generate_script
from generator.painpoints import enrich_case
from mailer.sender import send_email
from utils.dedup import SeenStore, title_hash
from utils.logger import get_logger
from utils.validator import verify_case

log = get_logger("pipeline")


def _select_candidates(
    cases: list,
    seen: SeenStore,
    count: int,
    cooldown_days: int = 7,
    min_gap_days: int = 1,
) -> list:
    """轮换选材，保证每日文案尽量独立不重复：

    优先级：
    1. 从未推送过的案例；
    2. 推送时间已超过冷却期（cooldown_days）的案例，最早推送的优先；
    3. 冷却期内但不在最近 min_gap_days 的案例，最早推送的优先；
    4. 兜底：案例池严重不足时，才复用最近推送过的案例。

    因此只要案例池充足，同一案例不会连续两天（甚至更久）重复出现。
    """
    unseen: list[dict] = []
    outside: list[tuple] = []
    inside: list[tuple] = []
    gap: list[tuple] = []
    for d in cases:
        pushed = seen.last_pushed_at(d.get("rule_code", ""), d.get("title", ""))
        if pushed is None:
            unseen.append(d)
            continue
        days = (datetime.now() - pushed).days
        if days >= cooldown_days:
            outside.append((pushed, d))
        elif days < min_gap_days:
            gap.append((pushed, d))
        else:
            inside.append((pushed, d))

    def by_oldest(bucket: list) -> list:
        return [x[1] for x in sorted(bucket, key=lambda x: x[0])]

    # 最近推送过的案例（gap）仅在基础池不足时才补位，且同一案例不会在一批内重复
    base = unseen + by_oldest(outside) + by_oldest(inside)
    ordered = base + by_oldest(gap)
    return ordered[:count]


def _load_config() -> dict:
    """加载 config.yaml + .env（轻量实现，避免依赖顺序问题）"""
    import os

    import yaml
    from dotenv import load_dotenv

    load_dotenv()
    base = os.path.dirname(os.path.abspath(__file__))
    cfg_path = os.path.join(base, "config.yaml")
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    # LLM key 优先读 .env
    if not cfg.get("llm", {}).get("api_key"):
        cfg.setdefault("llm", {})["api_key"] = os.getenv("LLM_API_KEY", "")
    if os.getenv("LLM_BASE_URL"):
        cfg["llm"]["base_url"] = os.getenv("LLM_BASE_URL")
    if os.getenv("LLM_MODEL"):
        cfg["llm"]["model"] = os.getenv("LLM_MODEL")
    return cfg


def run_pipeline(cfg: dict | None = None, dry_run: bool = False) -> dict:
    """执行一次完整流程，返回结果摘要"""
    cfg = cfg or _load_config()
    base = os.path.dirname(os.path.abspath(__file__))
    storage = cfg.get("storage", {})
    seen = SeenStore(os.path.join(base, storage.get("seen_file", "data/seen_cases.json")))
    extra = ExtraStore(os.path.join(base, storage.get("extra_file", "data/extra_cases.json")))
    crawled = CrawlStore(os.path.join(base, storage.get("crawled_file", "data/crawled_urls.json")))

    # 1. 采集（含自我扩充：搜索→提取→写扩展库）
    coll = cfg.get("collector", {})
    cases = collect(
        keywords=coll.get("keywords", []),
        extra=extra,
        crawled=crawled,
        use_fallback=coll.get("use_fallback", True),
        max_cases=int(coll.get("max_cases_per_day", 20)),
    )
    log.info("采集到案例 %d 个", len(cases))

    # 2. 真伪核查（严格：不满足"官方可查锚点"或内容残缺的一律丢弃，防止杜撰/无法核实的案例流出）
    verify_cfg = cfg.get("verify", {})
    min_src = int(verify_cfg.get("min_independent_sources", 1))
    strict = bool(verify_cfg.get("strict", True))
    require_anchor = bool(verify_cfg.get("require_official_anchor", True))
    valid: list[Case] = []
    for c in cases:
        v = verify_case(c.to_dict(), min_sources=min_src, require_official_anchor=require_anchor)
        # 内容残缺（无案情/无裁判要旨/案情过短）的线索案例直接丢弃，防止生成垃圾文案
        if not (c.facts or "").strip() or len(c.facts) < 50 or not (c.gist or "").strip():
            log.info("丢弃内容残缺案例 %s", c.rule_code)
            continue
        if not v["ok"]:
            if strict:
                log.warning("丢弃未通过核查案例 %s: %s", c.rule_code, v["issues"])
                continue
            log.warning("案例 %s 存在瑕疵（非严格模式保留）: %s", c.rule_code, v["issues"])
        valid.append(c)

    # 3. 选材：去重 + 冷却期轮换（未推送优先 → 冷却期外最早 → 冷却期内最早 → 兜底复用）
    gen_cfg = cfg.get("generator", {})
    count = int(gen_cfg.get("count_per_day", 5))
    cooldown_days = int(gen_cfg.get("cooldown_days", 7))
    min_gap_days = int(gen_cfg.get("min_gap_days", 1))
    all_dicts = [c.to_dict() for c in valid]
    candidates = _select_candidates(all_dicts, seen, count, cooldown_days, min_gap_days)
    if len(candidates) < count:
        log.warning("候选案例不足 %d 篇（案例池 %d 个），本轮仅推送 %d 篇",
                    count, len(all_dicts), len(candidates))

    # 4. 生成文案（每案例 1 篇）
    scripts = []
    for d in candidates[:count]:
        enriched = enrich_case(d)
        case = Case.from_dict(enriched)
        s = generate_script(case, cfg.get("llm", {}))
        s["case"] = enriched
        scripts.append(s)

    if not scripts:
        log.error("未生成任何文案，任务终止")
        return {"ok": False, "scripts": []}

    # 5. 渲染 markdown 并落盘
    today = datetime.now().strftime("%Y-%m-%d")
    out_dir = os.path.join(base, gen_cfg.get("output_dir", "data/output"))
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, f"口播文案_{today}.md")
    md_text = render_markdown(scripts, today)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_text)
    log.info("文案已写入 %s", md_path)

    # 6. 标记已推送（落盘成功即标记，防重）
    for s in scripts:
        seen.mark_seen(s["case"].get("rule_code", ""), s["case"].get("title", ""))

    # 7. 发送邮件
    sent = False
    if not dry_run:
        mail_cfg = cfg.get("mail", {})
        subject = f"{mail_cfg.get('subject_prefix', '')}{today} · 农村集体资产案例口播文案（{len(scripts)}篇）"
        body = md_text if len(md_text) < 40000 else md_text[:40000]
        attach = md_path if gen_cfg.get("attach_file", True) else ""
        sent = send_email(mail_cfg, subject, body, attach_path=attach)
    else:
        log.info("[dry-run] 跳过邮件发送")

    return {"ok": True, "sent": sent, "scripts": scripts, "md_path": md_path}


def render_markdown(scripts: list, date_str: str) -> str:
    """渲染每日文案 markdown（纯口播文案：编号/链接单独一行置于标题前，结尾留言引导）"""
    lines = [
        f"# 农村集体资产案例口播文案（{date_str}）",
        "",
        "> 案例参考来源：人民法院案例库（rmfyalk.court.gov.cn）+ 最高人民法院发布的典型案例。",
        "> 每个案例均标注可查的入库编号/官方链接，编号可在人民法院案例库检索核实，案例均为真实案件，非杜撰。",
        "",
    ]
    for i, s in enumerate(scripts, 1):
        c = s.get("case", {})
        ref_lines = [f"入库编号：{s['rule_code']}"]
        if c.get("official_link"):
            ref_lines.append(f"官方链接：{c['official_link']}")
        source_line = f"（案例：{c.get('title', '')}"
        if c.get("subtype"):
            source_line += f" | 细分：{c['subtype']}"
        srcs = c.get("source_names", []) or ["内置种子"]
        source_line += f" | 来源：{', '.join(srcs)}）"
        lines += [
            f"## 文案{i}",
            "",
            *ref_lines,
            f"标题：{s['title']}",
            f"正文：{s['body']}",
            "",
            f"评论区互动：{s['cta']}",
            "",
            source_line,
            "",
            "---",
            "",
        ]
    return "\n".join(lines)
