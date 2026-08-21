#!/usr/bin/env python3
"""清理 extra_cases.json：去除重复案例（同 URL / 同文书号 / 同标题哈希）和跑题案例。

用法: python scripts/clean_extra_store.py
"""
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.validator import is_rural_collective_theme


def _norm_title(title: str) -> str:
    return re.sub(r"[\s\u3000\uFF0C\u3002\u3001\uFF08\uFF09\uFF1A\uFF1B\uFF1F\uFF01\uFF1F\"'""''\u2014\u2015-]", "", title or "")


def _title_hash(title: str) -> str:
    return hashlib.md5(_norm_title(title).encode("utf-8")).hexdigest()[:16]


def main():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "data", "extra_cases.json")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    cases = data.get("cases", {})
    print(f"清理前: {len(cases)} 个案例")

    # 第一步：内容级去重（同 URL / 同文书号 / 同标题哈希）
    kept: dict = {}
    duplicates: list[str] = []
    seen_urls: dict[str, str] = {}       # url → rule_code
    seen_docnos: dict[str, str] = {}     # doc_no → rule_code
    seen_thashes: dict[str, str] = {}   # title_hash → rule_code

    for rule_code, rec in cases.items():
        case = rec.get("case", {})
        urls = set(case.get("source_urls", []) or [])
        doc_no = (case.get("doc_no") or "").strip()
        thash = _title_hash(case.get("title", "") or "")

        dup_target = None
        # 文书号匹配（最强唯一标识：同一案件的文书号是唯一的）
        if doc_no and doc_no in seen_docnos:
            dup_target = seen_docnos[doc_no]
        # 标题哈希匹配（次强：标题高度相似即为同一案例）
        if not dup_target and thash and thash in seen_thashes:
            dup_target = seen_thashes[thash]

        if dup_target:
            duplicates.append(f"{rule_code} → {dup_target} (doc_no={doc_no}, title_hash={thash})")
            # 合并到已有记录
            old = kept[dup_target]["case"]
            old_urls = list(dict.fromkeys(old.get("source_urls", []) + list(urls)))
            old["source_urls"] = old_urls
            old_names = list(dict.fromkeys(old.get("source_names", []) + case.get("source_names", [])))
            old["source_names"] = old_names
            if not old.get("official_link") and case.get("official_link"):
                old["official_link"] = case["official_link"]
            if not old.get("doc_no") and doc_no:
                old["doc_no"] = doc_no
            if not old.get("court") and case.get("court"):
                old["court"] = case["court"]
            if not old.get("province") and case.get("province"):
                old["province"] = case["province"]
            kept[dup_target]["case"] = old
            kept[dup_target]["source_count"] = len({u.split("/")[2] for u in old_urls if "/" in u})
            continue

        # 注册
        if doc_no:
            seen_docnos[doc_no] = rule_code
        if thash:
            seen_thashes[thash] = rule_code
        kept[rule_code] = rec

    print(f"去重后: {len(kept)} 个案例 (移除 {len(duplicates)} 个重复)")
    for d in duplicates:
        print(f"  {d}")

    # 第二步：主题过滤
    rejected: list[str] = []
    final: dict = {}
    for rule_code, rec in kept.items():
        case = rec.get("case", {})
        if not is_rural_collective_theme(case):
            rejected.append(f"{rule_code} {case.get('title', '')[:40]}")
            continue
        final[rule_code] = rec

    print(f"\n主题过滤后: {len(final)} 个案例 (移除 {len(rejected)} 个跑题)")
    for r in rejected:
        print(f"  {r}")

    # 保存
    data["cases"] = final
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n[OK] 已保存到 {path}")


if __name__ == "__main__":
    main()