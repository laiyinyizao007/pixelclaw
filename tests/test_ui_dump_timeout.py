"""uiautomator dump 挂死的防护回归测试（2026-10-22）。

背景：2026-10-22 的 120 次 sweep 在第 5 条招呼发送后卡死 15 分钟。根因是设备端
`uiautomator dump` 进程停在 `R` 状态不退出（Boss 聊天页动画/webview 永不到 idle），
而本地 `ADBRunner.shell()` 的 `subprocess.run` **没有 timeout** —— 整个脚本无限期
阻塞，连 `wait_for_element` 自己的超时都救不了。

修复要点（被本文件逐条锁住）：
1. `shell()` 支持 timeout，超时返回 `(False, "TIMEOUT")` 而不是抛异常
2. `get_ui_hierarchy()` 的 dump 必须带超时
3. dump 超时/失败后必须杀掉设备端残留的 uiautomator 进程
4. dump 失败后**不能**去 cat `window_dump.xml` —— 那是上一轮的陈旧 XML，
   读回来会让 `wait_for_element` 误判元素存在
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from skills.android.adb_runner import ADBRunner  # noqa: E402
from skills.android.android_skill import AndroidSkill  # noqa: E402


class FakeADB:
    """记录调用序列的假 ADB。"""

    def __init__(self, dump_result=(True, ""), cat_result=(True, "<hierarchy/>"),
                 supports_timeout=True):
        self.calls = []
        self.dump_result = dump_result
        self.cat_result = cat_result
        self.supports_timeout = supports_timeout

    def shell(self, cmd, device_id=None, timeout=None):
        self.calls.append(("shell", cmd, timeout))
        if "uiautomator dump" in cmd:
            if not self.supports_timeout and timeout is not None:
                raise TypeError("shell() got an unexpected keyword argument 'timeout'")
            return self.dump_result
        if "killall" in cmd:
            return True, ""
        if "cat " in cmd:
            return self.cat_result
        return True, ""


class NoTimeoutADB(FakeADB):
    """模拟 monitors.ADBManager —— shell() 不接受 timeout 参数。"""

    def __init__(self, **kw):
        super().__init__(supports_timeout=False, **kw)


def _skill(adb):
    return AndroidSkill(device_id="10.32.7.142:5555", adb=adb)


class TestShellTimeout:
    def test_timeout_returns_failure_marker(self):
        """超时必须返回 (False, "TIMEOUT")，不能让异常冒到调用方。"""
        runner = ADBRunner()
        # 用一个必然睡过头的命令验证超时分支（sleep 5 > timeout 0.5）
        ok, out = runner.shell("shell sleep 5", "10.32.7.142:5555", timeout=0.5)
        assert ok is False
        assert out == "TIMEOUT"

    def test_shell_signature_accepts_timeout(self):
        import inspect
        params = inspect.signature(ADBRunner.shell).parameters
        assert "timeout" in params
        assert params["timeout"].default is None


class TestGetUIHierarchy:
    def test_dump_uses_timeout(self):
        adb = FakeADB()
        _skill(adb).get_ui_hierarchy(force_refresh=True)
        dump_calls = [c for c in adb.calls if "uiautomator dump" in c[1]]
        assert dump_calls, "必须真的发起一次 dump"
        assert dump_calls[0][2] is not None, "dump 必须带 timeout，否则会永久阻塞"

    def test_dump_timeout_returns_empty(self):
        adb = FakeADB(dump_result=(False, "TIMEOUT"))
        assert _skill(adb).get_ui_hierarchy(force_refresh=True) == ""

    def test_dump_timeout_kills_device_uiautomator(self):
        """本地超时不会自动清掉设备端进程，必须显式 killall。"""
        adb = FakeADB(dump_result=(False, "TIMEOUT"))
        _skill(adb).get_ui_hierarchy(force_refresh=True)
        assert any("killall" in c[1] for c in adb.calls), (
            "超时后必须 killall -9 uiautomator，否则后续 dump 全堵死"
        )

    def test_dump_failure_does_not_cat_stale_xml(self):
        """dump 失败时 cat 到的是上一轮的陈旧 XML，会让 wait_for_element 误判。"""
        adb = FakeADB(dump_result=(False, "ERROR: could not get idle state."),
                      cat_result=(True, "<stale/>"))
        assert _skill(adb).get_ui_hierarchy(force_refresh=True) == ""
        assert not any("cat " in c[1] for c in adb.calls), (
            "dump 失败后不应再 cat window_dump.xml"
        )

    def test_dump_success_returns_xml(self):
        adb = FakeADB(dump_result=(True, ""), cat_result=(True, "<hierarchy>ok</hierarchy>"))
        assert _skill(adb).get_ui_hierarchy(force_refresh=True) == "<hierarchy>ok</hierarchy>"

    def test_cached_dump_skips_adb(self):
        adb = FakeADB()
        skill = _skill(adb)
        skill.get_ui_hierarchy(force_refresh=True)
        calls_after_first = len(adb.calls)
        skill.get_ui_hierarchy()
        assert len(adb.calls) == calls_after_first, "未 force_refresh 时应走缓存"

    def test_failed_dump_does_not_poison_cache(self):
        """失败后 last_ui_dump 必须为空，下次调用要重新 dump。"""
        adb = FakeADB(dump_result=(False, "TIMEOUT"))
        skill = _skill(adb)
        skill.get_ui_hierarchy(force_refresh=True)
        before = len(adb.calls)
        skill.get_ui_hierarchy()
        assert len(adb.calls) > before, "失败后不得把空结果当缓存复用"


class TestAdbCompat:
    def test_falls_back_when_backend_lacks_timeout(self):
        """注入 monitors.ADBManager 这类不支持 timeout 的实现时不能崩。"""
        adb = NoTimeoutADB(dump_result=(True, ""))
        skill = _skill(adb)
        assert skill.get_ui_hierarchy(force_refresh=True) == "<hierarchy/>"

    def test_fallback_still_kills_stuck_uiautomator(self):
        adb = NoTimeoutADB(dump_result=(False, "boom"))
        skill = _skill(adb)
        skill.get_ui_hierarchy(force_refresh=True)
        assert any("killall" in c[1] for c in adb.calls)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
