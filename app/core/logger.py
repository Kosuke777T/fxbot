# app/core/logger.py
from loguru import logger
import sys
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

def setup():
    """Loguru ロガーの共通設定"""
    logger.remove()
    logger.add(sys.stdout, level="INFO", format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}")
    logger.add(
        LOG_DIR / "app.log",
        rotation="10 MB",
        retention="10 days",
        compression="zip",
        level="INFO",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
    )
    return logger

# すぐ利用できるように初期化
setup()
