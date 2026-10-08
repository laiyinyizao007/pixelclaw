"""agent.py 工具层单测：_exec_* 实现、_execute_tool 派发、run() 生命周期、子进程桥接。

这些代码与 LLM 协议无关，因此独立于 test_agent_loop.py 的循环测试。
"""

import json
import sys

import pytest

from api.services import agent as ag
from api.services import process_manager as pm


@pytest.fixture
def broadcast():
    events = []

    async def _broadcast(task_id, payload):
        events.append(payload)

    _broadcast.events = events
    return _broadcast


# --------------------------------------------------------------------------
# 轻量工具实现
# --------------------------------------------------------------------------


def test_check_boss_stats_serializes_db_payload(monkeypatch):
    monkeypatch.setattr(ag.db_reader, "get_boss_stats", lambda: {"today": 3, "kw": "算法"})
    assert json.loads(ag._exec_check_boss_stats({})) == {"today": 3, "kw": "算法"}


def test_check_boss_stats_reports_db_failure_as_text(monkeypatch):
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(ag.db_reader, "get_boss_stats", _boom)
    assert ag._exec_check_boss_stats({}) == "查询失败: db down"


def test_get_recent_greetings_caps_limit_at_50(monkeypatch):
    seen = {}

    def _list_rows(table, page, page_size, order_by, order_dir):
        seen.update(table=table, page_size=page_size, order_dir=order_dir)
        return [{"company": "A"}], 1

    monkeypatch.setattr(ag.db_reader, "list_rows", _list_rows)

    payload = json.loads(ag._exec_get_recent_greetings({"limit": 999}))

    assert seen == {"table": "greetings", "page_size": 50, "order_dir": "DESC"}
    assert payload == {"total": 1, "rows": [{"company": "A"}]}


def test_get_recent_greetings_defaults_to_10(monkeypatch):
    captured = {}

    def _list_rows(table, page, page_size, order_by, order_dir):
        captured["page_size"] = page_size
        return [], 0

    monkeypatch.setattr(ag.db_reader, "list_rows", _list_rows)
    ag._exec_get_recent_greetings({})
    assert captured["page_size"] == 10


def test_get_recent_greetings_reports_failure_as_text(monkeypatch):
    def _boom(*a, **k):
        raise ValueError("bad sql")

    monkeypatch.setattr(ag.db_reader, "list_rows", _boom)
    assert ag._exec_get_recent_greetings({}) == "查询失败: bad sql"


def test_get_device_status_reports_adb_unavailable(monkeypatch):
    class _ADB:
        def is_adb_available(self):
            return False

        def list_devices(self):  # pragma: no cover - 不应被调用
            raise AssertionError("adb 不可用时不得枚举设备")

    monkeypatch.setitem(
        sys.modules, "monitors.adb_manager", type(sys)("monitors.adb_manager")
    )
    sys.modules["monitors.adb_manager"].ADBManager = _ADB

    payload = json.loads(ag._exec_get_device_status({}))
    assert payload == {"adb_available": False, "devices": []}


def test_get_device_status_lists_connected_devices(monkeypatch):
    class _Device:
        serial, status, model, is_connected = "SN1", "device", "Pixel", True

    class _ADB:
        def is_adb_available(self):
            return True

        def list_devices(self):
            return [_Device()]

    monkeypatch.setitem(
        sys.modules, "monitors.adb_manager", type(sys)("monitors.adb_manager")
    )
    sys.modules["monitors.adb_manager"].ADBManager = _ADB

    payload = json.loads(ag._exec_get_device_status({}))
    assert payload["adb_available"] is True
    assert payload["devices"] == [
        {"serial": "SN1", "status": "device", "model": "Pixel", "is_connected": True}
    ]


def test_get_device_status_reports_import_failure_as_text(monkeypatch):
    monkeypatch.setitem(sys.modules, "monitors.adb_manager", None)
    assert "查询设备状态失败" in ag._exec_get_device_status({})


# --------------------------------------------------------------------------
# 配置读写
# --------------------------------------------------------------------------


def test_get_config_rejects_unknown_key():
    assert ag._exec_get_config({"file_key": "nope"}) == "未知 file_key: nope"


def test_get_config_reports_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(ag, "REPO_ROOT", tmp_path)
    assert ag._exec_get_config({"file_key": "settings"}).startswith("文件不存在: ")


def test_get_config_reads_utf8_content(monkeypatch, tmp_path):
    target = tmp_path / "config" / "settings.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("名称: 测试\n", encoding="utf-8")
    monkeypatch.setattr(ag, "REPO_ROOT", tmp_path)

    assert ag._exec_get_config({"file_key": "settings"}) == "名称: 测试\n"


def test_update_config_rejects_unknown_key():
    assert ag._exec_update_config({"file_key": "settings"}) == "未知 file_key: settings"


def test_update_config_backs_up_before_overwrite(monkeypatch, tmp_path):
    target = tmp_path / "scenarios" / "boss" / "config" / "keywords.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("old: 1\n", encoding="utf-8")
    monkeypatch.setattr(ag, "REPO_ROOT", tmp_path)

    result = ag._exec_update_config({"file_key": "boss-keywords", "data": "new: 2\n"})

    assert result == "已更新 keywords.yaml"
    assert target.read_text(encoding="utf-8") == "new: 2\n"
    assert target.with_suffix(".yaml.bak").read_text(encoding="utf-8") == "old: 1\n"


def test_update_config_creates_file_without_backup_when_absent(monkeypatch, tmp_path):
    target = tmp_path / "scenarios" / "boss" / "config" / "candidate_profile.yaml"
    target.parent.mkdir(parents=True)
    monkeypatch.setattr(ag, "REPO_ROOT", tmp_path)

    ag._exec_update_config({"file_key": "candidate-profile", "data": "w: 1\n"})

    assert target.read_text(encoding="utf-8") == "w: 1\n"
    assert not target.with_suffix(".yaml.bak").exists()


# --------------------------------------------------------------------------
# _execute_tool 派发
# --------------------------------------------------------------------------


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setattr(ag, "_PROVIDERS", [("client", "provider")])
    return ag.PixelClawAgent()


def test_agent_reuses_cached_providers(monkeypatch):
    monkeypatch.setattr(ag, "_PROVIDERS", None)
    monkeypatch.setattr(ag.llm, "make_client", lambda: ["built-once"])

    first = ag.PixelClawAgent()
    monkeypatch.setattr(
        ag.llm, "make_client", lambda: pytest.fail("已缓存后不得重新构造 client")
    )
    second = ag.PixelClawAgent()

    assert first._providers == second._providers == ["built-once"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name, func_name",
    [
        ("check_boss_stats", "_exec_check_boss_stats"),
        ("get_device_status", "_exec_get_device_status"),
        ("get_recent_greetings", "_exec_get_recent_greetings"),
        ("get_config", "_exec_get_config"),
        ("update_config", "_exec_update_config"),
    ],
)
async def test_execute_tool_dispatches_light_tools(
    agent, broadcast, monkeypatch, name, func_name
):
    monkeypatch.setattr(ag, func_name, lambda inp: f"ran::{func_name}::{inp['k']}")

    result = await agent._execute_tool(name, {"k": "v"}, "t1", broadcast)

    assert result == f"ran::{func_name}::v"


@pytest.mark.asyncio
async def test_execute_tool_reports_unknown_tool(agent, broadcast):
    assert await agent._execute_tool("nope", {}, "t1", broadcast) == "未知工具: nope"


@pytest.mark.asyncio
async def test_run_boss_scrape_invokes_scrape_script(agent, broadcast, monkeypatch):
    captured = {}

    async def _fake(self, cmd, task_id, bc):
        captured["cmd"] = cmd
        return "ok"

    monkeypatch.setattr(ag.PixelClawAgent, "_run_subprocess", _fake)

    assert await agent._execute_tool("run_boss_scrape", {}, "t1", broadcast) == "ok"
    assert captured["cmd"] == ["python", "-u", "scrape_job_details.py"]


@pytest.mark.asyncio
async def test_run_smart_greet_passes_max_greet_through(agent, broadcast, monkeypatch):
    captured = {}

    async def _fake(self, cmd, task_id, bc):
        captured["cmd"] = cmd
        return "ok"

    monkeypatch.setattr(ag.PixelClawAgent, "_run_subprocess", _fake)

    await agent._execute_tool("run_smart_greet", {"max_greet": 3}, "t1", broadcast)
    assert captured["cmd"] == [
        "python", "-u", "smart_match_greet.py", "--max-greet", "3",
    ]

    await agent._execute_tool("run_smart_greet", {}, "t1", broadcast)
    assert captured["cmd"][-1] == "10"  # 默认值


# --------------------------------------------------------------------------
# _run_subprocess：stdout 桥接与退出码
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_subprocess_bridges_stdout_and_reports_success(agent, broadcast):
    result = await agent._run_subprocess(
        [sys.executable, "-u", "-c", "print('hello'); print('')"], "t1", broadcast
    )

    assert result.startswith("成功完成，耗时 ")
    assert [e["text"] for e in broadcast.events] == ["[subprocess] hello"]


@pytest.mark.asyncio
async def test_run_subprocess_marks_error_lines_and_nonzero_exit(agent, broadcast):
    result = await agent._run_subprocess(
        [sys.executable, "-u", "-c", "print('Traceback boom'); raise SystemExit(2)"],
        "t1",
        broadcast,
    )

    assert result.startswith("退出码 2，")
    assert broadcast.events[0]["level"] == "ERROR"


@pytest.mark.asyncio
async def test_run_subprocess_reports_launch_failure(agent, broadcast):
    result = await agent._run_subprocess(["definitely-not-a-binary"], "t1", broadcast)
    assert result.startswith("子进程启动失败: ")


# --------------------------------------------------------------------------
# run()：任务状态机与 done 事件
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_marks_task_done_and_emits_done_event(agent, broadcast, monkeypatch):
    async def _loop(self, task, task_id, bc):
        await bc(task_id, ag._log("INFO", "工作中"))

    monkeypatch.setattr(ag.PixelClawAgent, "_loop", _loop)

    await agent.run("干活", "t-done", broadcast)

    assert pm._tasks["t-done"]["status"] == "done"
    assert broadcast.events[-1] == {
        "type": "done",
        "exit_code": 0,
        "duration_s": pm._tasks["t-done"]["duration_s"],
    }


@pytest.mark.asyncio
async def test_run_converts_loop_exception_into_error_log(agent, broadcast, monkeypatch):
    async def _loop(self, task, task_id, bc):
        raise RuntimeError("provider 全挂")

    monkeypatch.setattr(ag.PixelClawAgent, "_loop", _loop)

    await agent.run("干活", "t-err", broadcast)

    errors = [e for e in broadcast.events if e.get("level") == "ERROR"]
    assert errors[0]["text"] == "[Agent] 未预期错误: provider 全挂"
    # 即使失败也要收尾，否则前端会一直转圈
    assert pm._tasks["t-err"]["status"] == "done"
    assert broadcast.events[-1]["type"] == "done"
