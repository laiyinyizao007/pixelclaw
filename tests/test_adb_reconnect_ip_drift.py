"""
ADB 断连重连的 IP 漂移恢复（2026-10-21）。

背景（Task #2「修 ADB 掉线的根因」）：
- 手机 WiFi IP 会因路由器 DHCP 续期漂移（2026-10-15 实测 10.32.7.125 → 10.32.7.135）
- `smart_match_greet.py` 的重连目标 `--adb-reconnect-ip` 之前是**写死的** `10.32.7.105:5555`
  （Windows 主机档的地址，Pi 5 档上根本连不上）
- 重连失败时没有触发子网扫描（`utils/refresh_device_ip.py` 已实现但没接进来）

覆盖：
- `_resolve_reconnect_ip()`：从 config/devices.json 取 ip:standard_port
- `_adb_reconnect()`：连不上时走 IP 漂移恢复并用新 IP 重试
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from scenarios.boss.scripts import smart_match_greet as smg  # noqa: E402


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    """把 config/devices.json 指向临时文件。"""
    p = tmp_path / "devices.json"
    p.write_text(
        json.dumps(
            {
                "devices": [
                    {
                        "id": "pixel8a_001",
                        "ip": "10.32.7.135",
                        "port": 42527,
                        "standard_port": 5555,
                        "serial": "42231JEKB04971",
                        "network": "10.32.7.0/24",
                    }
                ],
                "default_device": "pixel8a_001",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(smg, "DEVICES_CONFIG", p)
    return p


class TestResolveReconnectIp:
    def test_uses_config_ip_and_standard_port(self, cfg_file):
        """用 standard_port（5555），不是配对后的随机 port（42527）。"""
        assert smg._resolve_reconnect_ip() == "10.32.7.135:5555"

    def test_falls_back_when_config_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(smg, "DEVICES_CONFIG", tmp_path / "nope.json")
        assert smg._resolve_reconnect_ip("1.2.3.4:5555") == "1.2.3.4:5555"

    def test_falls_back_when_no_ip_field(self, tmp_path, monkeypatch):
        p = tmp_path / "devices.json"
        p.write_text(json.dumps({"devices": [{"id": "x"}]}), encoding="utf-8")
        monkeypatch.setattr(smg, "DEVICES_CONFIG", p)
        assert smg._resolve_reconnect_ip("1.2.3.4:5555") == "1.2.3.4:5555"

    def test_falls_back_when_port_absent(self, tmp_path, monkeypatch):
        p = tmp_path / "devices.json"
        p.write_text(json.dumps({"devices": [{"ip": "10.0.0.9"}]}), encoding="utf-8")
        monkeypatch.setattr(smg, "DEVICES_CONFIG", p)
        assert smg._resolve_reconnect_ip() == "10.0.0.9:5555"


class TestReconnectIpDrift:
    def test_refreshes_and_retries_with_new_ip(self, monkeypatch):
        """连不上旧 IP → 扫网段拿到新 IP → 用新 IP 重连成功。"""
        tried = []

        def _fake_run(cmd, **kwargs):
            tried.append(cmd)
            if cmd[:3] == ["adb", "connect", "10.32.7.135:5555"]:
                return subprocess.CompletedProcess(cmd, 1, stdout="failed to connect", stderr="")
            if cmd[:3] == ["adb", "connect", "10.32.7.199:5555"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="connected to 10.32.7.199:5555", stderr="")
            if cmd[:2] == ["adb", "devices"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="List of devices\n10.32.7.199:5555\tdevice\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(smg.subprocess, "run", _fake_run)
        monkeypatch.setattr(smg, "_refresh_device_ip", lambda: (True, "10.32.7.199:5555"))
        monkeypatch.setattr(smg.time, "sleep", lambda _s: None)

        assert smg._adb_reconnect("10.32.7.135:5555") == "10.32.7.199:5555"
        assert ["adb", "connect", "10.32.7.199:5555"] in tried

    def test_no_refresh_when_first_connect_succeeds(self, monkeypatch):
        called = []

        def _fake_run(cmd, **kwargs):
            if cmd[:3] == ["adb", "connect", "10.32.7.135:5555"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="already connected to 10.32.7.135:5555", stderr="")
            if cmd[:2] == ["adb", "devices"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="10.32.7.135:5555\tdevice\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(smg.subprocess, "run", _fake_run)
        monkeypatch.setattr(smg, "_refresh_device_ip", lambda: called.append(1) or (False, ""))
        monkeypatch.setattr(smg.time, "sleep", lambda _s: None)

        assert smg._adb_reconnect("10.32.7.135:5555") == "10.32.7.135:5555"
        assert not called, "第一次就连上时不该触发子网扫描"

    def test_refresh_disabled_skips_drift_recovery(self, monkeypatch):
        called = []

        def _fake_run(cmd, **kwargs):
            if cmd[:2] == ["adb", "connect"]:
                return subprocess.CompletedProcess(cmd, 1, stdout="failed to connect", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(smg.subprocess, "run", _fake_run)
        monkeypatch.setattr(smg, "_refresh_device_ip", lambda: called.append(1) or (False, ""))
        monkeypatch.setattr(smg.time, "sleep", lambda _s: None)

        assert smg._adb_reconnect("10.32.7.135:5555", allow_ip_refresh=False) is None
        assert not called

    def test_returns_none_when_refresh_finds_nothing(self, monkeypatch):
        def _fake_run(cmd, **kwargs):
            if cmd[:2] == ["adb", "connect"]:
                return subprocess.CompletedProcess(cmd, 1, stdout="failed to connect", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(smg.subprocess, "run", _fake_run)
        monkeypatch.setattr(smg, "_refresh_device_ip", lambda: (False, ""))
        monkeypatch.setattr(smg.time, "sleep", lambda _s: None)

        assert smg._adb_reconnect("10.32.7.135:5555") is None

    def test_refresh_result_on_same_ip_counts_as_success(self, monkeypatch):
        """refresh 校验了 serial 且 IP 没变 → 手机就在线上，不该报失败。

        回归：`refresh_device_ip` 在缓存 IP 可用时返回 (True, cached_ip)，
        旧实现把它当成「没找到」→ 返回 False，白白丢掉一次重试机会。
        """
        def _fake_run(cmd, **kwargs):
            if cmd[:2] == ["adb", "connect"]:
                return subprocess.CompletedProcess(cmd, 1, stdout="failed to connect", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(smg.subprocess, "run", _fake_run)
        monkeypatch.setattr(smg, "_refresh_device_ip", lambda: (True, "10.32.7.135:5555"))
        monkeypatch.setattr(smg.time, "sleep", lambda _s: None)

        assert smg._adb_reconnect("10.32.7.135:5555") == "10.32.7.135:5555"


class TestRefreshDeviceIpCall:
    def test_passes_config_path_and_standard_port(self, monkeypatch, cfg_file):
        """必须用 config/devices.json + standard_port（不是会被 VPN 劫持的 port）。"""
        captured = {}

        def _fake_refresh(**kwargs):
            captured.update(kwargs)
            return True, "10.32.7.135"

        import utils.refresh_device_ip as rdi

        monkeypatch.setattr(rdi, "refresh_device_ip", _fake_refresh)
        ok, target = smg._refresh_device_ip()

        assert ok is True
        assert target == "10.32.7.135:5555"
        assert captured["standard_port"] == 5555
        assert captured["config_path"] == str(cfg_file)
        assert captured["log_dir"].endswith("logs")


class TestScanNetworkDisconnect:
    def test_disconnects_non_matching_candidate(self, monkeypatch):
        """扫到 serial 不匹配的 adbd 设备必须断开——否则 adb devices 有两台，
        ADBRunner.shell() 不带 -s 会报 "more than one device" 整轮挂掉。"""
        from utils import refresh_device_ip as rdi

        calls = []

        def _fake_run(cmd, timeout):
            calls.append(cmd)
            if cmd[:2] == ["ping"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="1 packets", stderr="")
            if cmd[:2] == ["adb", "connect"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="connected", stderr="")
            if cmd[:3] == ["adb", "-s"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="OTHER-SERIAL", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(rdi, "_run", _fake_run)

        found = rdi._scan_network(
            network="10.0.0.0/29",
            expected_serial="TARGET-SERIAL",
            port=5555,
            ping_timeout=0.1,
            adb_timeout=0.5,
        )

        assert found is None, "serial 不匹配不该被当成目标设备"
        disconnects = [c for c in calls if c[:2] == ["adb", "disconnect"]]
        assert len(disconnects) == 6, "6 个候选都该被断开"
        assert ["adb", "disconnect", "10.0.0.1:5555"] in disconnects


class TestSourceGuards:
    def test_cli_default_is_not_hardcoded_ip(self):
        """--adb-reconnect-ip 的默认值不能写死某个 IP。"""
        src = (ROOT / "scenarios" / "boss" / "scripts" / "smart_match_greet.py").read_text(
            encoding="utf-8"
        )
        assert '"--adb-reconnect-ip", default="10.32.7' not in src, "默认 IP 不能写死"
        assert src.count("_resolve_reconnect_ip()") >= 2, "main() 与预解析都要用配置派生"

    def test_refresh_device_ip_module_is_importable(self):
        from utils.refresh_device_ip import refresh_device_ip  # noqa: F401
