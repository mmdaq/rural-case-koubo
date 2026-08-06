"""定时调度：每日早晨 8 点执行（也可用系统 crontab，见 deploy/crontab.example）"""
import time

import schedule

from pipeline import run_pipeline
from utils.logger import get_logger

log = get_logger("scheduler")


def start_daemon(time_str: str = "08:00"):
    """常驻进程：每天 time_str 执行一次"""
    log.info("调度器启动，每日 %s 执行", time_str)

    def job():
        try:
            run_pipeline()
        except Exception as e:
            log.exception("每日任务执行异常: %s", e)

    schedule.every().day.at(time_str).do(job)
    log.info("首次执行将等待至 %s（如需立即执行请用 run-once）", time_str)
    while True:
        schedule.run_pending()
        time.sleep(30)
