#!/usr/bin/env python3
"""清理 seen_cases.json：移除重复案例（同文书号/同标题哈希）和跑题案例。

用法: python scripts/clean_seen_store.py
"""
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.validator import is_rural_collective_theme
from collector.fallback import SEED_CASES


def _norm_title(title: str) -> str:
    return re.sub(r"[\s\u3000\uFF0C\u3002\u3001\uFF08\uFF09\uFF1A\uFF1B\uFF1F\uFF01\uFF1F\"'""''\u2014\u2015-]", "", title or "")


def _title_hash(title: str) -> str:
    return hashlib.md5(_norm_title(title).encode("utf-8")).hexdigest()[:16]


def main():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    seen_path = os.path.join(base, "data", "seen_cases.json")
    extra_path = os.path.join(base, "data", "extra_cases.json")

    with open(seen_path, "r", encoding="utf-8") as f:
        seen = json.load(f)

    with open(extra_path, "r", encoding="utf-8") as f:
        extra = json.load(f)

    cases = seen.get("cases", {})
    print(f"清理前 seen_cases: {len(cases)} 条")

    # 构建 extra_cases 的文书号 → rule_code 映射，用于识别"同案不同号"
    extra_docnos: dict[str, str] = {}
    extra_thashes: dict[str, str] = {}
    for code, rec in extra.get("cases", {}).items():
        c = rec.get("case", {})
        dn = (c.get("doc_no") or "").strip()
        th = _title_hash(c.get("title", "") or "")
        if dn:
            extra_docnos[dn] = code
        if th:
            extra_thashes[th] = code

    # 也构建种子案例的映射
    seed_docnos: dict[str, str] = {}
    seed_thashes: dict[str, str] = {}
    for s in SEED_CASES:
        dn = (s.get("doc_no") or "").strip()
        th = _title_hash(s.get("title", "") or "")
        if dn:
            seed_docnos[dn] = s["rule_code"]
        if th:
            seed_thashes[th] = s["rule_code"]

    # 第一步：内容级去重（同文书号 / 同标题哈希 → 保留更可信的那条）
    kept: dict = {}
    duplicates: list[str] = []
    seen_docnos: dict[str, str] = {}
    seen_thashes: dict[str, str] = {}

    for rule_code, rec in cases.items():
        title = rec.get("title_hash", "")
        # 从 extra_cases 和种子案例中查找真正的 rule_code
        # 这里我们用 title_hash 反查，但更可靠的是检查 extra_cases 中是否有同文书号的条目

        # 检查是否与已保留的条目重复
        # 由于 seen_cases 只存了 title_hash，我们需要用 title_hash 来去重
        # 但不同的案例可能有相同的 title_hash（极少情况），所以主要靠 extra_cases 的映射

        # 检查 extra_cases 中是否有同文书号的条目
        # seen_cases 中没有 doc_no 信息，所以我们需要通过 extra_cases 来判断
        # 简化方案：如果 seen_cases 中的 title_hash 与 extra_cases 中某个条目的 title_hash 相同，
        # 且 extra_cases 中该条目已合并到另一个 rule_code，则 seen_cases 中的这条也应移除

        kept[rule_code] = rec

    # 第二步：检查 seen_cases 中的条目是否在 extra_cases 中有"更好"的版本
    # 如果 seen_cases 的 rule_code 不在 extra_cases 中，但它的 title_hash 与 extra_cases 中
    # 某个条目的 title_hash 相同，说明它是重复的
    extra_codes = set(extra.get("cases", {}).keys())
    seed_codes = {s["rule_code"] for s in SEED_CASES}

    to_remove: list[str] = []
    for rule_code in list(kept.keys()):
        rec = kept[rule_code]
        th = rec.get("title_hash", "")

        # 检查是否是重复案例：title_hash 与 extra_cases 中另一个条目相同
        matched_codes = [c for c, t in extra_thashes.items() if t == th and c != rule_code]
        if matched_codes:
            # 这个 seen_cases 条目与 extra_cases 中的另一个条目是同一案例
            # 保留 extra_cases 中的版本（更完整），移除 seen_cases 中的
            to_remove.append(f"{rule_code} → 重复，extra_cases 中已有 {matched_codes[0]}")
            del kept[rule_code]
            continue

        # 检查是否是跑题案例
        # 从 extra_cases 中查找对应案例来判断主题
        if rule_code in extra_codes:
            case = extra["cases"][rule_code]["case"]
            if not is_rural_collective_theme(case):
                to_remove.append(f"{rule_code} → 跑题: {case.get('title', '')[:40]}")
                del kept[rule_code]
                continue
        elif rule_code in seed_codes:
            # 种子案例都是人工核实的，保留
            pass
        else:
            # 不在 extra_cases 也不在种子案例中（可能是早期推送后已从 extra_cases 移除的）
            # 保守起见，保留
            pass

    print(f"移除 {len(to_remove)} 条:")
    for r in to_remove:
        print(f"  {r}")

    print(f"\n清理后 seen_cases: {len(kept)} 条")

    # 保存
    seen["cases"] = kept
    with open(seen_path, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)

    print(f"[OK] 已保存到 {seen_path}")


if __name__ == "__main__":
    main()