"""
PixelClawAgent: 基于 Claude tool_use 的 AI 编排层

Agent 接收自然语言任务，通过 tool_use 自主决定：
- 检查当前 DB 状态（check_boss_stats）
- 查询设备（get_device_status）
- 读取最近打招呼记录（get_recent_greetings）
- 读取/更新配置（get_config / update_config）
- 运行爬取脚本（run_boss_scrape）
- 运行智能打招呼（run_smart_greet）

日志通过 broadcast 函数实时推送到 WS /ws/task/{task_id}。
"""

import asyncio
import json
import sys
import time as _time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from api.services import llm_client as llm
from api.services import db_reader
from api.services import process_manager as pm

_PRIMARY, _FALLBACK = None, None

PRIMARY_MODEL = "MiniMax-M3"
FALLBACK_MODEL = "claude-haiku-4-5-20251001"
MAX_STEPS = 20
SCENARIOS_DIR = str(REPO_ROOT / "scenarios" / "boss" / "scripts")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(level: str, text: str) -> dict:
    return {"type": "log", "level": level, "text": text, "ts": _now_iso()}


def _done(exit_code: int = 0, duration_s: float = 0.0) -> dict:
    return {"type": "done", "exit_code": exit_code, "duration_s": duration_s}


# ──────────────────────────────────────────────────────────────────────────────
# Claude tool definitions
# ──────────────────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "check_boss_stats",
        "description": "查询 Boss直聘数据库：今日打招呼数、今日浏览数、历史总计、平均评分、热门关键词。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_device_status",
        "description": "查询 ADB 设备连接状态：是否有设备在线、设备序列号和型号。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_recent_greetings",
        "description": "查询最近的打招呼记录，包括公司名、职位、评分、发送时间。",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "返回条数，默认 10，最多 50。",
                    "default": 10,
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_config",
        "description": "读取配置文件内容。file_key 可选: boss-keywords, candidate-profile, settings。",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_key": {
                    "type": "string",
                    "enum": ["boss-keywords", "candidate-profile", "settings"],
                    "description": "配置文件标识",
                }
            },
            "required": ["file_key"],
        },
    },
    {
        "name": "update_config",
        "description": "更新 Boss 关键词列表或候选人画像权重。data 为 YAML/JSON 字符串。",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_key": {
                    "type": "string",
                    "enum": ["boss-keywords", "candidate-profile"],
                },
                "data": {
                    "type": "string",
                    "description": "新的配置内容（YAML 或 JSON 格式字符串）",
                },
            },
            "required": ["file_key", "data"],
        },
    },
    {
        "name": "run_boss_scrape",
        "description": "运行 Boss直聘爬取脚本，抓取新职位并存入数据库。需要设备连接。大约需要 5-10 分钟。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "run_smart_greet",
        "description": "运行智能匹配打招呼脚本：对数据库中评分较高的职位发送打招呼。需要设备连接。",
        "input_schema": {
            "type": "object",
            "properties": {
                "max_greet": {
                    "type": "integer",
                    "description": "本次最多发送打招呼数，默认 10。",
                    "default": 10,
                }
            },
            "required": [],
        },
    },
]

SYSTEM_PROMPT = """你是 PixelClaw 的 AI 助手，负责编排 Boss直聘自动化任务。

你拥有以下工具：
- check_boss_stats: 查看今日/历史统计数据
- get_device_status: 检查手机是否已连接
- get_recent_greetings: 查看最近打招呼记录
- get_config / update_config: 读写关键词和评分配置
- run_boss_scrape: 爬取新职位（耗时较长）
- run_smart_greet: 智能评分并发送打招呼

工作原则：
1. 先用轻量工具（check_boss_stats / get_device_status）了解当前状态，再决定是否运行重型工具
2. 如果今天已经爬取过足够多的职位，可以直接运行 run_smart_greet 跳过爬取
3. 如果设备未连接，不要运行 run_boss_scrape 或 run_smart_greet，改为报告状态
4. 完成后给出清晰的中文摘要，包括执行了什么操作和结果
5. 用中文思考和回复"""


# ──────────────────────────────────────────────────────────────────────────────
# Lightweight tool implementations (sync, run in executor)
# ──────────────────────────────────────────────────────────────────────────────

def _exec_check_boss_stats(_inp: dict) -> str:
    try:
        return json.dumps(db_reader.get_boss_stats(), ensure_ascii=False)
    except Exception as e:
        return f"查询失败: {e}"


def _exec_get_device_status(_inp: dict) -> str:
    try:
        from monitors.adb_manager import ADBManager
        adb = ADBManager()
        adb_ok = adb.is_adb_available()
        devices = adb.list_devices() if adb_ok else []
        return json.dumps({
            "adb_available": adb_ok,
            "devices": [
                {"serial": d.serial, "status": d.status,
                 "model": d.model, "is_connected": d.is_connected}
                for d in devices
            ],
        }, ensure_ascii=False)
    except Exception as e:
        return f"查询设备状态失败: {e}"


def _exec_get_recent_greetings(inp: dict) -> str:
    try:
        limit = min(int(inp.get("limit", 10)), 50)
        rows, total = db_reader.list_rows(
            "greetings", page=1, page_size=limit,
            order_by="id", order_dir="DESC",
        )
        return json.dumps({"total": total, "rows": rows}, ensure_ascii=False, default=str)
    except Exception as e:
        return f"查询失败: {e}"


def _exec_get_config(inp: dict) -> str:
    key = inp.get("file_key", "")
    paths = {
        "boss-keywords":     REPO_ROOT / "scenarios/boss/config/keywords.yaml",
        "candidate-profile": REPO_ROOT / "scenarios/boss/config/candidate_profile.yaml",
        "settings":          REPO_ROOT / "config/settings.yaml",
    }
    path = paths.get(key)
    if not path:
        return f"未知 file_key: {key}"
    if not path.exists():
        return f"文件不存在: {path}"
    return path.read_text(encoding="utf-8")


def _exec_update_config(inp: dict) -> str:
    key = inp.get("file_key", "")
    data = inp.get("data", "")
    paths = {
        "boss-keywords":     REPO_ROOT / "scenarios/boss/config/keywords.yaml",
        "candidate-profile": REPO_ROOT / "scenarios/boss/config/candidate_profile.yaml",
    }
    path = paths.get(key)
    if not path:
        return f"未知 file_key: {key}"
    bak = path.with_suffix(".yaml.bak")
    if path.exists():
        bak.write_bytes(path.read_bytes())
    path.write_text(data, encoding="utf-8")
    return f"已更新 {path.name}"


# ──────────────────────────────────────────────────────────────────────────────
# Agent class
# ──────────────────────────────────────────────────────────────────────────────

class PixelClawAgent:
    """Claude tool_use agentic loop。"""

    def __init__(self) -> None:
        global _PRIMARY, _FALLBACK
        if _PRIMARY is None:
            _PRIMARY, _FALLBACK = llm.make_client()
        self._primary = _PRIMARY
        self._fallback = _FALLBACK

    async def run(
        self,
        task: str,
        task_id: str,
        broadcast: Callable,
    ) -> None:
        start = _time.monotonic()
        pm._tasks[task_id] = {"status": "running", "start_ts": start, "logs": []}

        try:
            await self._loop(task, task_id, broadcast)
        except Exception as e:
            await broadcast(task_id, _log("ERROR", f"[Agent] 未预期错误: {e}"))
        finally:
            elapsed = _time.monotonic() - start
            pm._tasks[task_id].update({"status": "done", "duration_s": round(elapsed, 1)})
            await broadcast(task_id, _done(exit_code=0, duration_s=round(elapsed, 1)))

    async def _loop(self, task: str, task_id: str, broadcast: Callable) -> None:
        messages = [{"role": "user", "content": task}]

        for step in range(MAX_STEPS):
            # 调用 Claude（同步 SDK，放到 executor 避免阻塞事件循环）
            _model = PRIMARY_MODEL
            _fallback_model = FALLBACK_MODEL
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: llm.call_with_fallback(
                    self._primary,
                    self._fallback,
                    fallback_model=_fallback_model,
                    model=_model,
                    max_tokens=2048,
                    system=SYSTEM_PROMPT,
                    tools=TOOLS,
                    messages=messages,
                ),
            )

            # 输出推理文字
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    await broadcast(task_id, _log("INFO", f"[思考] {block.text.strip()}"))

            tool_calls = [b for b in response.content if b.type == "tool_use"]

            if response.stop_reason == "end_turn" or not tool_calls:
                break

            # 执行所有工具调用
            tool_results = []
            for tc in tool_calls:
                await broadcast(task_id, _log("INFO", f"[工具] {tc.name}"))
                result = await self._execute_tool(tc.name, tc.input, task_id, broadcast)
                short = result[:300] + ("…" if len(result) > 300 else "")
                await broadcast(task_id, _log("INFO", f"[结果] {short}"))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": result,
                })

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

        else:
            await broadcast(task_id, _log("WARNING", f"[Agent] 已达最大步数 {MAX_STEPS}，强制结束。"))

    async def _execute_tool(
        self,
        name: str,
        inp: dict,
        task_id: str,
        broadcast: Callable,
    ) -> str:
        loop = asyncio.get_event_loop()

        if name == "check_boss_stats":
            return await loop.run_in_executor(None, _exec_check_boss_stats, inp)
        if name == "get_device_status":
            return await loop.run_in_executor(None, _exec_get_device_status, inp)
        if name == "get_recent_greetings":
            return await loop.run_in_executor(None, _exec_get_recent_greetings, inp)
        if name == "get_config":
            return await loop.run_in_executor(None, _exec_get_config, inp)
        if name == "update_config":
            return await loop.run_in_executor(None, _exec_update_config, inp)
        if name == "run_boss_scrape":
            return await self._run_subprocess(
                ["python", "-u", "scrape_job_details.py"], task_id, broadcast,
            )
        if name == "run_smart_greet":
            max_greet = inp.get("max_greet", 10)
            return await self._run_subprocess(
                ["python", "-u", "smart_match_greet.py", "--max-greet", str(max_greet)],
                task_id, broadcast,
            )
        return f"未知工具: {name}"

    async def _run_subprocess(
        self,
        cmd: list[str],
        task_id: str,
        broadcast: Callable,
    ) -> str:
        """直接运行 subprocess 并把 stdout 实时桥接到父 task 的 WS 流。"""
        start = _time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=SCENARIOS_DIR,
            )
            while True:
                line_bytes = await proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").rstrip()
                if line:
                    level = "ERROR" if any(w in line for w in ("ERROR", "Error", "Traceback")) else "INFO"
                    await broadcast(task_id, _log(level, f"[subprocess] {line}"))

            await proc.wait()
            elapsed = round(_time.monotonic() - start, 1)
            if proc.returncode == 0:
                return f"成功完成，耗时 {elapsed} 秒"
            return f"退出码 {proc.returncode}，耗时 {elapsed} 秒，请查看上方日志"
        except Exception as e:
            return f"子进程启动失败: {e}"
