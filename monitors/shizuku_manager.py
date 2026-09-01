"""
Shizuku Manager Module

Manages Shizuku service for elevated permissions on Android:
- Check Shizuku status
- Start/stop Shizuku service
- Execute commands via Shizuku
- Pairing with Shizuku

Note: Shizuku provides ADB-level permissions without root.
"""

import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ShizukuStatus:
    """Represents Shizuku service status."""
    is_running: bool
    version: Optional[str] = None
    uid: Optional[int] = None
    context: Optional[str] = None
    process_id: Optional[int] = None

    @property
    def is_active(self) -> bool:
        """Check if Shizuku is actively running."""
        return self.is_running and self.process_id is not None


class ShizukuManager:
    """
    Manages Shizuku service integration.

    Shizuku allows apps to use system APIs with ADB-level permissions
    without requiring root access.
    """

    SHIZUKU_PACKAGES = [
        "moe.shizuku.privileged.api",
        "rikka.shizuku",
    ]

    def __init__(self, adb_manager, config: Optional[Dict[str, Any]] = None):
        self.adb = adb_manager
        self.config = config or {}
        self._last_status: Optional[ShizukuStatus] = None
        self._last_check_time: float = 0
        self._check_interval = self.config.get("status_check_interval", 5)

    def _shell(self, command: str) -> Tuple[bool, str]:
        """Execute a shell command via ADB."""
        return self.adb.shell(command)

    def is_available(self) -> bool:
        """Check if Shizuku app is installed."""
        success, output = self._shell("pm list packages | grep shizuku")
        return success and any(pkg in output for pkg in self.SHIZUKU_PACKAGES)

    def get_status(self, force: bool = False) -> ShizukuStatus:
        """
        Get the current Shizuku service status.

        Args:
            force: Force a fresh check even if cached
        """
        # Use cached status if recent
        if not force and self._last_status:
            if time.time() - self._last_check_time < self._check_interval:
                return self._last_status

        status = ShizukuStatus(is_running=False)

        # Check if Shizuku service is running
        success, output = self._shell("shizuku status")

        if success:
            # Parse Shizuku status output
            # Example: "Shizuku is running (version 13, uid 2000, context u:r:shell:s0)"
            if "is running" in output.lower():
                status.is_running = True

                # Extract version
                version_match = re.search(r"version\s+(\d+)", output, re.IGNORECASE)
                if version_match:
                    status.version = version_match.group(1)

                # Extract UID
                uid_match = re.search(r"uid\s+(\d+)", output, re.IGNORECASE)
                if uid_match:
                    status.uid = int(uid_match.group(1))

                # Extract context
                context_match = re.search(r"context\s+(\S+)", output, re.IGNORECASE)
                if context_match:
                    status.context = context_match.group(1)

            # Also check process
            success2, ps_output = self._shell("ps -A | grep shizuku")
            if success2:
                lines = ps_output.strip().split("\n")
                for line in lines:
                    if "shizuku" in line.lower() and "grep" not in line.lower():
                        parts = line.split()
                        if len(parts) >= 2:
                            try:
                                status.process_id = int(parts[1])
                                status.is_running = True
                            except ValueError:
                                pass
                        break

        # Fallback: check if Shizuku service is bound
        if not status.is_running:
            success3, output3 = self._shell(
                "service check activity | grep shizuku"
            )
            if success3 and "shizuku" in output3.lower():
                status.is_running = True

        self._last_status = status
        self._last_check_time = time.time()
        return status

    def is_running(self) -> bool:
        """Quick check if Shizuku is running."""
        status = self.get_status()
        return status.is_active

    def start(self) -> bool:
        """
        Start the Shizuku service.

        Note: Starting Shizuku typically requires the app to be opened
        manually for the first time, or via ADB with proper authorization.
        """
        # Method 1: Try to start via app broadcast
        success, _ = self._shell(
            "am start-foreground-service -a shizuku.intent.action.START"
        )

        if not success:
            # Method 2: Try to start the main activity
            success2, _ = self._shell(
                "am start -n moe.shizuku.privileged.api/.starter.StarterActivity"
            )
            success = success2

        # Wait a moment and verify
        if success:
            time.sleep(2)
            return self.is_running()

        return False

    def stop(self) -> bool:
        """Stop the Shizuku service."""
        success, _ = self._shell(
            "am stopservice -a shizuku.intent.action.START"
        )

        # Also try to kill the process
        status = self.get_status(force=True)
        if status.process_id:
            self._shell(f"kill {status.process_id}")

        return success

    def execute(self, command: str, use_shizuku: bool = True) -> Tuple[bool, str]:
        """
        Execute a command, optionally via Shizuku for elevated permissions.

        Args:
            command: The shell command to execute
            use_shizuku: Whether to try using Shizuku for elevated permissions

        Returns:
            Tuple of (success, output)
        """
        if use_shizuku and self.is_running():
            # Try to execute via Shizuku
            success, output = self._shell(f"shizuku shell '{command}'")
            if success:
                return True, output

        # Fallback to regular shell
        return self._shell(command)

    def grant_permission(self, package: str, permission: str) -> bool:
        """
        Grant a permission to a package using Shizuku.

        This requires elevated permissions that Shizuku provides.
        """
        success, _ = self.execute(f"pm grant {package} {permission}")
        return success

    def revoke_permission(self, package: str, permission: str) -> bool:
        """Revoke a permission from a package."""
        success, _ = self.execute(f"pm revoke {package} {permission}")
        return success

    def install_app(self, apk_path: str) -> bool:
        """
        Install an APK with elevated permissions.

        Note: This requires the APK to be accessible on the device.
        """
        success, _ = self.execute(f"pm install -r '{apk_path}'")
        return success

    def uninstall_app(self, package: str, keep_data: bool = False) -> bool:
        """Uninstall an app with elevated permissions."""
        flags = "-k" if keep_data else ""
        success, _ = self.execute(f"pm uninstall {flags} {package}")
        return success

    def force_stop_app(self, package: str) -> bool:
        """Force stop an app with elevated permissions."""
        success, _ = self.execute(f"am force-stop {package}")
        return success

    def clear_app_data(self, package: str) -> bool:
        """Clear app data with elevated permissions."""
        success, _ = self.execute(f"pm clear {package}")
        return success

    def get_package_info(self, package: str) -> Optional[Dict[str, Any]]:
        """Get detailed package information."""
        success, output = self.execute(f"dumpsys package {package}")

        if not success:
            return None

        info = {"package": package}

        # Parse version
        version_match = re.search(r"versionName=([^\s]+)", output)
        if version_match:
            info["version"] = version_match.group(1)

        # Parse version code
        code_match = re.search(r"versionCode=(\d+)", output)
        if code_match:
            info["version_code"] = int(code_match.group(1))

        # Parse permissions
        permissions = []
        perm_section = re.search(
            r"grantedPermissions:(.*?)(?=\n\w|$)",
            output,
            re.DOTALL
        )
        if perm_section:
            for line in perm_section.group(1).split("\n"):
                line = line.strip()
                if line and line.startswith("android.permission."):
                    permissions.append(line)

        info["permissions"] = permissions
        info["raw_output"] = output[:2000]  # First 2000 chars

        return info

    def list_packages(self, system_apps: bool = False) -> List[str]:
        """List installed packages."""
        flag = "-s" if system_apps else "-3"  # -3 for third-party apps
        success, output = self.execute(f"pm list packages {flag}")

        packages = []
        if success:
            for line in output.strip().split("\n"):
                if line.startswith("package:"):
                    packages.append(line.replace("package:", "").strip())

        return packages

    def get_detailed_status(self) -> Dict[str, Any]:
        """Get detailed Shizuku status information."""
        status = self.get_status(force=True)

        return {
            "available": self.is_available(),
            "running": status.is_running,
            "active": status.is_active,
            "version": status.version,
            "uid": status.uid,
            "context": status.context,
            "process_id": status.process_id,
            "last_check": self._last_check_time,
        }