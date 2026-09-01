"""
ADB Manager Module

Manages ADB (Android Debug Bridge) connections:
- Wireless ADB connection
- Device detection and status
- Shell command execution
- Screenshot capture
- Input events (tap, swipe, etc.)
"""

import asyncio
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image


@dataclass
class ADBDevice:
    """Represents an ADB-connected device."""
    serial: str
    status: str  # device, offline, unauthorized
    ip: Optional[str] = None
    port: Optional[int] = None
    product: Optional[str] = None
    model: Optional[str] = None
    device: Optional[str] = None

    @property
    def is_connected(self) -> bool:
        return self.status == "device"

    @property
    def is_wireless(self) -> bool:
        return self.ip is not None and ":" in self.serial


class ADBManager:
    """
    Manages ADB connections and operations.

    Supports both USB and wireless (WiFi) ADB connections.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.adb_path = self.config.get("adb_path", "adb")
        self.default_timeout = self.config.get("timeout", 30)
        self.connection_timeout = self.config.get("connection_timeout", 10)

        self._connected_device: Optional[ADBDevice] = None
        self._last_error: Optional[str] = None
        self._connection_start_time: Optional[float] = None

    def _run_command(
        self,
        args: List[str],
        timeout: Optional[int] = None,
        check: bool = True
    ) -> Tuple[bool, str, str]:
        """
        Run an ADB command.

        Returns:
            Tuple of (success, stdout, stderr)
        """
        cmd = [self.adb_path] + args
        timeout = timeout or self.default_timeout

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )

            if result.returncode == 0:
                return True, result.stdout.strip(), result.stderr.strip()
            else:
                if check:
                    self._last_error = result.stderr.strip()
                return False, result.stdout.strip(), result.stderr.strip()

        except subprocess.TimeoutExpired:
            error = f"Command timed out after {timeout}s"
            self._last_error = error
            return False, "", error

        except Exception as e:
            error = str(e)
            self._last_error = error
            return False, "", error

    def is_adb_available(self) -> bool:
        """Check if ADB is installed and accessible."""
        success, stdout, _ = self._run_command(["version"], check=False)
        return success and "Android Debug Bridge" in stdout

    def list_devices(self) -> List[ADBDevice]:
        """List all connected ADB devices."""
        success, stdout, _ = self._run_command(["devices", "-l"], check=False)

        if not success:
            return []

        devices = []
        lines = stdout.strip().split("\n")

        # Skip header line
        for line in lines[1:]:
            if not line.strip():
                continue

            parts = line.split()
            if len(parts) < 2:
                continue

            serial = parts[0]
            status = parts[1]

            # Parse additional info
            info = {}
            for part in parts[2:]:
                if ":" in part:
                    key, value = part.split(":", 1)
                    info[key] = value

            # Extract IP from serial if wireless
            ip = None
            port = None
            if ":" in serial:
                ip_port = serial.rsplit(":", 1)
                if len(ip_port) == 2 and ip_port[1].isdigit():
                    ip = ip_port[0]
                    port = int(ip_port[1])

            device = ADBDevice(
                serial=serial,
                status=status,
                ip=ip,
                port=port,
                product=info.get("product"),
                model=info.get("model"),
                device=info.get("device"),
            )
            devices.append(device)

        return devices

    def connect(self, ip: str, port: int = 5555) -> bool:
        """
        Connect to a device via wireless ADB.

        Args:
            ip: Device IP address
            port: ADB port (default: 5555)

        Returns:
            True if connection succeeded
        """
        address = f"{ip}:{port}"
        success, stdout, stderr = self._run_command(
            ["connect", address],
            timeout=self.connection_timeout,
            check=False,
        )

        if success and ("connected" in stdout.lower() or "already connected" in stdout.lower()):
            # Verify the device is in the device list
            devices = self.list_devices()
            for device in devices:
                if device.serial == address and device.is_connected:
                    self._connected_device = device
                    self._connection_start_time = time.time()
                    return True

        # Check if it's already connected but we missed it
        devices = self.list_devices()
        for device in devices:
            if device.serial == address and device.is_connected:
                self._connected_device = device
                self._connection_start_time = time.time()
                return True

        return False

    def disconnect(self, ip: Optional[str] = None, port: Optional[int] = None) -> bool:
        """Disconnect from a wireless device or all devices."""
        if ip:
            address = f"{ip}:{port or 5555}"
            success, _, _ = self._run_command(["disconnect", address])
        else:
            success, _, _ = self._run_command(["disconnect"])

        if success:
            self._connected_device = None
            self._connection_start_time = None

        return success

    def pair(self, ip: str, port: int, pairing_code: str) -> bool:
        """
        Pair with a device for wireless debugging.

        Args:
            ip: Device IP address
            port: Pairing port (from developer options)
            pairing_code: 6-digit pairing code

        Returns:
            True if pairing succeeded
        """
        address = f"{ip}:{port}"
        success, stdout, stderr = self._run_command(
            ["pair", address, pairing_code],
            timeout=30,
            check=False,
        )

        if success and ("successfully paired" in stdout.lower() or "paired" in stdout.lower()):
            return True

        # Sometimes pairing works even if the output is unexpected
        if "failed" not in stderr.lower() and "error" not in stderr.lower():
            return True

        return False

    def is_device_connected(self, device_id: Optional[str] = None) -> bool:
        """Check if a specific device or any device is connected."""
        devices = self.list_devices()

        if device_id:
            for device in devices:
                if device.serial == device_id or device.ip == device_id:
                    return device.is_connected
            return False

        # Check if any device is connected
        for device in devices:
            if device.is_connected:
                self._connected_device = device
                return True

        return False

    def get_device(self) -> Optional[ADBDevice]:
        """Get the currently connected device."""
        if self._connected_device:
            # Verify it's still connected
            if self.is_device_connected(self._connected_device.serial):
                return self._connected_device
            self._connected_device = None

        # Try to find any connected device
        devices = self.list_devices()
        for device in devices:
            if device.is_connected:
                self._connected_device = device
                return device

        return None

    def shell(self, command: str, device_id: Optional[str] = None) -> Tuple[bool, str]:
        """
        Execute a shell command on the device.

        Returns:
            Tuple of (success, output)
        """
        args = ["shell", command]
        if device_id:
            args = ["-s", device_id] + args

        success, stdout, stderr = self._run_command(args, check=False)

        if success:
            return True, stdout
        else:
            return False, stderr or stdout

    def shell_stream(self, command: str, device_id: Optional[str] = None) -> subprocess.Popen:
        """
        Execute a shell command and return a streaming process.

        Use this for long-running commands.
        """
        cmd = [self.adb_path]
        if device_id:
            cmd.extend(["-s", device_id])
        cmd.extend(["shell", command])

        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def screenshot(self, device_id: Optional[str] = None) -> Optional[Image.Image]:
        """
        Capture a screenshot from the device.

        Returns:
            PIL Image or None if failed
        """
        # Use exec-out for binary output (more reliable)
        cmd = [self.adb_path]
        if device_id:
            cmd.extend(["-s", device_id])
        cmd.extend(["exec-out", "screencap", "-p"])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=self.default_timeout,
            )

            if result.returncode == 0 and result.stdout:
                # Convert binary PNG data to PIL Image
                import io
                image = Image.open(io.BytesIO(result.stdout))
                return image.convert("RGB")

        except Exception as e:
            self._last_error = f"Screenshot failed: {str(e)}"

        return None

    def tap(self, x: int, y: int, device_id: Optional[str] = None) -> bool:
        """Tap at coordinates."""
        success, _ = self.shell(f"input tap {x} {y}", device_id)
        return success

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration: int = 300,
        device_id: Optional[str] = None
    ) -> bool:
        """
        Swipe from (x1, y1) to (x2, y2).

        Args:
            duration: Duration in milliseconds
        """
        success, _ = self.shell(
            f"input swipe {x1} {y1} {x2} {y2} {duration}",
            device_id
        )
        return success

    def long_press(
        self,
        x: int,
        y: int,
        duration: int = 1000,
        device_id: Optional[str] = None
    ) -> bool:
        """Long press at coordinates."""
        # Long press is a swipe with zero distance
        return self.swipe(x, y, x, y, duration, device_id)

    def type_text(self, text: str, device_id: Optional[str] = None) -> bool:
        """Type text on the device."""
        # Escape special characters
        escaped = text.replace("'", "'\"'\"'").replace(" ", "%s")
        if " " in text:
            success, _ = self.shell(f"input text '{escaped}'", device_id)
        else:
            success, _ = self.shell(f"input text {escaped}", device_id)
        return success

    def keyevent(self, keycode: int, device_id: Optional[str] = None) -> bool:
        """
        Send a key event.

        Common keycodes:
        - 3: HOME
        - 4: BACK
        - 24: VOLUME_UP
        - 25: VOLUME_DOWN
        - 26: POWER
        - 82: MENU/Recent Apps
        """
        success, _ = self.shell(f"input keyevent {keycode}", device_id)
        return success

    def press_home(self, device_id: Optional[str] = None) -> bool:
        """Press home button."""
        return self.keyevent(3, device_id)

    def press_back(self, device_id: Optional[str] = None) -> bool:
        """Press back button."""
        return self.keyevent(4, device_id)

    def press_recent(self, device_id: Optional[str] = None) -> bool:
        """Show recent apps."""
        return self.keyevent(82, device_id)

    def start_app(self, package: str, activity: Optional[str] = None, device_id: Optional[str] = None) -> bool:
        """Start an app."""
        if activity:
            cmd = f"am start -n {package}/{activity}"
        else:
            cmd = f"monkey -p {package} -c android.intent.category.LAUNCHER 1"

        success, _ = self.shell(cmd, device_id)
        return success

    def stop_app(self, package: str, device_id: Optional[str] = None) -> bool:
        """Force stop an app."""
        success, _ = self.shell(f"am force-stop {package}", device_id)
        return success

    def get_screen_size(self, device_id: Optional[str] = None) -> Optional[Tuple[int, int]]:
        """Get the device screen size."""
        success, output = self.shell("wm size", device_id)

        if success and "Physical size" in output:
            # Parse "Physical size: 1080x2400"
            try:
                size_str = output.split(":")[-1].strip()
                width, height = map(int, size_str.split("x"))
                return (width, height)
            except (ValueError, IndexError):
                pass

        return None

    def get_status(self) -> Dict[str, Any]:
        """Get the current status of the ADB manager."""
        device = self.get_device()

        return {
            "adb_available": self.is_adb_available(),
            "device_connected": device is not None,
            "device": {
                "serial": device.serial if device else None,
                "model": device.model if device else None,
                "ip": device.ip if device else None,
                "wireless": device.is_wireless if device else False,
            } if device else None,
            "last_error": self._last_error,
            "connection_duration": (
                time.time() - self._connection_start_time
                if self._connection_start_time else 0
            ),
        }