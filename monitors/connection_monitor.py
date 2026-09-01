"""
Connection Monitor Module

Monitors ADB and Shizuku connection health:
- Periodic health checks
- Automatic reconnection
- Status notifications
- Connection statistics
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from .adb_manager import ADBManager
from .shizuku_manager import ShizukuManager


class ConnectionState(Enum):
    """Connection state enumeration."""
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    RECONNECTING = auto()
    ERROR = auto()


@dataclass
class ConnectionStats:
    """Connection statistics."""
    total_disconnects: int = 0
    total_reconnects: int = 0
    connection_start_time: Optional[float] = None
    last_disconnect_time: Optional[float] = None
    last_reconnect_time: Optional[float] = None
    uptime_seconds: float = 0.0
    health_check_count: int = 0
    failed_health_checks: int = 0

    def record_connect(self):
        """Record a successful connection."""
        self.connection_start_time = time.time()
        self.last_reconnect_time = time.time()

    def record_disconnect(self):
        """Record a disconnection."""
        self.total_disconnects += 1
        self.last_disconnect_time = time.time()

        # Calculate uptime
        if self.connection_start_time:
            session_uptime = time.time() - self.connection_start_time
            self.uptime_seconds += session_uptime
            self.connection_start_time = None

    def record_reconnect(self):
        """Record a successful reconnection."""
        self.total_reconnects += 1
        self.record_connect()

    def get_current_uptime(self) -> float:
        """Get current session uptime."""
        if self.connection_start_time:
            return time.time() - self.connection_start_time
        return 0.0

    def get_total_uptime(self) -> float:
        """Get total accumulated uptime."""
        return self.uptime_seconds + self.get_current_uptime()


class ConnectionMonitor:
    """
    Monitors and maintains device connections.

    Features:
    - Periodic health checks
    - Automatic reconnection with exponential backoff
    - Status change notifications
    - Live status panel display
    """

    def __init__(
        self,
        adb_manager: ADBManager,
        shizuku_manager: ShizukuManager,
        config: Optional[Dict[str, Any]] = None
    ):
        self.adb = adb_manager
        self.shizuku = shizuku_manager
        self.config = config or {}

        # Configuration
        self.health_check_interval = self.config.get("health_check_interval", 30)
        self.connection_timeout = self.config.get("connection_timeout", 10)
        self.max_reconnect_attempts = self.config.get("max_reconnect_attempts", 10)
        self.reconnect_base_delay = self.config.get("reconnect_base_delay", 5)
        self.reconnect_max_delay = self.config.get("reconnect_max_delay", 300)

        # Device configuration
        self.device_ip = self.config.get("device_ip")
        self.device_port = self.config.get("device_port", 5555)
        self.pairing_code = self.config.get("pairing_code")
        self.pairing_port = self.config.get("pairing_port")

        # State
        self.state = ConnectionState.DISCONNECTED
        self.stats = ConnectionStats()
        self._reconnect_attempts = 0
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None

        # Callbacks
        self._on_connect: Set[Callable] = set()
        self._on_disconnect: Set[Callable] = set()
        self._on_state_change: Set[Callable] = set()

        # Console for output
        self.console = Console()

    def on_connect(self, callback: Callable):
        """Register a callback for connection events."""
        self._on_connect.add(callback)

    def on_disconnect(self, callback: Callable):
        """Register a callback for disconnection events."""
        self._on_disconnect.add(callback)

    def on_state_change(self, callback: Callable):
        """Register a callback for state changes."""
        self._on_state_change.add(callback)

    def _set_state(self, new_state: ConnectionState):
        """Set the connection state and notify callbacks."""
        old_state = self.state
        if old_state != new_state:
            self.state = new_state
            for callback in self._on_state_change:
                try:
                    callback(old_state, new_state)
                except Exception as e:
                    print(f"State change callback error: {e}")

    async def connect(self, ip: Optional[str] = None, port: Optional[int] = None) -> bool:
        """
        Establish initial connection to the device.

        Returns:
            True if connection succeeded
        """
        ip = ip or self.device_ip
        port = port or self.device_port

        if not ip:
            self.console.print("[red]Error: Device IP not configured[/red]")
            return False

        self._set_state(ConnectionState.CONNECTING)
        self.console.print(f"[yellow]Connecting to {ip}:{port}...[/yellow]")

        # Try to connect via ADB
        success = self.adb.connect(ip, port)

        if success:
            self._set_state(ConnectionState.CONNECTED)
            self.stats.record_connect()
            self._reconnect_attempts = 0

            device = self.adb.get_device()
            model = device.model if device else "Unknown"
            self.console.print(f"[green]Connected to {model} at {ip}:{port}[/green]")

            # Notify callbacks
            for callback in self._on_connect:
                try:
                    callback()
                except Exception as e:
                    print(f"Connect callback error: {e}")

            return True
        else:
            self._set_state(ConnectionState.ERROR)
            self.console.print(f"[red]Failed to connect to {ip}:{port}[/red]")
            return False

    async def disconnect(self) -> bool:
        """Disconnect from the device."""
        self.adb.disconnect()
        self._set_state(ConnectionState.DISCONNECTED)
        self.stats.record_disconnect()
        return True

    async def _reconnect(self) -> bool:
        """
        Attempt to reconnect with exponential backoff.

        Returns:
            True if reconnection succeeded
        """
        self._set_state(ConnectionState.RECONNECTING)
        self.stats.record_disconnect()

        while self._reconnect_attempts < self.max_reconnect_attempts:
            self._reconnect_attempts += 1

            # Calculate backoff delay
            delay = min(
                self.reconnect_base_delay * (2 ** (self._reconnect_attempts - 1)),
                self.reconnect_max_delay
            )

            self.console.print(
                f"[yellow]Reconnection attempt {self._reconnect_attempts}/"
                f"{self.max_reconnect_attempts} (waiting {delay}s)...[/yellow]"
            )

            await asyncio.sleep(delay)

            # Try to reconnect
            success = await self.connect()

            if success:
                self.stats.record_reconnect()
                self._reconnect_attempts = 0

                # Notify callbacks
                for callback in self._on_connect:
                    try:
                        callback()
                    except Exception as e:
                        print(f"Reconnect callback error: {e}")

                return True

        # Max attempts reached
        self._set_state(ConnectionState.ERROR)
        self.console.print("[red]Max reconnection attempts reached[/red]")

        # Notify disconnect callbacks
        for callback in self._on_disconnect:
            try:
                callback()
            except Exception as e:
                print(f"Disconnect callback error: {e}")

        return False

    async def health_check(self) -> bool:
        """
        Perform a health check on the connection.

        Returns:
            True if connection is healthy
        """
        self.stats.health_check_count += 1

        # Check ADB connection
        if not self.adb.is_device_connected():
            self.stats.failed_health_checks += 1
            return False

        # Try to get screen size as a basic test
        success, _ = self.adb.shell("echo health_check")
        if not success:
            self.stats.failed_health_checks += 1
            return False

        return True

    async def _monitor_loop(self):
        """Main monitoring loop."""
        while self._running:
            try:
                # Perform health check
                is_healthy = await self.health_check()

                if not is_healthy and self.state == ConnectionState.CONNECTED:
                    self.console.print(
                        "[yellow]Connection lost, initiating reconnection...[/yellow]"
                    )

                    # Notify disconnect callbacks
                    for callback in self._on_disconnect:
                        try:
                            callback()
                        except Exception as e:
                            print(f"Disconnect callback error: {e}")

                    # Attempt reconnection
                    await self._reconnect()

                # Wait for next check
                await asyncio.sleep(self.health_check_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.console.print(f"[red]Monitor error: {e}[/red]")
                await asyncio.sleep(self.health_check_interval)

    async def start_monitoring(self):
        """Start the connection monitoring loop."""
        if self._running:
            return

        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        self.console.print(
            f"[blue]Connection monitoring started "
            f"(interval: {self.health_check_interval}s)[/blue]"
        )

    async def stop_monitoring(self):
        """Stop the connection monitoring loop."""
        self._running = False

        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

        self.console.print("[blue]Connection monitoring stopped[/blue]")

    def get_status_panel(self) -> Panel:
        """Generate a Rich panel with current connection status."""
        # Connection status
        state_colors = {
            ConnectionState.CONNECTED: "green",
            ConnectionState.CONNECTING: "yellow",
            ConnectionState.RECONNECTING: "yellow",
            ConnectionState.DISCONNECTED: "red",
            ConnectionState.ERROR: "red",
        }
        state_color = state_colors.get(self.state, "white")

        # Device info
        device = self.adb.get_device()
        device_info = f"{device.model}" if device else "Not connected"

        # Shizuku status
        try:
            shizuku_status = self.shizuku.is_running()
            shizuku_str = "[green]Running[/green]" if shizuku_status else "[red]Not running[/red]"
        except:
            shizuku_str = "[gray]Unknown[/gray]"

        # Create table
        table = Table(show_header=False, box=None)
        table.add_column("Key", style="cyan")
        table.add_column("Value")

        table.add_row("State", f"[{state_color}]{self.state.name}[/{state_color}]")
        table.add_row("Device", device_info)
        table.add_row("Shizuku", shizuku_str)
        table.add_row("Session Uptime", f"{self.stats.get_current_uptime():.0f}s")
        table.add_row("Total Uptime", f"{self.stats.get_total_uptime():.0f}s")
        table.add_row("Disconnects", str(self.stats.total_disconnects))
        table.add_row("Reconnects", str(self.stats.total_reconnects))
        table.add_row("Health Checks", str(self.stats.health_check_count))
        table.add_row("Failed Checks", str(self.stats.failed_health_checks))

        return Panel(table, title="Connection Status", border_style=state_color)

    def display_status(self):
        """Display the current status panel."""
        self.console.print(self.get_status_panel())

    async def run_status_panel(self, refresh_rate: float = 1.0):
        """Run a live-updating status panel."""
        with Live(self.get_status_panel(), refresh_per_second=1/refresh_rate) as live:
            while self._running:
                live.update(self.get_status_panel())
                await asyncio.sleep(refresh_rate)

    def get_status(self) -> Dict[str, Any]:
        """Get detailed connection status."""
        device = self.adb.get_device()
        shizuku_info = self.shizuku.get_detailed_status()

        return {
            "state": self.state.name,
            "adb": self.adb.get_status(),
            "shizuku": shizuku_info,
            "device": {
                "serial": device.serial if device else None,
                "model": device.model if device else None,
                "ip": device.ip if device else None,
            },
            "stats": {
                "total_disconnects": self.stats.total_disconnects,
                "total_reconnects": self.stats.total_reconnects,
                "session_uptime": self.stats.get_current_uptime(),
                "total_uptime": self.stats.get_total_uptime(),
                "health_checks": self.stats.health_check_count,
                "failed_checks": self.stats.failed_health_checks,
            },
            "config": {
                "health_check_interval": self.health_check_interval,
                "max_reconnect_attempts": self.max_reconnect_attempts,
            },
        }