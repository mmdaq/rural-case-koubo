#!/usr/bin/env python3
"""清理 extra_cases.json 中的跑题案例（刑事/行政/非农村集体资产）。

这些案例虽然通过了采集阶段的关键词筛选，但不适合口播文案，
且会挤占候选池位置。用法: python scripts/clean_extra_offtopic.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.validator import is_rural_collective_theme


def main():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# scripts/ -> project root
    path = os.path.join(base, "data", "extra_cases.json")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    cases = data.get("cases", {})
    print(f"清理前: {len(cases)} 个案例")

    rejected = []
    kept = {}
    for rule_code, rec in cases.items():
        case = rec.get("case", {})
        if not is_rural_collective_theme(case):
            rejected.append(f"{rule_code} {case.get('title', '')[:50]}")
            continue
        kept[rule_code] = rec

    print(f"移除 {len(rejected)} 个跑题案例:")
    for r in rejected:
        print(f"  {r}")

    print(f"\n清理后: {len(kept)} 个案例")

    data["cases"] = kept
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[OK] 已保存到 {path}")


if __name__ == "__main__":
    main()