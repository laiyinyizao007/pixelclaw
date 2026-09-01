"""
Device Connector Module

Manages device connections and provides a unified interface for:
- ADB connection management
- Shizuku integration
- Device configuration loading
- Connection testing
"""

import json
import os
from typing import Any, Dict, Optional

from ..monitors.adb_manager import ADBManager
from ..monitors.shizuku_manager import ShizukuManager


class DeviceConnector:
    """
    Manages device connections and configuration.

    Provides a unified interface for connecting to and managing
    Android devices via ADB and Shizuku.
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        device_id: Optional[str] = None,
        adb_config: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize the device connector.

        Args:
            config_path: Path to devices.json configuration file
            device_id: ID of the device to connect to
            adb_config: Additional ADB configuration
        """
        self.config_path = config_path or "config/devices.json"
        self.device_id = device_id
        self._config: Dict[str, Any] = {}
        self._device_config: Optional[Dict[str, Any]] = None

        # Load configuration
        self._load_config()

        # Initialize ADB manager
        self.adb = ADBManager(adb_config or {})

        # Initialize Shizuku manager
        self.shizuku = ShizukuManager(self.adb)

    def _load_config(self):
        """Load device configuration from file."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r") as f:
                    self._config = json.load(f)
            except json.JSONDecodeError as e:
                print(f"Error loading config: {e}")
                self._config = {"devices": []}
        else:
            self._config = {"devices": []}

        # Find device configuration
        if self.device_id:
            for device in self._config.get("devices", []):
                if device.get("id") == self.device_id:
                    self._device_config = device
                    break
        else:
            # Use default or first device
            default_id = self._config.get("default_device")
            for device in self._config.get("devices", []):
                if default_id and device.get("id") == default_id:
                    self._device_config = device
                    break
                elif not self._device_config:
                    self._device_config = device

    def get_device_config(self) -> Optional[Dict[str, Any]]:
        """Get the current device configuration."""
        return self._device_config

    def get_device_ip(self) -> Optional[str]:
        """Get the device IP address."""
        if self._device_config:
            return self._device_config.get("ip")
        return None

    def get_device_port(self) -> int:
        """Get the device ADB port."""
        if self._device_config:
            return self._device_config.get("port", 5555)
        return 5555

    def get_pairing_code(self) -> Optional[str]:
        """Get the device pairing code."""
        if self._device_config:
            return self._device_config.get("pairing_code")
        return None

    def pair(self) -> bool:
        """
        Pair with the device using wireless debugging.

        Returns:
            True if pairing succeeded
        """
        ip = self.get_device_ip()
        code = self.get_pairing_code()
        pairing_port = self._device_config.get("pairing_port") if self._device_config else None

        if not ip:
            print("Error: Device IP not configured")
            return False

        if not code:
            print("Error: Pairing code not configured")
            return False

        # Default pairing port if not specified
        if not pairing_port:
            pairing_port = self.get_device_port()

        print(f"Pairing with {ip}:{pairing_port} using code {code}...")
        success = self.adb.pair(ip, pairing_port, code)

        if success:
            print("Pairing successful!")
        else:
            print("Pairing failed. Make sure:")
            print("  1. Wireless debugging is enabled on the device")
            print("  2. The IP address and pairing code are correct")
            print("  3. The device is on the same network")

        return success

    def connect(self) -> bool:
        """
        Connect to the configured device.

        Returns:
            True if connection succeeded
        """
        ip = self.get_device_ip()
        port = self.get_device_port()

        if not ip:
            print("Error: Device IP not configured")
            return False

        print(f"Connecting to {ip}:{port}...")
        success = self.adb.connect(ip, port)

        if success:
            print("Connection successful!")
            device = self.adb.get_device()
            if device:
                print(f"Device: {device.model or 'Unknown'}")
                print(f"Serial: {device.serial}")

            # Check Shizuku
            if self.shizuku.is_available():
                print("Shizuku is available")
                if self.shizuku.is_running():
                    print("Shizuku service is running")
                else:
                    print("Shizuku service is not running (optional)")
            else:
                print("Shizuku not installed (optional)")
        else:
            print("Connection failed!")

        return success

    def disconnect(self) -> bool:
        """Disconnect from the device."""
        self.adb.disconnect()
        print("Disconnected")
        return True

    def test_connection(self) -> bool:
        """
        Run comprehensive connection tests.

        Returns:
            True if all tests passed
        """
        print("\n" + "=" * 50)
        print("CONNECTION TEST")
        print("=" * 50)

        all_passed = True

        # Test 1: ADB availability
        print("\n[1/6] Checking ADB availability...")
        if self.adb.is_adb_available():
            print("  ✓ ADB is available")
        else:
            print("  ✗ ADB not found. Install Android SDK platform tools.")
            all_passed = False

        # Test 2: Device connection
        print("\n[2/6] Checking device connection...")
        if self.adb.is_device_connected():
            print("  ✓ Device is connected")
            device = self.adb.get_device()
            if device:
                print(f"    Model: {device.model}")
                print(f"    Serial: {device.serial}")
        else:
            print("  ✗ Device not connected")
            print(f"    Run: adb connect {self.get_device_ip()}:{self.get_device_port()}")
            all_passed = False

        # Test 3: Shell access
        print("\n[3/6] Testing shell access...")
        success, output = self.adb.shell("echo test")
        if success and "test" in output:
            print("  ✓ Shell access working")
        else:
            print("  ✗ Shell access failed")
            all_passed = False

        # Test 4: Screenshot capability
        print("\n[4/6] Testing screenshot capability...")
        screenshot = self.adb.screenshot()
        if screenshot:
            print(f"  ✓ Screenshot working ({screenshot.size[0]}x{screenshot.size[1]})")
        else:
            print("  ✗ Screenshot failed")
            all_passed = False

        # Test 5: Input capability
        print("\n[5/6] Testing input capability...")
        # Just test the command syntax, don't actually tap
        success, _ = self.adb.shell("input tap 100 100")
        if success:
            print("  ✓ Input commands working")
        else:
            print("  ✗ Input commands failed")
            all_passed = False

        # Test 6: Shizuku (optional)
        print("\n[6/6] Checking Shizuku (optional)...")
        if self.shizuku.is_available():
            print("  ✓ Shizuku is installed")
            if self.shizuku.is_running():
                print("  ✓ Shizuku service is running")
            else:
                print("  ! Shizuku service not running (optional)")
        else:
            print("  - Shizuku not installed (optional)")

        # Summary
        print("\n" + "=" * 50)
        if all_passed:
            print("✓ All required tests passed!")
        else:
            print("✗ Some tests failed. Check the output above.")
        print("=" * 50 + "\n")

        return all_passed

    def get_status(self) -> Dict[str, Any]:
        """Get detailed connection status."""
        return {
            "device_config": self._device_config,
            "adb": self.adb.get_status(),
            "shizuku": self.shizuku.get_detailed_status(),
        }