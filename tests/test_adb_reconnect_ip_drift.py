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
        monkeypatch.setattr(smg, "_refresh_device_ip", lambda: "10.32.7.199:5555")
        monkeypatch.setattr(smg.time, "sleep", lambda _s: None)

        assert smg._adb_reconnect("10.32.7.135:5555") is True
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
        monkeypatch.setattr(smg, "_refresh_device_ip", lambda: called.append(1) or "")
        monkeypatch.setattr(smg.time, "sleep", lambda _s: None)

        assert smg._adb_reconnect("10.32.7.135:5555") is True
        assert not called, "第一次就连上时不该触发子网扫描"

    def test_refresh_disabled_skips_drift_recovery(self, monkeypatch):
        called = []

        def _fake_run(cmd, **kwargs):
            if cmd[:2] == ["adb", "connect"]:
                return subprocess.CompletedProcess(cmd, 1, stdout="failed to connect", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(smg.subprocess, "run", _fake_run)
        monkeypatch.setattr(smg, "_refresh_device_ip", lambda: called.append(1) or "")
        monkeypatch.setattr(smg.time, "sleep", lambda _s: None)

        assert smg._adb_reconnect("10.32.7.135:5555", allow_ip_refresh=False) is False
        assert not called

    def test_returns_false_when_refresh_finds_nothing(self, monkeypatch):
        def _fake_run(cmd, **kwargs):
            if cmd[:2] == ["adb", "connect"]:
                return subprocess.CompletedProcess(cmd, 1, stdout="failed to connect", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(smg.subprocess, "run", _fake_run)
        monkeypatch.setattr(smg, "_refresh_device_ip", lambda: None)
        monkeypatch.setattr(smg.time, "sleep", lambda _s: None)

        assert smg._adb_reconnect("10.32.7.135:5555") is False


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
