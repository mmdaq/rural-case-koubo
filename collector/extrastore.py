"""扩展案例库持久化：每日自动发现的案例入库，长期累积，供后续每日使用

结构：{"cases": {rule_code: {case_dict, first_seen, source_count}}}
"""
import json
import os
from datetime import datetime

from .models import Case
from utils.logger import get_logger

log = get_logger("extrastore")


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

    def upsert(self, case: Case, source_count: int = 1):
        """新增或合并（同一编号累计来源，提升置信度）"""
        rule_code = case.rule_code
        rec = self.data["cases"].get(rule_code)
        if rec:
            # 合并来源
            old = rec["case"]
            urls = list(dict.fromkeys(old.get("source_urls", []) + case.source_urls))
            names = list(dict.fromkeys(old.get("source_names", []) + case.source_names))
            old["source_urls"] = urls
            old["source_names"] = names
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
