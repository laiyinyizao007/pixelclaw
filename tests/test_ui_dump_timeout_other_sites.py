"""其余 `uiautomator dump` 调用点的挂死防护（2026-10-22 续）。

`tests/test_ui_dump_timeout.py` 已经把 `skills/android/android_skill.py` 的
dump 超时 + killall + 不 cat 陈旧 XML 三件事钉死。但全仓还有 4 处 dump
**没有**这些保护，同一个坑会从别的入口再踩一次：

| 调用点 | 原问题 |
|---|---|
| `skills/linkedin/linkedin_automation_skill.py` | 覆写了 `get_ui_hierarchy()`，dump 无超时；且 dump 失败后仍 cat → 读到上一轮陈旧 XML |
| `core/vision_agent.py` | SoM 标注前的 `uiautomator dump && cat` 无超时，adb 是 ADBManager 时靠 30s 默认值兜，ADBRunner 时无限阻塞 |
| `scenarios/wecom/scripts/probe_elements.py` | dump 无超时；失败后仍 cat 陈旧 XML |
| `scenarios/xhs/tasks/ui_automator_task.py` / `scenarios/xhs/scripts/xhs_auto_fav.py` | 裸 `subprocess.run(shell=True)`，无 timeout，dump 挂死 = 脚本永久阻塞 |

约束（与基座一致）：
1. dump 必须带 timeout
2. dump 失败/超时 → 不 cat 陈旧 XML
3. 能清设备端残留 uiautomator 的地方要清
"""
import ast
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from skills.linkedin.linkedin_automation_skill import LinkedInAutomationSkill  # noqa: E402


class FakeADB:
    """记录调用序列的假 ADB。"""

    def __init__(self, dump_result=(True, ""), cat_result=(True, "<hierarchy/>")):
        self.calls = []
        self.dump_result = dump_result
        self.cat_result = cat_result

    def shell(self, cmd, device_id=None, timeout=None):
        self.calls.append(("shell", cmd, timeout))
        if "uiautomator dump" in cmd:
            return self.dump_result
        if "killall" in cmd:
            return True, ""
        if "cat " in cmd:
            return self.cat_result
        return True, ""

    def type_text(self, text, device_id=None):
        return True


class TestLinkedInSkill:
    """LinkedInAutomationSkill 覆写了基座的 get_ui_hierarchy，绕过了基座的保护。"""

    def _skill(self, adb):
        # LinkedInAutomationSkill 的第一个位置参数是 adb_manager
        return LinkedInAutomationSkill(adb_manager=adb, device_id="dev")

    def test_dump_uses_timeout(self):
        adb = FakeADB()
        self._skill(adb).get_ui_hierarchy()
        dump_calls = [c for c in adb.calls if "uiautomator dump" in c[1]]
        assert dump_calls, "必须真的发起一次 dump"
        assert dump_calls[0][2] is not None, "dump 必须带 timeout"

    def test_dump_failure_does_not_cat_stale_xml(self):
        adb = FakeADB(dump_result=(False, "ERROR: could not get idle state."),
                      cat_result=(True, "<stale/>"))
        assert self._skill(adb).get_ui_hierarchy() == ""
        assert not any("cat " in c[1] for c in adb.calls), (
            "dump 失败后 cat 到的是上一轮陈旧 XML"
        )

    def test_dump_timeout_kills_device_uiautomator(self):
        adb = FakeADB(dump_result=(False, "TIMEOUT"))
        self._skill(adb).get_ui_hierarchy()
        assert any("killall" in c[1] for c in adb.calls)


class TestVisionAgent:
    """`core/vision_agent.py` 的导入链要 PIL + cv2，测试环境装不上，
    所以这里直接按 AST 取源码断言，不 import 整个模块。"""

    @staticmethod
    def _source():
        return (ROOT / "core/vision_agent.py").read_text("utf-8")

    def test_shell_call_has_timeout(self):
        """SoM 标注前的 dump 必须带 timeout。"""
        src = self._source()
        assert "uiautomator dump" in src
        tree = ast.parse(src)
        dumps_with_timeout = False
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "_shell_with_timeout":
                continue
            kw = {k.arg for k in node.keywords}
            if "timeout" not in kw:
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and "uiautomator dump" in str(arg.value):
                    dumps_with_timeout = True
        assert dumps_with_timeout, "vision_agent 的 uiautomator dump 调用必须带 timeout"

    def test_helper_falls_back_when_backend_lacks_timeout(self):
        """注入的 adb 实现不支持 timeout 参数时不能崩。"""
        tree = ast.parse(self._source())
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_shell_with_timeout")
        ns: dict = {"Tuple": tuple}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "<vision_agent>", "exec"), ns)

        class NoTimeoutADB:
            def __init__(self):
                self.calls = []

            def shell(self, command, timeout=None):
                if timeout is not None:
                    raise TypeError("shell() got an unexpected keyword argument 'timeout'")
                self.calls.append((command, timeout))
                return True, "<xml/>"

        class Holder:
            pass

        holder = Holder()
        holder.adb = NoTimeoutADB()
        ok, out = ns["_shell_with_timeout"](holder, "uiautomator dump /sdcard/x.xml", 15)
        assert ok is True and out == "<xml/>"
        assert holder.adb.calls == [("uiautomator dump /sdcard/x.xml", None)]

    def test_adb_manager_shell_exposes_timeout(self):
        """monitors.ADBManager.shell() 要能透传 timeout，否则调用方只能瞎等。"""
        from monitors.adb_manager import ADBManager
        params = inspect.signature(ADBManager.shell).parameters
        assert "timeout" in params, "ADBManager.shell 必须支持 timeout 透传"


class TestProbeElements:
    def test_dump_has_timeout_and_no_stale_cat(self):
        src = (ROOT / "scenarios/wecom/scripts/probe_elements.py").read_text("utf-8")
        assert "timeout=" in src, "dump 必须带 timeout"
        # dump 的返回值要判 success，失败就不该 cat
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_dump_xml")
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
        dump_call = next(c for c in calls
                         if isinstance(c.func, ast.Attribute)
                         and c.func.attr == "shell"
                         and any(isinstance(a, ast.Constant) and "uiautomator dump" in str(a.value)
                                 for a in c.args))
        kw = {k.arg for k in dump_call.keywords}
        assert "timeout" in kw, "probe_elements 的 dump 必须带 timeout"


class TestXhsScripts:
    """裸 subprocess 的两个 xhs 脚本：dump 必须有 timeout，否则永久阻塞。"""

    def _subprocess_runs(self, rel_path):
        """找出文件里所有 `subprocess.run(...)` 调用。"""
        src = (ROOT / rel_path).read_text("utf-8")
        tree = ast.parse(src)
        out = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "run":
                continue
            owner = node.func.value
            if not (isinstance(owner, ast.Name) and owner.id == "subprocess"):
                continue
            out.append(node)
        return out

    @pytest.mark.parametrize("rel_path", [
        "scenarios/xhs/tasks/ui_automator_task.py",
        "scenarios/xhs/scripts/xhs_auto_fav.py",
    ])
    def test_dump_command_present(self, rel_path):
        src = (ROOT / rel_path).read_text("utf-8")
        assert "uiautomator dump" in src, f"{rel_path} 应当有 uiautomator dump 调用"

    @pytest.mark.parametrize("rel_path", [
        "scenarios/xhs/tasks/ui_automator_task.py",
        "scenarios/xhs/scripts/xhs_auto_fav.py",
    ])
    def test_subprocess_run_has_timeout(self, rel_path):
        calls = self._subprocess_runs(rel_path)
        assert calls, f"{rel_path} 应当有 subprocess.run 调用"
        for call in calls:
            kw = {k.arg for k in call.keywords}
            assert "timeout" in kw, (
                f"{rel_path} 的 subprocess.run 缺 timeout，dump 挂死时会永久阻塞"
            )


class TestBossSkillBroadcast:
    """BOSS 发消息的 ADBKeyboard 广播也是裸 subprocess.run——挂死会卡住整轮。"""

    def test_broadcast_subprocess_has_timeout(self):
        src = (ROOT / "skills/boss/boss_automation_skill.py").read_text("utf-8")
        tree = ast.parse(src)
        runs = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "run"
            and isinstance(n.func.value, ast.Name) and n.func.value.id == "subprocess"
        ]
        assert runs, "应当有 subprocess.run 调用"
        for call in runs:
            kw = {k.arg for k in call.keywords}
            assert "timeout" in kw, "BOSS 广播的 subprocess.run 缺 timeout，挂死会卡住整轮"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
