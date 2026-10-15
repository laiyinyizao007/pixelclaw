"""
Unit tests for utils/refresh_device_ip.py

Covers the auto IP refresh feature (2026-10-15): when phone WiFi IP drifts
(路由器 DHCP 续期), script scans the configured subnet, verifies the
device by serial number, and rewrites config/devices.json.
"""
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from utils import refresh_device_ip


# ───────────────────────────── fixtures ─────────────────────────────


@pytest.fixture
def tmp_devices_json(tmp_path: Path) -> Path:
    """Fake devices.json with wrong IP — simulates IP drift scenario."""
    cfg = {
        "devices": [
            {
                "id": "pixel8a_001",
                "name": "Pixel 8a",
                "type": "android",
                "ip": "10.32.7.99",  # stale
                "port": 42527,
                "standard_port": 5555,
                "serial": "42231JEKB04971",
                "network": "10.32.7.0/24",
            }
        ],
        "active_device": "pixel8a_001",
        "default_device": "pixel8a_001",
    }
    p = tmp_path / "devices.json"
    p.write_text(json.dumps(cfg))
    return p


def _fake_proc(returncode: int = 0, stdout: str = "", stderr: str = ""):
    """Build a fake CompletedProcess."""
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


# ─────────────────── _verify_current_ip tests ───────────────────


def test_verify_current_ip_returns_true_on_serial_match():
    """current IP + matching serial = no refresh needed."""
    with patch.object(
        refresh_device_ip,
        "_run",
        side_effect=[
            _fake_proc(0, "connected to 10.32.7.135:5555"),  # adb connect
            _fake_proc(0, "42231JEKB04971\n"),  # adb shell getprop
        ],
    ) as mock_run:
        result = refresh_device_ip._verify_current_ip(
            ip="10.32.7.135",
            port=5555,
            expected_serial="42231JEKB04971",
            timeout=2.0,
        )
    assert result is True
    assert mock_run.call_count == 2


def test_verify_current_ip_returns_false_on_serial_mismatch():
    """current IP reachable but wrong device → must NOT trust it."""
    with patch.object(
        refresh_device_ip,
        "_run",
        side_effect=[
            _fake_proc(0, "connected to 10.32.7.135:5555"),
            _fake_proc(0, "DIFFERENTSERIAL\n"),  # wrong phone!
        ],
    ):
        result = refresh_device_ip._verify_current_ip(
            ip="10.32.7.135",
            port=5555,
            expected_serial="42231JEKB04971",
            timeout=2.0,
        )
    assert result is False


def test_verify_current_ip_returns_false_on_connect_timeout():
    """SYN timeout / connection refused → return False."""
    with patch.object(
        refresh_device_ip,
        "_run",
        return_value=_fake_proc(1, "", "failed to connect"),
    ):
        result = refresh_device_ip._verify_current_ip(
            ip="10.32.7.99",  # stale, unreachable
            port=5555,
            expected_serial="42231JEKB04971",
            timeout=2.0,
        )
    assert result is False


# ─────────── _scan_network tests (the safety-critical path) ───────────


def test_scan_network_finds_phone_with_correct_serial():
    """Scan /24: only 10.32.7.135 responds with the right serial."""
    target_ip = "10.32.7.135"

    def fake_run(cmd, **kwargs):
        joined = " ".join(cmd)
        # ping target_ip → returncode 0 (host is up)
        if cmd[0] == "ping" and target_ip in joined:
            return _fake_proc(0, "1 packets received")
        # adb connect to target_ip → "connected"
        if "connect" in joined and target_ip in joined:
            return _fake_proc(0, "connected to 10.32.7.135:5555")
        if "shell" in joined and "getprop" in joined and target_ip in joined:
            return _fake_proc(0, "42231JEKB04971\n")
        if "disconnect" in joined and target_ip in joined:
            return _fake_proc(0, "disconnected")
        return _fake_proc(1, "", "No route to host")

    with patch.object(refresh_device_ip, "_run", side_effect=fake_run):
        found = refresh_device_ip._scan_network(
            network="10.32.7.0/24",
            expected_serial="42231JEKB04971",
            port=5555,
            ping_timeout=0.05,
            adb_timeout=1.0,
        )
    assert found == "10.32.7.135"


def test_scan_network_rejects_wrong_serial():
    """Connected device returns wrong serial — must NOT be returned."""
    def fake_run(cmd, **kwargs):
        # Some IP responds with a wrong serial
        return _fake_proc(0, "WRONGSERIAL\n")

    with patch.object(refresh_device_ip, "_run", side_effect=fake_run):
        found = refresh_device_ip._scan_network(
            network="10.32.7.0/24",
            expected_serial="42231JEKB04971",
            port=5555,
            ping_timeout=0.05,
            adb_timeout=1.0,
        )
    assert found is None


def test_scan_network_returns_none_if_no_candidate_matches():
    """No device responds with matching serial → None, no false claim."""
    with patch.object(
        refresh_device_ip,
        "_run",
        return_value=_fake_proc(1, "", "timeout"),
    ):
        found = refresh_device_ip._scan_network(
            network="10.32.7.0/24",
            expected_serial="42231JEKB04971",
            port=5555,
            ping_timeout=0.05,
            adb_timeout=0.5,
        )
    assert found is None


# ─────────────────── top-level refresh_device_ip tests ───────────────────


def test_refresh_returns_existing_ip_when_correct(tmp_devices_json: Path):
    """Current IP works → no config write, return (True, current_ip)."""
    # First verify_current_ip succeeds, so no scan needed
    with patch.object(
        refresh_device_ip,
        "_verify_current_ip",
        return_value=True,
    ) as mock_verify:
        ok, ip = refresh_device_ip.refresh_device_ip(
            config_path=str(tmp_devices_json),
            log_dir=str(tmp_devices_json.parent),
        )
    assert ok is True
    assert ip == "10.32.7.99"  # unchanged, current was correct
    assert mock_verify.call_count == 1
    # Config not modified
    cfg = json.load(open(tmp_devices_json))
    assert cfg["devices"][0]["ip"] == "10.32.7.99"


def test_refresh_updates_config_when_ip_changes(tmp_devices_json: Path):
    """Current IP fails, scan finds new IP, config is rewritten."""
    with patch.object(
        refresh_device_ip,
        "_verify_current_ip",
        return_value=False,
    ), patch.object(
        refresh_device_ip,
        "_scan_network",
        return_value="10.32.7.135",
    ) as mock_scan:
        ok, new_ip = refresh_device_ip.refresh_device_ip(
            config_path=str(tmp_devices_json),
            log_dir=str(tmp_devices_json.parent),
        )
    assert ok is True
    assert new_ip == "10.32.7.135"
    assert mock_scan.call_count == 1

    # Config WAS updated
    cfg = json.load(open(tmp_devices_json))
    assert cfg["devices"][0]["ip"] == "10.32.7.135"


def test_refresh_does_not_update_config_if_scan_fails(tmp_devices_json: Path):
    """Conservative: if scan finds nothing, leave config untouched."""
    with patch.object(
        refresh_device_ip,
        "_verify_current_ip",
        return_value=False,
    ), patch.object(
        refresh_device_ip,
        "_scan_network",
        return_value=None,
    ):
        ok, ip = refresh_device_ip.refresh_device_ip(
            config_path=str(tmp_devices_json),
            log_dir=str(tmp_devices_json.parent),
        )
    assert ok is False
    assert ip == "10.32.7.99"  # original stale value

    # Config NOT updated
    cfg = json.load(open(tmp_devices_json))
    assert cfg["devices"][0]["ip"] == "10.32.7.99"


def test_refresh_skips_when_no_serial_in_config(tmp_path: Path):
    """Defensive: config without 'serial' field → refuse to scan (no
    way to verify)."""
    cfg = {
        "devices": [{"id": "x", "ip": "10.32.7.99", "network": "10.32.7.0/24"}],
        "default_device": "x",
    }
    p = tmp_path / "devices.json"
    p.write_text(json.dumps(cfg))

    with patch.object(refresh_device_ip, "_scan_network") as mock_scan:
        ok, ip = refresh_device_ip.refresh_device_ip(
            config_path=str(p),
            log_dir=str(tmp_path),
        )
    assert ok is False
    assert ip == "10.32.7.99"
    mock_scan.assert_not_called()  # never scanned without serial