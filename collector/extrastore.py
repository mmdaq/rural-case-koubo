"""扩展案例库持久化：每日自动发现的案例入库，长期累积，供后续每日使用

结构：{"cases": {rule_code: {case_dict, first_seen, source_count}}}
"""
import hashlib
import json
import os
import re
from datetime import datetime

from .models import Case
from utils.logger import get_logger

log = get_logger("extrastore")


def _norm_title(title: str) -> str:
    """标题归一化：去标点空格，便于相似标题比对"""
    return re.sub(r"[\s\u3000，。、（）()：:；;！？!?\"'""''—-]", "", title or "")


def _title_hash(title: str) -> str:
    return hashlib.md5(_norm_title(title).encode("utf-8")).hexdigest()[:16]


class ExtraStore:
    def __init__(self, path: str):
        self.path = path
        self.data = {"cases": {}}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                log.warning("扩展库读取失败，重建: %s", e)
                self.data = {"cases": {}}

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def get(self, rule_code: str) -> dict | None:
        rec = self.data["cases"].get(rule_code)
        return rec.get("case") if rec else None

    def all_cases(self) -> list[dict]:
        return [rec["case"] for rec in self.data["cases"].values()]

    def _find_duplicate(self, case: Case) -> str | None:
        """内容级查重：返回已存在的 rule_code，若找到则说明是同一案例。

        按优先级匹配：
        1. 裁判文书号相同（同一案件的唯一标识，最强）
        2. 标题归一化哈希相同（标题高度相似）

        注意：不使用 URL 匹配，因为同一页面（如澎湃"一网一库"专题）
        可能包含多个不同案例，它们共享同一个 source_url 但不是同一案例。
        """
        new_doc_no = (case.doc_no or "").strip()
        new_title_hash = _title_hash(case.title or "")

        for rule_code, rec in self.data["cases"].get("cases", {}).items():
            old = rec.get("case", {})

            # 1. 裁判文书号相同 → 同一案件
            old_doc_no = (old.get("doc_no") or "").strip()
            if new_doc_no and old_doc_no and new_doc_no == old_doc_no:
                return rule_code

            # 2. 标题归一化后哈希相同 → 标题高度相似
            old_title_hash = _title_hash(old.get("title", "") or "")
            if new_title_hash and old_title_hash and new_title_hash == old_title_hash:
                return rule_code

        return None

    def upsert(self, case: Case, source_count: int = 1):
        """新增或合并（同一编号累计来源，提升置信度）

        同时做内容级查重：若新案例与已存在案例的 URL / 文书号 / 标题哈希匹配，
        则合并到已有记录下，避免同一案例被分配不同编号后重复入库。
        """
        rule_code = case.rule_code

        # 内容级查重：同源 URL / 同文书号 / 同标题 → 合并到已有记录
        dup_code = self._find_duplicate(case)
        if dup_code and dup_code != rule_code:
            log.info(
                "内容查重命中：新编号 %s 合并到已有 %s（来源: %s）",
                rule_code, dup_code,
                ",".join(case.source_urls or [""]),
            )
            rule_code = dup_code

        rec = self.data["cases"].get(rule_code)
        if rec:
            # 合并来源
            old = rec["case"]
            urls = list(dict.fromkeys(old.get("source_urls", []) + case.source_urls))
            names = list(dict.fromkeys(old.get("source_names", []) + case.source_names))
            old["source_urls"] = urls
            old["source_names"] = names
            # 保留更完整的字段（优先保留有 official_link / doc_no 的）
            if not old.get("official_link") and case.official_link:
                old["official_link"] = case.official_link
            if not old.get("doc_no") and case.doc_no:
                old["doc_no"] = case.doc_no
            if not old.get("court") and case.court:
                old["court"] = case.court
            if not old.get("province") and case.province:
                old["province"] = case.province
            rec["case"] = old
            rec["source_count"] = len({u.split("/")[2] for u in urls if "/" in u})
            rec["updated_at"] = datetime.now().isoformat(timespec="seconds")
            changed = False
        else:
            self.data["cases"][rule_code] = {
                "case": case.to_dict(),
                "source_count": source_count,
                "first_seen": datetime.now().isoformat(timespec="seconds"),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            changed = True
        self._save()
        return changed

    def stats(self) -> dict:
        return {"total": len(self.data["cases"])}


class CrawlStore:
    """已抓取 URL 记录（持久化到 data/crawled_urls.json，随仓库提交）

    用途：每次运行只抓取"没抓过"的新页面，避免反复抓同一批页面，
    让每日自动采集聚焦新内容，案例池持续扩容。
    """

    def __init__(self, path: str):
        self.path = path
        self.urls: set[str] = set()
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.urls = set(data.get("urls", []))
            except (json.JSONDecodeError, OSError) as e:
                log.warning("已抓URL记录读取失败，重建: %s", e)
                self.urls = set()

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"urls": sorted(self.urls)}, f, ensure_ascii=False, indent=2)

    def is_crawled(self, url: str) -> bool:
        return url in self.urls

    def mark_crawled(self, url: str):
        if url not in self.urls:
            self.urls.add(url)
            self._save()

    def stats(self) -> dict:
        return {"total": len(self.urls)}
