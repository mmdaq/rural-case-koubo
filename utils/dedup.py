"""查重去重：基于入库编号（唯一键）的持久化去重库，支持冷却期轮换选材"""
import hashlib
import json
import os
import re
from datetime import datetime

from .logger import get_logger

log = get_logger("dedup")


def _norm_title(title: str) -> str:
    """标题归一化：去标点空格，便于相似标题比对"""
    return re.sub(r"[\s\u3000，。、（）()：:；;！？!?\"'""''—-]", "", title or "")


def title_hash(title: str) -> str:
    return hashlib.md5(_norm_title(title).encode("utf-8")).hexdigest()[:16]


class SeenStore:
    """记录已推送案例与推送时间。结构：{"cases": {rule_code: {title_hash, pushed_at}}}

    同一入库编号即视为同一案例（入库编号是人民法院案例库的唯一标识），
    不再用标题差异区分，避免同案不同标题绕过去重。
    """

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
        return rule_code in self.data["cases"]

    def last_pushed_at(self, rule_code: str, title: str = "") -> datetime | None:
        """返回该案例最近一次推送时间（未推送过返回 None）"""
        if not rule_code:
            rule_code = f"no-code:{title_hash(title)}" if title else ""
        if not rule_code:
            return None
        rec = self.data["cases"].get(rule_code)
        if not rec:
            return None
        try:
            return datetime.fromisoformat(rec.get("pushed_at", ""))
        except (ValueError, TypeError):
            return None

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
