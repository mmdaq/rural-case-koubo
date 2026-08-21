import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.validator import is_rural_collective_theme

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# scripts/ -> project root

with open(os.path.join(base, "data", "seen_cases.json"), "r", encoding="utf-8") as f:
    seen = json.load(f)

with open(os.path.join(base, "data", "extra_cases.json"), "r", encoding="utf-8") as f:
    extra = json.load(f)

extra_cases = {code: rec["case"] for code, rec in extra.get("cases", {}).items()}

print("=== seen_cases 主题检查 ===")
for code, rec in seen.get("cases", {}).items():
    # 从 extra_cases 中查找对应案例
    case = extra_cases.get(code)
    if case:
        ok = is_rural_collective_theme(case)
        title = case.get("title", "")[:50]
        status = "OK" if ok else "OFF-TOPIC"
        print(f"  [{status}] {code} | {title}")
    else:
        print(f"  [???] {code} | not in extra_cases (seed case?)")

print()
print("=== extra_cases 主题检查 ===")
offtopic = []
for code, case in extra_cases.items():
    if not is_rural_collective_theme(case):
        offtopic.append((code, case.get("title", "")[:50]))

if offtopic:
    print(f"  发现 {len(offtopic)} 个跑题案例:")
    for code, title in offtopic:
        print(f"    {code} | {title}")
else:
    print("  OK: 全部通过主题过滤")