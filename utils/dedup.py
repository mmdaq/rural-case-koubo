"""查重去重：基于入库编号（唯一键）的持久化去重库，支持冷却期轮换选材

防状态丢失机制：除 seen_cases.json 外，还维护一份 append-only 推送历史
push_history.jsonl（每次推送追加一行，从不删改）。即使 seen_cases.json 被
旧版本/误操作覆盖丢失条目，加载时也会从历史文件合并恢复，保证已推送案例
永不重复推送。
"""
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

    def __init__(self, path: str, history_path: str = ""):
        self.path = path
        self.history_path = history_path
        self.data = {"cases": {}}
        self._load()
        if self.history_path:
            self._merge_history()
            self._backfill_history()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                log.warning("去重库读取失败，重建: %s", e)
                self.data = {"cases": {}}

    def _merge_history(self):
        """从 append-only 历史文件合并推送记录（seen_cases.json 丢条目时兜底恢复）"""
        if not os.path.exists(self.history_path):
            return
        merged = 0
        try:
            with open(self.history_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # 容忍半行/损坏行
                    code = rec.get("rule_code", "")
                    pushed_at = rec.get("pushed_at", "")
                    if not code or not pushed_at:
                        continue
                    cur = self.data["cases"].get(code)
                    if cur is None or str(cur.get("pushed_at", "")) < pushed_at:
                        self.data["cases"][code] = {
                            "title_hash": rec.get("title_hash", ""),
                            "pushed_at": pushed_at,
                        }
                        merged += 1
        except OSError as e:
            log.warning("推送历史读取失败（忽略）: %s", e)
            return
        if merged:
            log.info("从 push_history.jsonl 合并 %d 条推送记录（防丢失兜底）", merged)

    def _backfill_history(self):
        """历史文件不存在时，用当前去重库一次性播种，之后只追加"""
        if os.path.exists(self.history_path):
            return
        try:
            os.makedirs(os.path.dirname(self.history_path) or ".", exist_ok=True)
            with open(self.history_path, "a", encoding="utf-8") as f:
                for code, rec in self.data["cases"].items():
                    f.write(json.dumps({
                        "rule_code": code,
                        "title_hash": rec.get("title_hash", ""),
                        "pushed_at": rec.get("pushed_at", ""),
                    }, ensure_ascii=False) + "\n")
            log.info("已播种推送历史 %s（%d 条）", self.history_path, len(self.data["cases"]))
        except OSError as e:
            log.warning("推送历史播种失败（不影响主流程）: %s", e)

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
        rec = {
            "title_hash": title_hash(title) if title else "",
            "pushed_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.data["cases"][rule_code] = rec
        self._save()
        # append-only 历史：即使 seen_cases.json 日后被旧版覆盖，也能从这里恢复
        if self.history_path:
            try:
                os.makedirs(os.path.dirname(self.history_path) or ".", exist_ok=True)
                with open(self.history_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(
                        {"rule_code": rule_code, **rec}, ensure_ascii=False,
                    ) + "\n")
            except OSError as e:
                log.warning("推送历史追加失败（不影响主流程）: %s", e)

    def dedup(self, cases: list) -> list:
        """过滤掉已推送过的案例"""
        fresh = [c for c in cases if not self.is_seen(c.get("rule_code", ""), c.get("title", ""))]
        dropped = len(cases) - len(fresh)
        if dropped:
            log.info("去重过滤 %d 个已推送案例", dropped)
        return fresh
