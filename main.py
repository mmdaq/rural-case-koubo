#!/usr/bin/env python3
"""农村集体资产案例口播文案机器人 —— 入口

用法：
    python main.py run-once        立即执行一次完整流程（采集→核查→去重→生成→发送邮件）
    python main.py dry-run         立即执行但不发送邮件，文案落盘到 data/output/
    python main.py daemon          常驻进程，按 config.yaml 定时（默认每天 08:00）执行
    python main.py verify --file x.json   核查单个案例文件（真伪校验）
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import run_pipeline
from scheduler import start_daemon
from utils.logger import get_logger
from utils.validator import verify_case

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


def main():
    parser = argparse.ArgumentParser(description="农村集体资产案例口播文案机器人")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("run-once", help="立即执行一次完整流程（含发送邮件）")
    sub.add_parser("dry-run", help="执行流程但不发送邮件")
    sub.add_parser("daemon", help="常驻定时运行（默认每天 08:00）")

    pv = sub.add_parser("verify", help="核查案例文件")
    pv.add_argument("--file", required=True, help="案例 JSON 文件路径")

    args = parser.parse_args()

    if args.cmd == "verify":
        cmd_verify(args.file)
        return

    if args.cmd == "daemon":
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
    else:
        log.error("任务失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
