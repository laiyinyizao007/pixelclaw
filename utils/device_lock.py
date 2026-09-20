"""
device_lock — 跨脚本设备占用锁

同一台手机同一时刻只能有一个自动化脚本控制屏幕，此模块提供一个基于文件的
互斥锁，供各 scenario 脚本的 main() 包裹整个运行主体。拿不到锁时抛出
DeviceBusyError，调用方应捕获后记录日志并直接退出（不等待重试）。
"""

import ctypes
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK_PATH = REPO_ROOT / "scenarios" / ".device_lock"


class DeviceBusyError(Exception):
    """设备当前被其他脚本占用。"""


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@contextmanager
def device_lock(
    lock_path: Union[str, Path] = DEFAULT_LOCK_PATH,
    timeout_minutes: int = 30,
    script_name: str = "",
):
    """
    获取设备锁；成功则 yield，失败抛出 DeviceBusyError。

    锁文件记录 {pid, started_at, script}；若已有锁但进程已死或超过
    timeout_minutes，视为陈旧锁并自动接管。
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if lock_path.exists():
        try:
            info = json.loads(lock_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            info = {}
        pid = info.get("pid")
        started_at = info.get("started_at")
        stale = True
        if pid and started_at:
            try:
                age_minutes = (
                    datetime.now(timezone.utc) - datetime.fromisoformat(started_at)
                ).total_seconds() / 60
            except ValueError:
                age_minutes = timeout_minutes + 1
            if age_minutes < timeout_minutes and _pid_alive(pid):
                stale = False
        if not stale:
            raise DeviceBusyError(
                f"设备已被占用：script={info.get('script', '?')} pid={pid} started_at={started_at}"
            )

    lock_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "started_at": datetime.now(timezone.utc).isoformat(),
                "script": script_name,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    try:
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
