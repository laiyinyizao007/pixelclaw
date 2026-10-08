"""
Debug router: 跨环境日志接收端点

POST /api/log 接收 AutoX.js 等外部脚本的错误日志，写入 logs/autox_debug/
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["debug"])

# 日志目录
_LOG_DIR = Path("./logs/autox_debug")
_LOG_DIR.mkdir(parents=True, exist_ok=True)


class LogEntry(BaseModel):
    """日志条目模型"""
    level: str = "INFO"  # DEBUG, INFO, WARNING, ERROR, CRITICAL
    message: str
    source: str = "autox"  # 来源标识
    timestamp: str = None  # 可选，ISO 格式时间戳
    stack_trace: str = None  # 可选，异常堆栈
    context: dict = None  # 可选，附加上下文


def _get_logger() -> logging.Logger:
    """获取或创建 autox_debug logger"""
    logger = logging.getLogger("autox_debug")
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        # 控制台 handler
        import io, sys
        sh = logging.StreamHandler(io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8"))
        sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
        logger.addHandler(sh)
    return logger


def _write_log(entry: LogEntry) -> None:
    """写入日志到文件"""
    log_file = _LOG_DIR / f"autox_debug_{datetime.now().strftime('%Y%m%d')}.log"

    ts = entry.timestamp or datetime.now().isoformat(timespec="milliseconds")

    # 格式化输出
    log_lines = [f"[{ts}] [{entry.level.upper()}] [{entry.source}] {entry.message}"]

    if entry.stack_trace:
        log_lines.append(f"  Stack trace:\n{entry.stack_trace}")

    if entry.context:
        log_lines.append(f"  Context: {json.dumps(entry.context, ensure_ascii=False)}")

    # 追加写入文件
    with open(log_file, "a", encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n")

    # 同时输出到标准日志
    logger = _get_logger()
    log_method = getattr(logger, entry.level.lower(), logger.info)
    log_method(entry.message)
    if entry.stack_trace:
        logger.debug(entry.stack_trace)


@router.post("/log")
async def receive_log(entry: LogEntry):
    """
    接收外部日志条目并写入 logs/autox_debug.log

    AutoX.js 脚本中的 try-catch 异常捕获块应调用此端点上报日志。

    示例请求:
        curl -X POST http://localhost:8000/api/log \\
          -H "Content-Type: application/json" \\
          -d '{"level":"ERROR","message":"操作失败","source":"autox","stack_trace":"Error: test\\n at test.js:10"}'
    """
    try:
        _write_log(entry)
        return {"status": "ok", "timestamp": datetime.now().isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/log/files")
async def list_log_files():
    """列出可用的日志文件"""
    files = []
    if _LOG_DIR.exists():
        for f in sorted(_LOG_DIR.glob("autox_debug_*.log"), reverse=True):
            files.append({
                "name": f.name,
                "size": f.stat().st_size,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat()
            })
    return {"files": files}


@router.get("/log/files/{filename}")
async def read_log_file(filename: str):
    """读取指定日志文件内容"""
    # 安全检查：只允许读取 autox_debug_*.log
    if not filename.startswith("autox_debug_") or not filename.endswith(".log"):
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = _LOG_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    return {"filename": filename, "content": content}
