"""
Auto refresh device WiFi IP (2026-10-15)

When the phone's WiFi IP drifts (路由器 DHCP 续期), `adb connect` to the
cached IP times out. This module detects the drift, scans the configured
subnet for the phone, verifies it by **serial number** (never by IP alone
— 同子网有别的设备会误判), and rewrites `config/devices.json`.

Why serial-verify (not model or hostname):
- Serial is unique per device and stored on the device (`ro.serialno`)
- Model could collide with another phone of same model on the LAN
- Hostname DHCP option is unreliable on consumer routers

Why IP /24 subnet (not wider scan):
- Phone and Pi are known to be in the same /24 (`10.32.7.0/24`)
- Wider scan is slow + blocked by most routers
- `network` field in config is extensible for other subnets

Caller integration points:
- `monitors/adb_manager.py:ADBManager.connect()` retries after refresh
- `__main__.py` exposes `cmd_refresh_ip` for manual runs

Manual usage:
    python -m utils.refresh_device_ip [--config config/devices.json]
"""

from __future__ import annotations

import ipaddress
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger("utils.refresh_device_ip")

# ────────────────────────────── low-level ──────────────────────────────


def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    """Run a command with timeout, swallow exceptions, return CompletedProcess.

    We don't want a single timeout to crash the refresh loop; failures are
    handled by callers checking returncode / stderr.
    """
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        m = subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr="timeout")
        return m
    except FileNotFoundError as e:
        return subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr=str(e))


# ──────────────────────────── verification ────────────────────────────


def _verify_current_ip(
    ip: str,
    port: int,
    expected_serial: str,
    timeout: float,
) -> bool:
    """Try cached IP. Return True iff reachable AND serial matches.

    Two-step because a wrong-device reachable IP must NOT be trusted
    (e.g. 同子网另一台设备碰巧响应 5555).
    """
    connect = _run(["adb", "connect", f"{ip}:{port}"], timeout=timeout)
    if connect.returncode != 0 or "connected" not in connect.stdout.lower():
        return False

    getprop = _run(
        ["adb", "-s", f"{ip}:{port}", "shell", "getprop", "ro.serialno"],
        timeout=timeout,
    )
    if getprop.returncode != 0:
        return False
    actual = getprop.stdout.strip()
    return actual == expected_serial


# ──────────────────────────── subnet scan ────────────────────────────


def _scan_network(
    network: str,
    expected_serial: str,
    port: int,
    ping_timeout: float,
    adb_timeout: float,
) -> Optional[str]:
    """Scan the /24 (or configured CIDR) for the phone, verify by serial.

    Two-tier filtering for speed:
    1. `ping -c1 -W1` rejects IPs with no device (filters most of /24 fast)
    2. `adb connect` + `getprop ro.serialno` confirms the candidate

    Returns the IP string, or None if not found.
    """
    try:
        net = ipaddress.ip_network(network, strict=False)
    except ValueError:
        logger.error("invalid network: %s", network)
        return None

    # Skip network/broadcast addresses for /24 and smaller
    candidates = [str(h) for h in net.hosts()]

    for ip in candidates:
        # Tier 1: cheap ICMP probe
        ping = _run(
            ["ping", "-c", "1", "-W", "1", ip],
            timeout=ping_timeout + 0.5,
        )
        if ping.returncode != 0:
            continue

        # Tier 2: adb connect + serial verify
        connect = _run(["adb", "connect", f"{ip}:{port}"], timeout=adb_timeout)
        if connect.returncode != 0 or "connected" not in connect.stdout.lower():
            continue

        getprop = _run(
            ["adb", "-s", f"{ip}:{port}", "shell", "getprop", "ro.serialno"],
            timeout=adb_timeout,
        )
        if getprop.returncode == 0 and getprop.stdout.strip() == expected_serial:
            logger.info("found phone at %s (serial %s)", ip, expected_serial)
            # Disconnect the test connection; the real caller will reconnect
            _run(["adb", "disconnect", f"{ip}:{port}"], timeout=adb_timeout)
            return ip

        # 不是目标设备 → 断开。留着会让 adb devices 同时列出两台设备，
        # 而 ADBRunner.shell() 不带 -s，adb 会报 "more than one device"
        # 直接让整轮跑挂掉。
        _run(["adb", "disconnect", f"{ip}:{port}"], timeout=adb_timeout)

    return None


# ──────────────────────────── config update ────────────────────────────


def _update_config_ip(config_path: str, new_ip: str) -> None:
    """Rewrite `ip` field for the active device. Atomic via tmp+rename."""
    path = Path(config_path)
    cfg = json.loads(path.read_text())
    devices = cfg.get("devices", [])
    if not devices:
        raise ValueError(f"no devices in {config_path}")

    # Update default_device (the one we just verified)
    target_id = cfg.get("default_device") or cfg.get("active_device") or devices[0]["id"]
    for dev in devices:
        if dev.get("id") == target_id:
            dev["ip"] = new_ip
            break
    else:
        # Default to first device if id match fails
        devices[0]["ip"] = new_ip

    # Atomic write: write to tmp, rename
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)
    logger.info("updated %s: ip=%s", config_path, new_ip)


# ──────────────────────────── public API ────────────────────────────


def refresh_device_ip(
    config_path: str = "config/devices.json",
    log_dir: str = "logs",
    standard_port: int = 5555,
    ping_timeout: float = 0.3,
    adb_timeout: float = 1.5,
) -> Tuple[bool, str]:
    """Detect phone IP drift, scan configured subnet, rewrite config.

    Returns (True, IP) on success — IP is the verified current phone IP.
    Returns (False, current_ip) on failure — current_ip is what was in the
    config (caller can decide to retry or alert).

    Conservative: if `serial` field is missing in config, refuses to scan
    (no way to verify candidates), leaves config untouched.
    """
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    if not logger.handlers:
        log_file = Path(log_dir) / "auto_refresh.log"
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
            )
        )
        logger.addHandler(fh)
        logger.setLevel(logging.INFO)

    path = Path(config_path)
    if not path.exists():
        logger.error("config not found: %s", config_path)
        return False, ""

    cfg = json.loads(path.read_text())
    devices = cfg.get("devices", [])
    if not devices:
        logger.error("no devices in %s", config_path)
        return False, ""

    target_id = cfg.get("default_device") or cfg.get("active_device") or devices[0]["id"]
    dev = next((d for d in devices if d.get("id") == target_id), devices[0])

    current_ip = dev.get("ip", "")
    expected_serial = dev.get("serial", "")
    network = dev.get("network", "")

    if not expected_serial:
        logger.warning(
            "device %s has no 'serial' field; cannot verify candidates. "
            "skipping scan.",
            target_id,
        )
        return False, current_ip

    # Step 1: verify cached IP
    if _verify_current_ip(current_ip, standard_port, expected_serial, adb_timeout):
        logger.info("cached IP %s OK (serial match)", current_ip)
        return True, current_ip

    # Step 2: scan subnet
    logger.info(
        "cached IP %s unreachable or wrong serial; scanning %s",
        current_ip,
        network,
    )
    new_ip = _scan_network(
        network=network,
        expected_serial=expected_serial,
        port=standard_port,
        ping_timeout=ping_timeout,
        adb_timeout=adb_timeout,
    )

    if new_ip is None:
        logger.warning("no candidate with serial %s found", expected_serial)
        return False, current_ip

    # Step 3: update + return
    try:
        _update_config_ip(config_path, new_ip)
    except Exception as e:
        logger.error("failed to update %s: %s", config_path, e)
        return False, current_ip

    logger.info("refresh complete: %s → %s", current_ip, new_ip)
    return True, new_ip


def main(argv: list[str] | None = None) -> int:
    """CLI entry: `python -m utils.refresh_device_ip [config_path]`"""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default="config/devices.json",
        help="path to devices.json (default: config/devices.json)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5555,
        help="ADB standard port (default: 5555)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ok, ip = refresh_device_ip(
        config_path=args.config,
        standard_port=args.port,
    )
    print(f"{'✓' if ok else '✗'} refresh: ip={ip}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())