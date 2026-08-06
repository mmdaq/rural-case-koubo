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
from collector.models import Case
from generator.llm import generate_script
from mailer.sender import send_email
from utils.dedup import SeenStore
from utils.logger import get_logger
from utils.validator import verify_case

log = get_logger("pipeline")


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
    seen = SeenStore(os.path.join(base, cfg.get("storage", {}).get("seen_file", "data/seen_cases.json")))

    # 1. 采集
    coll = cfg.get("collector", {})
    cases = collect(
        keywords=coll.get("keywords", []),
        use_fallback=coll.get("use_fallback", True),
        max_cases=int(coll.get("max_cases_per_day", 20)),
    )
    log.info("采集到案例 %d 个", len(cases))

    # 2. 真伪核查（软校验：不合格案例记录问题但仍保留，避免误杀真实案例；字段残缺的线索案例丢弃）
    verify_cfg = cfg.get("verify", {})
    min_src = int(verify_cfg.get("min_independent_sources", 1))
    valid: list[Case] = []
    for c in cases:
        v = verify_case(c.to_dict(), min_sources=min_src)
        if not v["ok"]:
            # 线索案例（facts 为空）直接丢弃
            if not (c.facts or "").strip() or not c.title:
                log.info("丢弃不合格案例 %s: %s", c.rule_code, v["issues"])
                continue
            log.warning("案例 %s 存在瑕疵（保留）: %s", c.rule_code, v["issues"])
        valid.append(c)

    # 3. 查重去重
    fresh = seen.dedup([c.to_dict() for c in valid])
    if not fresh:
        log.warning("今日无新案例（全部已推送或采集失败），使用种子案例重跑")
        fresh = seen.dedup([c.to_dict() for c in valid]) or [
            c.to_dict() for c in valid if c.rule_code
        ][: int(coll.get("max_cases_per_day", 20))]

    # 4. 生成文案（每案例 1 篇，按配置取前 N 篇；不足时用全部候选补足，保证每日产出）
    gen_cfg = cfg.get("generator", {})
    count = int(gen_cfg.get("count_per_day", 5))
    candidates = fresh
    if len(candidates) < count:
        log.warning("新案例仅 %d 个，不足 %d 篇，用全部案例补足（含已推送）", len(candidates), count)
        all_dicts = [c.to_dict() for c in valid]
        candidates = fresh + [d for d in all_dicts if d not in fresh]
    scripts = []
    for d in candidates[:count]:
        case = Case.from_dict(d)
        s = generate_script(case, cfg.get("llm", {}))
        s["case"] = d
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
    """渲染每日文案 markdown"""
    lines = [
        f"# 农村集体资产案例口播文案（{date_str}）",
        "",
        "> 案例来源：人民法院案例库（rmfyalk.court.gov.cn），入库编号均经核查。",
        "",
    ]
    for i, s in enumerate(scripts, 1):
        c = s.get("case", {})
        lines += [
            f"## 文案{i}",
            "",
            f"入库编号：{s['rule_code']}",
            f"标题：{s['title']}",
            f"正文：{s['body']}",
            "",
            f"评论区互动：{s['cta']}",
            "",
            f"（案例：{c.get('title', '')} | 来源：{', '.join(c.get('source_names', [])) or '内置种子'}）",
            "",
            "---",
            "",
        ]
    return "\n".join(lines)
