"""
AutoX.js 脚本的统一日志客户端

供 AutoX.js 脚本调用，将日志发送到 API 端点。
同时支持本地文件回退写入。
"""

import json
import logging
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests


class AutoXLogger:
    """
    AutoX.js 日志客户端

    使用方式:
        logger = AutoXLogger("my_script")
        logger.info("操作成功")
        logger.error("操作失败", exc_info=True)  # exc_info=True 自动捕获堆栈
    """

    def __init__(
        self,
        source: str = "autox",
        api_url: str = "http://localhost:8000/api/log",
        local_log_dir: str = "./logs/autox",
        fallback_to_file: bool = True,
    ):
        self.source = source
        self.api_url = api_url
        self.local_log_dir = Path(local_log_dir)
        self.fallback_to_file = fallback_to_file
        self._local_log_file: Optional[Path] = None

    def _ensure_local_log(self) -> Path:
        """确保本地日志文件存在"""
        if self._local_log_file is None:
            self.local_log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d")
            self._local_log_file = self.local_log_dir / f"{self.source}_{stamp}.log"
        return self._local_log_file

    def _write_local(self, level: str, message: str, stack_trace: str = None) -> None:
        """写入本地日志文件（API 失败时的回退）"""
        if not self.fallback_to_file:
            return

        try:
            log_file = self._ensure_local_log()
            ts = datetime.now().isoformat(timespec="milliseconds")
            lines = [f"[{ts}] [{level.upper()}] [{self.source}] {message}"]
            if stack_trace:
                lines.append(f"  Stack trace:\n{stack_trace}")

            with open(log_file, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception:
            pass  # 静默失败，不影响主流程

    def _send_to_api(self, level: str, message: str, stack_trace: str = None) -> bool:
        """发送日志到 API 端点"""
        payload = {
            "level": level.upper(),
            "message": message,
            "source": self.source,
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            "stack_trace": stack_trace,
        }

        try:
            resp = requests.post(self.api_url, json=payload, timeout=5)
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def _log(self, level: str, message: str, exc_info: bool = False) -> None:
        """内部日志方法"""
        stack_trace = None
        if exc_info and sys.exc_info()[0]:
            stack_trace = "".join(traceback.format_exception(*sys.exc_info()))

        # 优先尝试 API，失败则写本地文件
        if not self._send_to_api(level, message, stack_trace):
            self._write_local(level, message, stack_trace)

        # 同时输出到 stdout（如果存在）
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"{ts} {level.upper():7s} [{self.source}] {message}")
        if stack_trace:
            print(f"  Stack trace:\n{stack_trace}")

    def debug(self, message: str, exc_info: bool = False):
        self._log("DEBUG", message, exc_info)

    def info(self, message: str, exc_info: bool = False):
        self._log("INFO", message, exc_info)

    def warning(self, message: str, exc_info: bool = False):
        self._log("WARNING", message, exc_info)

    def error(self, message: str, exc_info: bool = False):
        self._log("ERROR", message, exc_info)

    def critical(self, message: str, exc_info: bool = False):
        self._log("CRITICAL", message, exc_info)


# 便捷函数
_default_logger: Optional[AutoXLogger] = None


def get_logger(source: str = "autox") -> AutoXLogger:
    """获取默认日志客户端实例"""
    global _default_logger
    if _default_logger is None:
        _default_logger = AutoXLogger(source=source)
    return _default_logger
