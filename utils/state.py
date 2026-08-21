"""运行状态持久化：记录连续无新增案例天数，控制通知/停止节奏。

结构：{"consecutive_no_new": int, "last_run": str, "stopped": bool}
"""
import json
import os
from datetime import datetime

from .logger import get_logger

log = get_logger("state")


class RunState:
    def __init__(self, path: str):
        self.path = path
        self.data = {
            "consecutive_no_new": 0,
            "last_run": "",
            "stopped": False,
        }
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                log.warning("状态文件读取失败，重建: %s", e)
                self.data = {
                    "consecutive_no_new": 0,
                    "last_run": "",
                    "stopped": False,
                }

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    @property
    def consecutive_no_new(self) -> int:
        return self.data.get("consecutive_no_new", 0)

    @property
    def stopped(self) -> bool:
        return self.data.get("stopped", False)

    def record_no_new(self):
        """记录一次无新增案例"""
        self.data["consecutive_no_new"] = self.data.get("consecutive_no_new", 0) + 1
        self.data["last_run"] = datetime.now().isoformat(timespec="seconds")
        self._save()
        log.info("连续无新增案例天数: %d", self.data["consecutive_no_new"])

    def reset(self):
        """有新案例时重置计数器"""
        if self.data.get("consecutive_no_new", 0) > 0:
            log.info("发现新案例，重置连续无新增计数（原 %d 天）",
                     self.data["consecutive_no_new"])
        self.data["consecutive_no_new"] = 0
        self.data["last_run"] = datetime.now().isoformat(timespec="seconds")
        self._save()

    def stop(self):
        """达到阈值，停止推送"""
        self.data["stopped"] = True
        self._save()
        log.warning("连续 %d 天无新增案例，系统停止推送。"
                    "请检查采集源配置或关键词后手动恢复（删除此标记或修改 config.yaml）。",
                    self.data["consecutive_no_new"])

    def unstop(self):
        """手动恢复推送"""
        self.data["stopped"] = False
        self._save()
        log.info("系统已恢复推送。")