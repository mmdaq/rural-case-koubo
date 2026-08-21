import json
import sys

import os
base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(base, "data", "extra_cases.json"), "r", encoding="utf-8") as f:
    data = json.load(f)

cases = data.get("cases", {})
print(f"案例总数: {len(cases)}")
for code, rec in cases.items():
    c = rec.get("case", {})
    title = (c.get("title", "") or "")[:50]
    doc_no = (c.get("doc_no", "") or "")[:30]
    urls = len(c.get("source_urls", []))
    print(f"  {code} | {title} | doc_no={doc_no} | urls={urls}")