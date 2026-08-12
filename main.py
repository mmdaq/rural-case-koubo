#!/usr/bin/env python3
"""农村集体资产案例口播文案机器人 —— 入口

用法：
    python main.py run-once        立即执行一次完整流程（采集→核查→去重→生成→发送邮件）
    python main.py dry-run         立即执行但不发送邮件，文案落盘到 data/output/
    python main.py daemon          常驻进程，按 config.yaml 定时（默认每天 08:00）执行
    python main.py verify --file x.json   核查单个案例文件（真伪校验）
    python main.py verify-online [--file x.json]   到人民法院案例库官网核对入库编号（需 RMFYALK_TOKEN）
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import run_pipeline
from utils.logger import get_logger
from utils.validator import verify_case
from utils.online_verify import verify_rule_code, OnlineVerifyUnavailable

log = get_logger("main")


def cmd_verify(file_path: str):
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cases = data if isinstance(data, list) else [data]
    for c in cases:
        v = verify_case(c)
        status = "✓ 通过" if v["ok"] else "✗ 不合格"
        print(f"{c.get('rule_code', '无编号')} {c.get('title', '无标题')[:30]}  {status}")
        for issue in v["issues"]:
            print(f"    - {issue}")


def cmd_verify_online(file_path: str | None):
    """逐案到人民法院案例库官网核对入库编号（RMFYALK_TOKEN 必填）"""
    token = os.getenv("RMFYALK_TOKEN", "").strip()
    if not token:
        print("缺少 RMFYALK_TOKEN（案例库官网登录 Token），请先设置环境变量")
        sys.exit(2)

    if file_path:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cases = data if isinstance(data, list) else [data]
    else:
        from collector.fallback import SEED_CASES

        extra_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "extra_cases.json")
        cases = [dict(s) for s in SEED_CASES]
        if os.path.exists(extra_path):
            with open(extra_path, "r", encoding="utf-8") as f:
                extra = json.load(f)
            for rec in extra.get("cases", {}).values():
                cases.append(rec["case"])
        # 去重（同编号只留一个）
        seen_codes: set[str] = set()
        unique = []
        for c in cases:
            code = c.get("rule_code", "")
            if code and code in seen_codes:
                continue
            if code:
                seen_codes.add(code)
            unique.append(c)
        cases = unique

    print(f"共 {len(cases)} 个案例，开始官网核对…")
    failed = 0
    for c in cases:
        code = c.get("rule_code", "")
        try:
            r = verify_rule_code(code, token)
        except OnlineVerifyUnavailable as e:
            print(f"✗ {code} {c.get('title', '')[:30]}  服务不可用: {e}")
            sys.exit(2)
        if r["found"]:
            print(f"✓ {code} {c.get('title', '')[:30]}")
            print(f"    官网标题: {r['official_title']}")
            print(f"    官网链接: {r['official_url']}")
        else:
            failed += 1
            print(f"✗ {code} {c.get('title', '')[:30]}  官网检索不到")
    print(f"\n核对完成：{len(cases) - failed} 个可查，{failed} 个检索不到")
    sys.exit(1 if failed else 0)


def main():
    parser = argparse.ArgumentParser(description="农村集体资产案例口播文案机器人")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("run-once", help="立即执行一次完整流程（含发送邮件）")
    sub.add_parser("dry-run", help="执行流程但不发送邮件")
    sub.add_parser("daemon", help="常驻定时运行（默认每天 08:00）")

    pv = sub.add_parser("verify", help="核查案例文件")
    pv.add_argument("--file", required=True, help="案例 JSON 文件路径")

    pvo = sub.add_parser("verify-online", help="到人民法院案例库官网核对入库编号（需 RMFYALK_TOKEN）")
    pvo.add_argument("--file", default=None, help="案例 JSON 文件路径（缺省核对全部案例池）")

    args = parser.parse_args()

    if args.cmd == "verify":
        cmd_verify(args.file)
        return
    if args.cmd == "verify-online":
        cmd_verify_online(args.file)
        return

    if args.cmd == "daemon":
        from scheduler import start_daemon

        import yaml

        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml"), encoding="utf-8") as f:
            t = yaml.safe_load(f) or {}
        start_daemon(t.get("schedule", {}).get("time", "08:00"))
        return

    dry = args.cmd == "dry-run"
    result = run_pipeline(dry_run=dry)
    if result["ok"]:
        log.info(
            "任务完成：生成 %d 篇文案%s，输出：%s",
            len(result["scripts"]),
            "（已发送邮件）" if result.get("sent") else "（dry-run 未发送）",
            result.get("md_path"),
        )
        for s in result["scripts"]:
            print(f"  · {s['rule_code']} {s['title']}")
        # 关键：非 dry-run 且邮件未发送成功 → 非零退出，让 GitHub Actions 标红可见
        if not dry and not result.get("sent"):
            log.error("邮件发送失败，任务标记失败（检查 .env 的授权码/收件人/发件人）")
            sys.exit(2)
    else:
        log.error("任务失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
