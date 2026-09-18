"""
Command router: 自然语言指挥 AI Agent

POST /api/command  →  {"text": "帮我查今日统计"}
返回 {task_id}，客户端订阅 WS /ws/task/{task_id} 查看实时日志。
"""

import asyncio
import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from api.services.agent import PixelClawAgent
from api.services import process_manager as pm

router = APIRouter(prefix="/api", tags=["command"])

_agent = PixelClawAgent()


class CommandRequest(BaseModel):
    text: str


@router.post("/command")
async def run_command(body: CommandRequest):
    task_id = str(uuid.uuid4())[:8]
    asyncio.create_task(
        _agent.run(body.text, task_id, pm._broadcast)
    )
    return {"task_id": task_id}
