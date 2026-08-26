#!/usr/bin/env python3
"""一次性重刷 extra_cases.json 中所有案例的 scenario 标签。

背景（2026-08-21 排查日报重复/文不对题 bug）：
- 旧分类器是"首条关键词命中即定场景"，泛词"青苗补偿"排在前面，导致
  村干部职务侵占案（2024-05-1-226-003）被误标为"承包方消亡继承"，
  生成出"老人去世征地款"开场 + 职务侵占案情的文不对题文案。
- extractor.classify_scenario 已改为评分制并补齐资金侵占类关键词。
- 本脚本用新分类器重刷扩展库全部案例的场景标签（种子案例 fallback.py
  为人工标注，不在处理范围）。

用法: python scripts/rescore_scenarios.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector.extractor import classify_scenario


def main():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    extra_path = os.path.join(base, "data", "extra_cases.json")

    with open(extra_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    changed = 0
    for code, rec in data.get("cases", {}).items():
        c = rec.get("case", {})
        text = " ".join([
            c.get("title", ""),
            c.get("facts", ""),
            c.get("gist", ""),
            c.get("reasoning", ""),
        ])
        new_scenario = classify_scenario(text)
        old_scenario = c.get("scenario", "")
        if new_scenario and new_scenario != old_scenario:
            print(f"{code}: {old_scenario or '(空)'} → {new_scenario} | {c.get('title', '')[:30]}")
            c["scenario"] = new_scenario
            changed += 1

    with open(extra_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n共更新 {changed} 条场景标签 → {extra_path}")


if __name__ == "__main__":
    main()
