"""
ADB 掉线的错误分类（2026-10-21 真机演练发现）。

演练现象
--------
在 `smart_match_greet.py` 运行途中执行 `adb disconnect 10.32.7.135:5555`，
脚本最终报的是：

    ✗ 错误：1 个
      · 搜索失败：商汤

而不是 ADB 断连标记。于是 `_is_adb_disconnect_error()` 返回 False，
`_main_with_adb_recovery()` 的自愈链（disconnect → connect → 重跑 main）
**从未触发**，整轮直接死掉。

根因
----
`_ADB_ERROR_MARKERS` 只有两个来源：
- "ADB 设备未连接"      → P0 早筛（unlock 之前）
- "ADB 设备已断开连接"  → P1 早筛（只在「PIN 已配置 且 屏幕灭」分支里）

其余所有掉线落点（启动等待循环、browse_jobs、职位列表、详情页）产生的都是
通用错误文案（"搜索失败：X" / "职位列表加载超时" / "详情页加载超时"），
不带任何 ADB 语义 → 自愈链看不见。

覆盖
----
- `_device_reachable()`：现场连通性探测（按本轮实际使用的 serial）
- `_append_ui_error()`：报错现场探测并补断连标记
- `_startup_abort_if_disconnected()`：启动等待循环的 fail-fast 守卫
- `_main_with_adb_recovery()`：main() 返回通用错误 + 设备不可达 → 触发重连并重跑
- `_main_with_adb_recovery()`：设备可达 → 不误触发重连
- `_main_with_adb_recovery()`：main() 抛异常 + 设备不可达 → 走重连而不是崩掉
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from scenarios.boss.scripts import smart_match_greet as smg  # noqa: E402


class _FakeADB:
    def __init__(self, connected: bool = True):
        self.connected = connected
        self.queried: list[str | None] = []

    def is_device_connected(self, device_id=None):
        self.queried.append(device_id)
        return self.connected


class TestDeviceReachable:
    def test_uses_given_serial(self, monkeypatch):
        """连通性探测走 `adb -s <serial> get-state`，返回 state == "device"。"""
        fake = _FakeADB(True)
        monkeypatch.setattr(smg, "ADBRunner", lambda: fake)
        assert smg._device_reachable("10.32.7.135:5555") is True
        assert fake.queried == ["10.32.7.135:5555"]

    def test_none_means_any_device(self, monkeypatch):
        """没指定设备时用「任意设备在线」的宽松语义——宁可漏判不误判。"""
        fake = _FakeADB(True)
        monkeypatch.setattr(smg, "ADBRunner", lambda: fake)
        assert smg._device_reachable(None) is True
        assert fake.queried == [None]

    def test_false_when_probe_raises(self, monkeypatch):
        """探测本身炸了（adb 不存在等）→ 视为不可达，不能让自愈链崩掉。"""

        class _Boom:
            def is_device_connected(self, device_id=None):
                raise RuntimeError("adb not found")

        monkeypatch.setattr(smg, "ADBRunner", lambda: _Boom())
        assert smg._device_reachable("x") is False


class TestDeviceReachableStable:
    """连续两次确认才判死——单次探测可能是 adb 抖动的假阴性。"""

    def test_two_failures_means_unreachable(self, monkeypatch):
        fake = _FakeADB(False)
        monkeypatch.setattr(smg, "ADBRunner", lambda: fake)
        assert smg._device_reachable_stable("x") is False
        assert len(fake.queried) == 2

    def test_any_success_means_reachable(self, monkeypatch):
        """任意一次探测成功即判可达（抖动假阴性不会误伤）。"""
        calls = {"n": 0}

        class _Flaky:
            def is_device_connected(self, device_id=None):
                calls["n"] += 1
                return calls["n"] >= 2

        monkeypatch.setattr(smg, "ADBRunner", lambda: _Flaky())
        assert smg._device_reachable_stable("x") is True
        assert calls["n"] == 2


class TestAppendUiError:
    """UI 类错误要在报错现场探测设备，并补 ADB 断连标记。"""

    def test_adds_marker_when_unreachable(self, monkeypatch):
        fake = _FakeADB(False)
        monkeypatch.setattr(smg, "ADBRunner", lambda: fake)
        result = {"errors": [], "device_id": "10.32.7.135:5555"}
        smg._append_ui_error(result, "搜索失败：商汤")
        assert "搜索失败：商汤" in result["errors"]
        assert smg._is_adb_disconnect_error(result["errors"]), "必须补上断连标记"

    def test_no_marker_when_reachable(self, monkeypatch):
        """设备在线时不能加标记——否则业务错误会被误判成断连并整轮重跑。"""
        fake = _FakeADB(True)
        monkeypatch.setattr(smg, "ADBRunner", lambda: fake)
        result = {"errors": [], "device_id": "10.32.7.135:5555"}
        smg._append_ui_error(result, "搜索失败：商汤")
        assert result["errors"] == ["搜索失败：商汤"]
        assert not smg._is_adb_disconnect_error(result["errors"])

    def test_probes_the_actual_device(self, monkeypatch):
        """探测必须针对本轮实际使用的 serial，不能拿 config 里的 ip 猜。"""
        fake = _FakeADB(False)
        monkeypatch.setattr(smg, "ADBRunner", lambda: fake)
        result = {"errors": [], "device_id": "42231JEKB04971"}
        smg._append_ui_error(result, "详情页加载超时")
        # 连续两次确认（_device_reachable_stable），两次都针对同一 serial
        assert fake.queried == ["42231JEKB04971", "42231JEKB04971"]

    def test_idempotent_when_already_marked(self, monkeypatch):
        """已有断连标记时不再重复探测/追加。"""
        fake = _FakeADB(False)
        monkeypatch.setattr(smg, "ADBRunner", lambda: fake)
        result = {"errors": ["ADB 设备已断开连接"], "device_id": "x"}
        smg._append_ui_error(result, "详情页加载超时")
        assert result["errors"] == ["ADB 设备已断开连接", "详情页加载超时"]
        assert fake.queried == [], "已有标记就不该再探测"


class TestStartupGuard:
    """启动等待循环的 fail-fast 守卫（抽成独立函数以便直接驱动）。"""

    def test_aborts_when_disconnected(self):
        class _Skill:
            device_id = "10.32.7.135:5555"
            adb = _FakeADB(False)

        result = {"errors": [], "device_id": "10.32.7.135:5555"}
        assert smg._startup_abort_if_disconnected(_Skill(), result) is True
        assert smg._is_adb_disconnect_error(result["errors"])

    def test_continues_when_connected(self):
        class _Skill:
            device_id = "10.32.7.135:5555"
            adb = _FakeADB(True)

        result = {"errors": [], "device_id": "10.32.7.135:5555"}
        assert smg._startup_abort_if_disconnected(_Skill(), result) is False
        assert result["errors"] == []

    def test_called_in_startup_loop(self):
        """守卫必须真的挂在启动等待循环里（源码级唯一防线）。"""
        src = (ROOT / "scenarios" / "boss" / "scripts" / "smart_match_greet.py").read_text(
            encoding="utf-8"
        )
        assert "_startup_abort_if_disconnected(skill, result)" in src, (
            "启动等待循环必须调用 fail-fast 守卫"
        )


class TestRecoveryTriggersOnUnreachable:
    """main() 报通用错误 + 设备此刻不可达 → 必须触发重连。"""

    @pytest.fixture
    def harness(self, monkeypatch):
        calls = {"main": 0, "reconnect": 0}

        def _fake_main():
            calls["main"] += 1
            if calls["main"] == 1:
                # 第一轮：掉线后 main() 归类成「搜索失败」，没有 ADB 标记
                return 1, {"greeted": [], "skipped": [], "all_scores": [],
                           "errors": ["搜索失败：商汤"],
                           "device_id": "10.32.7.135:5555"}
            return 0, {"greeted": [], "skipped": [], "all_scores": [],
                       "errors": [], "device_id": "10.32.7.135:5555"}

        def _fake_reconnect(ip, timeout=15.0, allow_ip_refresh=True):
            calls["reconnect"] += 1
            return ip

        monkeypatch.setattr(smg, "main", _fake_main)
        monkeypatch.setattr(smg, "_adb_reconnect", _fake_reconnect)
        monkeypatch.setattr(smg, "_device_reachable", lambda *a, **k: False)
        return calls

    def test_triggers_reconnect(self, harness, monkeypatch):
        monkeypatch.setattr(
            sys, "argv", ["smart_match_greet.py", "--keyword", "商汤", "--adb-retry-max", "2"],
        )
        rc = smg._main_with_adb_recovery()
        assert harness["reconnect"] == 1, "设备不可达时必须触发一次重连"
        assert harness["main"] == 2, "重连后必须重跑 main()"
        assert rc == 0

    def test_stops_after_max_attempts(self, harness, monkeypatch):
        monkeypatch.setattr(
            sys, "argv", ["smart_match_greet.py", "--keyword", "商汤", "--adb-retry-max", "2"],
        )
        smg._main_with_adb_recovery()
        assert harness["reconnect"] == 1, "最多重试 adb-retry-max-1 次重连"

    def test_no_reconnect_when_device_reachable(self, harness, monkeypatch):
        """设备可达 → 那是真业务错误（比如搜不到职位），不该重连。"""
        monkeypatch.setattr(smg, "_device_reachable", lambda *a, **k: True)
        monkeypatch.setattr(
            sys, "argv", ["smart_match_greet.py", "--keyword", "商汤"],
        )
        rc = smg._main_with_adb_recovery()
        assert harness["reconnect"] == 0
        assert harness["main"] == 1
        assert rc == 1

    def test_probes_the_device_from_result(self, harness, monkeypatch):
        """探测的 serial 必须来自 result['device_id']（本轮实际用的设备）。"""
        probed = []

        def _probe(device_id=None):
            probed.append(device_id)
            return False

        monkeypatch.setattr(smg, "_device_reachable", _probe)
        monkeypatch.setattr(
            sys, "argv", ["smart_match_greet.py", "--keyword", "商汤", "--adb-retry-max", "1"],
        )
        smg._main_with_adb_recovery()
        assert probed and probed[0] == "10.32.7.135:5555"


class TestRecoveryOnException:
    """main() 抛异常时不能直接崩掉——ADB 掉了要走自愈链。"""

    def test_exception_with_unreachable_device_triggers_reconnect(self, monkeypatch):
        calls = {"main": 0, "reconnect": 0}

        def _boom():
            calls["main"] += 1
            if calls["main"] == 1:
                raise RuntimeError("adb shell timed out")
            return 0, {"greeted": [], "skipped": [], "all_scores": [], "errors": []}

        monkeypatch.setattr(smg, "main", _boom)
        monkeypatch.setattr(
            smg, "_adb_reconnect", lambda ip, **kw: (calls.__setitem__("reconnect", calls["reconnect"] + 1), ip)[1]
        )
        monkeypatch.setattr(smg, "_device_reachable", lambda *a, **k: False)
        monkeypatch.setattr(
            sys, "argv", ["smart_match_greet.py", "--keyword", "商汤", "--adb-retry-max", "2"],
        )
        rc = smg._main_with_adb_recovery()
        assert calls["reconnect"] == 1, "异常 + 设备不可达 → 应走重连"
        assert rc == 0

    def test_exception_with_reachable_device_reraises(self, monkeypatch):
        """设备还在线 → 是真 bug，原样抛出，不要被自愈链吞掉。"""

        def _boom():
            raise RuntimeError("真 bug")

        monkeypatch.setattr(smg, "main", _boom)
        monkeypatch.setattr(smg, "_device_reachable", lambda *a, **k: True)
        monkeypatch.setattr(
            sys, "argv", ["smart_match_greet.py", "--keyword", "商汤"],
        )
        with pytest.raises(RuntimeError, match="真 bug"):
            smg._main_with_adb_recovery()
