"""
ProcessManager: asyncio subprocess + WebSocket 实时日志推送

特性：
- 日志缓冲：所有日志保存在内存，晚连接的 WS 客户端会收到历史回放
- Windows ProactorEventLoop 支持（在 server.py 设置）
- 停止进程用 proc.terminate()
"""

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from fastapi import WebSocket


# task_id -> 运行状态 + 日志缓冲
_tasks: Dict[str, dict] = {}

# task_id -> 实时订阅集合
_ws_subscribers: Dict[str, Set[WebSocket]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_task_id() -> str:
    return str(uuid.uuid4())[:8]


def get_task(task_id: str) -> Optional[dict]:
    return _tasks.get(task_id)


async def subscribe(task_id: str, ws: WebSocket) -> None:
    """
    注册订阅，并立即回放该任务已有的历史日志。
    如果任务已结束，回放后发 done 然后关闭。
    """
    task = _tasks.get(task_id)

    # 回放历史日志
    if task:
        for msg in task.get("logs", []):
            try:
                await ws.send_json(msg)
            except Exception:
                return

    # 任务已结束 → 发 done 后不再订阅
    if task and task.get("status") in ("done", "error"):
        try:
            await ws.send_json({
                "type": "done",
                "exit_code": task.get("exit_code"),
                "duration_s": task.get("duration_s"),
            })
        except Exception:
            pass
        return

    _ws_subscribers.setdefault(task_id, set()).add(ws)


def unsubscribe(task_id: str, ws: WebSocket) -> None:
    subs = _ws_subscribers.get(task_id)
    if subs:
        subs.discard(ws)


async def _broadcast(task_id: str, msg: dict) -> None:
    # 缓冲（done 消息不缓冲，避免重复发送）
    task = _tasks.get(task_id)
    if task is not None and msg.get("type") == "log":
        task.setdefault("logs", []).append(msg)

    subs = list(_ws_subscribers.get(task_id, set()))
    dead = []
    for ws in subs:
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        unsubscribe(task_id, ws)


async def run_script(task_id: str, cmd: list, cwd: str | None = None) -> None:
    """
    在后台运行命令，逐行推送 stdout/stderr 到订阅的 WebSocket。
    cmd 示例: ["python", "-u", "scenarios/boss/scripts/daily_greet.py"]
    """
    _tasks[task_id] = {
        "status": "running",
        "start_ts": time.monotonic(),
        "logs": [],
    }

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
        )

        _tasks[task_id]["pid"] = proc.pid

        # 逐行读取并广播
        while True:
            line_bytes = await proc.stdout.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue
            level = "ERROR" if ("ERROR" in line or "Error" in line or "Traceback" in line) else "INFO"
            await _broadcast(task_id, {
                "type": "log",
                "level": level,
                "text": line,
                "ts": _now_iso(),
            })

        await proc.wait()
        elapsed = time.monotonic() - _tasks[task_id]["start_ts"]
        _tasks[task_id].update({
            "status": "done" if proc.returncode == 0 else "error",
            "exit_code": proc.returncode,
            "duration_s": round(elapsed, 1),
        })

        await _broadcast(task_id, {
            "type": "done",
            "exit_code": proc.returncode,
            "duration_s": round(elapsed, 1),
        })

    except Exception as e:
        _tasks[task_id]["status"] = "error"
        err_msg = {
            "type": "log",
            "level": "ERROR",
            "text": f"[ProcessManager] 启动失败: {e}",
            "ts": _now_iso(),
        }
        _tasks[task_id].setdefault("logs", []).append(err_msg)
        await _broadcast(task_id, err_msg)
        await _broadcast(task_id, {"type": "done", "exit_code": -1, "duration_s": 0})


def stop_task(task_id: str) -> bool:
    task = _tasks.get(task_id)
    if not task or task.get("status") != "running":
        return False
    pid = task.get("pid")
    if pid is None:
        return False
    try:
        import psutil
        p = psutil.Process(pid)
        p.terminate()
        return True
    except Exception:
        return False
