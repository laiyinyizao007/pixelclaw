"""
Scenarios router: 触发/停止 Boss 等场景任务，返回 task_id。
前端通过 WS /ws/task/{task_id} 订阅实时日志。
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from api.models.schemas import TaskResponse, TaskStatus
from api.services import process_manager as pm

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])

# 项目根目录（此文件在 api/routers/ 下，上两级为项目根）
PROJECT_ROOT = str(Path(__file__).resolve().parents[2])

# 可用步骤 → 脚本路径（相对于项目根）
_BOSS_STEPS = {
    "daily-greet": "scenarios/boss/scripts/daily_greet.py",
    "scrape":       "scenarios/boss/scripts/scrape_job_details.py",
    "smart-greet":  "scenarios/boss/scripts/smart_match_greet.py",
    "analyze":      "scenarios/boss/scripts/analyze_requirements.py",
}


@router.post("/boss/run", response_model=TaskResponse)
async def run_boss(
    background_tasks: BackgroundTasks,
    step: str = Query(default="daily-greet", description="daily-greet | scrape | smart-greet | analyze"),
):
    script = _BOSS_STEPS.get(step)
    if script is None:
        raise HTTPException(400, f"未知步骤 '{step}'，可选: {list(_BOSS_STEPS)}")

    task_id = pm.new_task_id()
    cmd = ["python", "-u", script]
    background_tasks.add_task(pm.run_script, task_id, cmd, PROJECT_ROOT)
    return TaskResponse(task_id=task_id)


@router.post("/{name}/stop")
async def stop_scenario(name: str):
    # 找到该场景最近一个 running 任务
    running = [
        tid for tid, t in pm._tasks.items()
        if t.get("status") == "running"
    ]
    if not running:
        raise HTTPException(404, "没有正在运行的任务")
    stopped = []
    for tid in running:
        if pm.stop_task(tid):
            stopped.append(tid)
    return {"stopped": stopped}


@router.get("/{name}/status", response_model=TaskStatus)
async def task_status(name: str, task_id: str = Query(...)):
    task = pm.get_task(task_id)
    if task is None:
        return TaskStatus(task_id=task_id, status="not_found")
    return TaskStatus(
        task_id=task_id,
        status=task["status"],
        exit_code=task.get("exit_code"),
        duration_s=task.get("duration_s"),
    )
