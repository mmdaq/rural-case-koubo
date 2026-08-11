"""查重去重：基于入库编号 + 标题归一化的持久化去重库"""
import hashlib
import json
import os
import re
from datetime import datetime

from .logger import get_logger

log = get_logger("dedup")


def _norm_title(title: str) -> str:
    """标题归一化：去标点空格，便于相似标题比对"""
    return re.sub(r"[\s\u3000，。、（）()：:；;！？!?\"'“”‘’\-—]", "", title or "")


def title_hash(title: str) -> str:
    return hashlib.md5(_norm_title(title).encode("utf-8")).hexdigest()[:16]


class SeenStore:
    """记录已推送案例，防止重复。结构：{"cases": {rule_code: {title_hash, pushed_at}}}"""

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
                log.warning("去重库读取失败，重建: %s", e)
                self.data = {"cases": {}}

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def is_seen(self, rule_code: str, title: str = "") -> bool:
        if not rule_code:
            # 无入库编号（如仅官方链接的最高院典型案例）：以标题哈希为键
            rule_code = f"no-code:{title_hash(title)}" if title else ""
        if not rule_code:
            return False
        rec = self.data["cases"].get(rule_code)
        if rec is None:
            return False
        # 同编号但标题差异很大（如合并案件），视为不同案例
        if title and rec.get("title_hash") != title_hash(title):
            return False
        return True

    def mark_seen(self, rule_code: str, title: str = ""):
        if not rule_code:
            rule_code = f"no-code:{title_hash(title)}" if title else ""
        if not rule_code:
            return
        self.data["cases"][rule_code] = {
            "title_hash": title_hash(title) if title else "",
            "pushed_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._save()

    def dedup(self, cases: list) -> list:
        """过滤掉已推送过的案例"""
        fresh = [c for c in cases if not self.is_seen(c.get("rule_code", ""), c.get("title", ""))]
        dropped = len(cases) - len(fresh)
        if dropped:
            log.info("去重过滤 %d 个已推送案例", dropped)
        return fresh
