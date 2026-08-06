"""统一日志：控制台 + 文件双输出"""
import logging
import os
from datetime import datetime

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "logs")


def get_logger(name: str = "rck") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        fh = logging.FileHandler(
            os.path.join(_LOG_DIR, f"run_{datetime.now():%Y%m%d}.log"),
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass
    return logger
