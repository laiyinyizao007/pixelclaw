#!/usr/bin/env python3
"""
watch_wecom.py — 企业微信消息定时抓取调度器

每天在指定时刻（默认 22:00）触发一次 monitor_messages.py。
"""

import argparse
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))

from utils.logging_setup import setup_logger

REPO_ROOT = Path(__file__).parents[3]
MONITOR_SCRIPT = Path(__file__).parent / "monitor_messages.py"
DEFAULT_HOUR = 22
DEFAULT_MINUTE = 0

logger = setup_logger("wecom_watcher", str(REPO_ROOT / "logs" / "wecom"))


def _next_trigger(hour: int, minute: int) -> datetime:
    """返回下一次触发时刻（今天或明天）。"""
    now = datetime.now()
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _run_monitor(device_id: str | None) -> None:
    cmd = [sys.executable, str(MONITOR_SCRIPT)]
    if device_id:
        cmd += ["--device", device_id]
    logger.info("触发 monitor_messages.py")
    subprocess.run(cmd)


def watch(device_id: str | None, hour: int, minute: int) -> None:
    logger.info("企业微信每日抓取调度启动（每天 %02d:%02d）", hour, minute)
    try:
        while True:
            trigger = _next_trigger(hour, minute)
            wait_sec = (trigger - datetime.now()).total_seconds()
            logger.info("下次抓取时刻: %s（%.0f 秒后）", trigger.strftime("%Y-%m-%d %H:%M"), wait_sec)
            time.sleep(max(wait_sec, 0))
            logger.info("开始每日抓取")
            _run_monitor(device_id)
    except KeyboardInterrupt:
        logger.info("调度已停止")


def main() -> None:
    parser = argparse.ArgumentParser(description="企业微信消息每日定时抓取")
    parser.add_argument("--device", default=None, help="ADB 设备 serial")
    parser.add_argument("--hour", type=int, default=DEFAULT_HOUR, help="每日触发小时（默认 22）")
    parser.add_argument("--minute", type=int, default=DEFAULT_MINUTE, help="每日触发分钟（默认 0）")
    args = parser.parse_args()
    watch(args.device, args.hour, args.minute)


if __name__ == "__main__":
    main()
