"""
PixelClaw FastAPI 后端

启动方式:
    uvicorn api.server:app --host 0.0.0.0 --port 8000 --reload

Windows 需要 ProactorEventLoop 才能用 asyncio.create_subprocess_exec。
"""

import asyncio
import sys

# Windows: 必须在任何 asyncio 操作前设置
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from api.routers import device, scenarios, boss, command
from api.services import process_manager as pm

app = FastAPI(title="PixelClaw API", version="0.1.0")

# CORS
_FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_FRONTEND_ORIGIN] if _FRONTEND_ORIGIN != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Key 鉴权（设置 PIXELCLAW_API_KEY 后生效，不设则跳过）
_API_KEY = os.getenv("PIXELCLAW_API_KEY", "")

@app.middleware("http")
async def check_api_key(request: Request, call_next):
    if _API_KEY:
        # /api/health 不鉴权
        if request.url.path != "/api/health":
            key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
            if key != _API_KEY:
                raise HTTPException(401, "Invalid API Key")
    return await call_next(request)

# --- Routers ---
app.include_router(device.router)
app.include_router(scenarios.router)
app.include_router(boss.router)
app.include_router(command.router)


# --- WebSocket: 实时任务日志 ---
@app.websocket("/ws/task/{task_id}")
async def ws_task_log(websocket: WebSocket, task_id: str, api_key: str = ""):
    """
    订阅某个任务的实时日志流。
    消息格式: {"type":"log","level":"INFO","text":"...","ts":"..."}
               {"type":"done","exit_code":0,"duration_s":47.3}
    """
    if _API_KEY and api_key != _API_KEY:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    # subscribe 会回放历史日志，若任务已结束则发 done
    await pm.subscribe(task_id, websocket)

    try:
        # 保持连接直到客户端断开
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                # 发 ping 保活
                await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
    finally:
        pm.unsubscribe(task_id, websocket)


# --- 静态文件：托管前端 build 产物 ---
_WEB_DIST = Path(__file__).parent.parent / "web" / "dist"
if _WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=str(_WEB_DIST), html=True), name="web")


@app.get("/api/health")
async def health():
    return {"status": "ok"}
