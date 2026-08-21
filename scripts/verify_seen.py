import json
import os
from collections import Counter

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The script is in scripts/, so base is the project root
with open(os.path.join(base, "data", "seen_cases.json"), "r", encoding="utf-8") as f:
    seen = json.load(f)

cases = seen.get("cases", {})
print(f"seen_cases 条目数: {len(cases)}")

thashes = [v.get("title_hash", "") for v in cases.values()]
dupes = {th: cnt for th, cnt in Counter(thashes).items() if cnt > 1}
if dupes:
    print(f"WARNING: 发现重复 title_hash: {dupes}")
else:
    print("OK: 无重复 title_hash")

for code, rec in cases.items():
    print(f"  {code} | pushed_at={rec['pushed_at']}")