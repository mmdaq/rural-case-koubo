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
from utils.state import RunState
from utils.validator import verify_case, is_rural_collective_theme

log = get_logger("pipeline")


def _select_candidates(
    cases: list,
    seen: SeenStore,
    count: int,
    cooldown_days: int = 7,
    min_gap_days: int = 1,
) -> list:
    """选材：**仅选从未推送过的案例**。

    不复用已推送案例。如果所有案例均已推送过，返回空列表，
    由调用方（run_pipeline）决定是发送通知还是停止推送。

    同一批次内不会重复选取同一案例（cases 已由 collect() 按 rule_code 去重）。
    """
    unseen: list[dict] = []
    for d in cases:
        pushed = seen.last_pushed_at(d.get("rule_code", ""), d.get("title", ""))
        if pushed is None:
            unseen.append(d)

    if not unseen:
        log.info("所有 %d 个候选案例均已推送过，无新案例可选", len(cases))
        return []

    log.info("选材：从 %d 个候选中选取 %d 个未推送案例", len(unseen), min(count, len(unseen)))
    return unseen[:count]


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
        # 主题闸门（兜底）：只允许农村集体资产案例进入生成，防止跑题文案外发
        if not is_rural_collective_theme(c.to_dict()):
            log.info("丢弃非农村集体资产主题案例 %s | %s", c.rule_code, (c.title or "")[:30])
            continue
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

    # 2.5 官网在线核对：入库编号必须在人民法院案例库官网可查到，查不到一律拦截
    token = os.getenv("RMFYALK_TOKEN", "").strip()
    online_cfg = verify_cfg.get("online", {}) or {}
    if token:
        from utils.online_verify import verify_cases

        valid_dicts = [c.to_dict() for c in valid]
        passed_dicts, rejected, unavailable = verify_cases(
            valid_dicts,
            token,
            timeout=int(online_cfg.get("timeout", 20)),
        )
        require_online = bool(online_cfg.get("require", True))
        if unavailable:
            if require_online:
                log.error(
                    "官网在线核对不可用且配置要求强制核对（verify.online.require=true），"
                    "本轮终止，不发送未经官网核对的文案"
                )
                return {"ok": False, "scripts": [], "reason": "online_verify_unavailable"}
            log.warning("官网在线核对不可用，降级为锚点校验（存在编号不可查风险），请检查 RMFYALK_TOKEN")
        else:
            for r in rejected:
                log.info("拦截未通过官网核对的案例: %s | %s", r["rule_code"], r["title"])
            log.info("官网核对通过 %d 个，拦截 %d 个", len(passed_dicts), len(rejected))
            valid = [Case.from_dict(d) for d in passed_dicts]
    else:
        log.warning("未配置 RMFYALK_TOKEN，本轮跳过官网在线核对，仅使用本地锚点校验")

    # 3. 选材：仅选从未推送过的案例（不复用已推送案例）
    gen_cfg = cfg.get("generator", {})
    count = int(gen_cfg.get("count_per_day", 5))
    cooldown_days = int(gen_cfg.get("cooldown_days", 7))
    min_gap_days = int(gen_cfg.get("min_gap_days", 1))
    out_dir = os.path.join(base, gen_cfg.get("output_dir", "data/output"))
    os.makedirs(out_dir, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    all_dicts = [c.to_dict() for c in valid]
    candidates = _select_candidates(all_dicts, seen, count, cooldown_days, min_gap_days)

    # ── 无新案例：通知 / 停止逻辑 ──
    if not candidates:
        state_path = os.path.join(base, storage.get("state_file", "data/run_state.json"))
        state = RunState(state_path)

        if state.stopped:
            log.info("系统已处于停止状态（连续 %d 天无新增），跳过本次推送",
                     state.consecutive_no_new)
            return {"ok": True, "scripts": [], "reason": "stopped", "consecutive": state.consecutive_no_new}

        # 检查是否同一天内重复触发（防止同一天多次运行导致计数暴涨）
        last_run_date = (state.data.get("last_run") or "")[:10]
        if last_run_date == today:
            log.info("今天已记录过无新增，跳过重复通知")
            return {"ok": True, "scripts": [], "reason": "already_notified_today", "consecutive": state.consecutive_no_new}

        state.record_no_new()
        notify_cfg = cfg.get("notify", {})
        max_notify_days = int(notify_cfg.get("max_notify_days", 3))

        if state.consecutive_no_new > max_notify_days:
            state.stop()
            log.warning("连续 %d 天无新增案例（超过阈值 %d），系统停止推送。"
                        "如需恢复，请检查采集源配置或关键词后删除 data/run_state.json 中的 stopped 标记。",
                        state.consecutive_no_new, max_notify_days)
            return {"ok": True, "scripts": [], "reason": "stopped", "consecutive": state.consecutive_no_new}

        # 发送通知邮件
        notification_md = render_notification(state.consecutive_no_new, today)
        notif_path = os.path.join(out_dir, f"通知_{today}.md") if dry_run else None
        if notif_path:
            with open(notif_path, "w", encoding="utf-8") as f:
                f.write(notification_md)

        sent = False
        if not dry_run:
            mail_cfg = cfg.get("mail", {})
            subject = f"{mail_cfg.get('subject_prefix', '')}{today} · 无新增案例通知（连续{state.consecutive_no_new}天）"
            body = notification_md if len(notification_md) < 40000 else notification_md[:40000]
            sent = send_email(mail_cfg, subject, body, attach_path="")
        else:
            log.info("[dry-run] 跳过通知邮件发送")

        log.info("已发送无新增通知（连续 %d 天），案例池 %d 个全部已推送",
                 state.consecutive_no_new, len(all_dicts))
        return {"ok": True, "sent": sent, "scripts": [], "reason": "no_new_cases_notification",
                "consecutive": state.consecutive_no_new}

    # ── 有新案例：重置状态计数器 ──
    state_path = os.path.join(base, storage.get("state_file", "data/run_state.json"))
    state = RunState(state_path)
    if state.consecutive_no_new > 0:
        state.reset()

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


def render_notification(consecutive: int, date_str: str) -> str:
    """渲染无新增案例通知 markdown"""
    lines = [
        f"# ⚠️ 无新增案例通知（{date_str}）",
        "",
        f"**这是连续第 {consecutive} 天未发现新的农村集体资产案例。**",
        "",
        "## 当前状态",
        "",
        "- 案例池中所有案例均已推送过",
        "- 采集源（预置链接 / 转载源翻页 / 搜索引擎）未发现新的可采集页面",
        "- 系统不会重复推送已推送过的案例",
        "",
        "## 建议操作",
        "",
        "1. **检查关键词**：`config.yaml` → `collector.keywords`，是否需要调整检索词？",
        "2. **扩充采集源**：`collector.crawler.py` → `FEED_SOURCES` / `SEED_LINKS`，添加新的转载栏目或案例库页面",
        "3. **检查网络环境**：采集依赖外部网站可达性，某些站点可能需要登录或变更 URL",
        "4. **手动添加种子案例**：在 `collector/fallback.py` → `SEED_CASES` 中补充人工核实的真实案例",
        "",
        f"---",
        "",
        f"📌 连续 {consecutive} 天无新增后系统将自动停止推送。恢复方式：修复采集源后删除 `data/run_state.json` 中的 `stopped` 标记即可。",
        "",
    ]
    return "\n".join(lines)


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
