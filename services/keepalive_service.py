"""
Keepalive Service Module

Background service that maintains connections:
- Periodic health checks
- Automatic reconnection
- Connection state persistence
- Logging and monitoring
"""

import asyncio
import json
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from rich.console import Console
from rich.table import Table

from ..monitors.connection_monitor import ConnectionMonitor


@dataclass
class ServiceStats:
    """Service statistics."""
    start_time: float = field(default_factory=time.time)
    total_health_checks: int = 0
    total_reconnections: int = 0
    total_errors: int = 0
    last_error: Optional[str] = None
    last_error_time: Optional[float] = None

    def record_health_check(self):
        self.total_health_checks += 1

    def record_reconnection(self):
        self.total_reconnections += 1

    def record_error(self, error: str):
        self.total_errors += 1
        self.last_error = error
        self.last_error_time = time.time()

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self.start_time


class KeepaliveService:
    """
    Background service that maintains device connections.

    This service runs continuously to ensure the device connection
    remains active, automatically reconnecting if the connection drops.

    Features:
    - Runs as a daemon process
    - Periodic health checks
    - Automatic reconnection with exponential backoff
    - Status logging
    - Signal handling for graceful shutdown
    """

    PID_FILE = "/tmp/pixelclaw_keepalive.pid"
    LOG_FILE = "./logs/keepalive.log"
    STATUS_FILE = "./logs/keepalive_status.json"

    def __init__(
        self,
        connection_monitor: ConnectionMonitor,
        config: Optional[Dict[str, Any]] = None
    ):
        self.monitor = connection_monitor
        self.config = config or {}
        self.console = Console()
        self.stats = ServiceStats()

        self._running = False
        self._shutdown_event = asyncio.Event()

        # Ensure log directory exists
        os.makedirs(os.path.dirname(self.LOG_FILE), exist_ok=True)

    def _write_pid(self):
        """Write PID to file."""
        with open(self.PID_FILE, 'w') as f:
            f.write(str(os.getpid()))

    def _remove_pid(self):
        """Remove PID file."""
        if os.path.exists(self.PID_FILE):
            os.remove(self.PID_FILE)

    def _is_running(self) -> bool:
        """Check if service is already running."""
        if not os.path.exists(self.PID_FILE):
            return False

        try:
            with open(self.PID_FILE, 'r') as f:
                pid = int(f.read().strip())

            # Check if process exists
            os.kill(pid, 0)
            return True
        except (ValueError, OSError, ProcessLookupError):
            return False

    def _log(self, message: str, level: str = "INFO"):
        """Log a message to file and console."""
        timestamp = datetime.now().isoformat()
        log_entry = f"[{timestamp}] [{level}] {message}\n"

        # Write to file
        with open(self.LOG_FILE, 'a') as f:
            f.write(log_entry)

        # Print to console
        color_map = {
            "INFO": "blue",
            "WARNING": "yellow",
            "ERROR": "red",
            "SUCCESS": "green",
        }
        color = color_map.get(level, "white")
        self.console.print(f"[{color}]{message}[/{color}]")

    def _save_status(self):
        """Save current status to file."""
        status = {
            "running": self._running,
            "timestamp": time.time(),
            "uptime_seconds": self.stats.uptime_seconds,
            "health_checks": self.stats.total_health_checks,
            "reconnections": self.stats.total_reconnections,
            "errors": self.stats.total_errors,
            "last_error": self.stats.last_error,
            "last_error_time": self.stats.last_error_time,
            "connection": self.monitor.get_status(),
        }

        with open(self.STATUS_FILE, 'w') as f:
            json.dump(status, f, indent=2, default=str)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        self._log(f"Received signal {signum}, shutting down...", "WARNING")
        self._shutdown_event.set()

    async def _health_check_loop(self):
        """Main health check loop."""
        while self._running and not self._shutdown_event.is_set():
            try:
                self.stats.record_health_check()

                # Perform health check
                is_healthy = await self.monitor.health_check()

                if not is_healthy:
                    self._log("Health check failed, initiating reconnection", "WARNING")
                    self.stats.record_reconnection()

                    # Attempt reconnection
                    success = await self.monitor.connect()

                    if success:
                        self._log("Reconnection successful", "SUCCESS")
                    else:
                        self._log("Reconnection failed, will retry", "ERROR")
                        self.stats.record_error("Reconnection failed")

                # Save status periodically
                if self.stats.total_health_checks % 10 == 0:
                    self._save_status()

                # Wait for next check or shutdown
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=self.monitor.health_check_interval
                    )
                except asyncio.TimeoutError:
                    pass

            except Exception as e:
                error_msg = str(e)
                self._log(f"Error in health check loop: {error_msg}", "ERROR")
                self.stats.record_error(error_msg)
                await asyncio.sleep(5)

    async def start(self):
        """Start the keepalive service."""
        if self._is_running():
            self.console.print("[yellow]Service is already running[/yellow]")
            return False

        if self._running:
            self.console.print("[yellow]Service is already started[/yellow]")
            return False

        # Set up signal handlers
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        self._running = True
        self._write_pid()

        self._log("=" * 50)
        self._log("PixelClaw Keepalive Service Started")
        self._log(f"Health check interval: {self.monitor.health_check_interval}s")
        self._log("=" * 50)

        try:
            # Start the monitor
            await self.monitor.start_monitoring()

            # Run health check loop
            await self._health_check_loop()

        except Exception as e:
            self._log(f"Service error: {e}", "ERROR")
            self.stats.record_error(str(e))

        finally:
            await self.stop()

        return True

    async def stop(self):
        """Stop the keepalive service."""
        if not self._running:
            return

        self._running = False
        self._shutdown_event.set()

        self._log("Stopping service...")

        await self.monitor.stop_monitoring()

        self._save_status()
        self._remove_pid()

        self._log("Service stopped")
        self._log(f"Total uptime: {self.stats.uptime_seconds:.0f}s")
        self._log(f"Health checks: {self.stats.total_health_checks}")
        self._log(f"Reconnections: {self.stats.total_reconnections}")
        self._log("=" * 50)

    def status(self) -> Dict[str, Any]:
        """Get current service status."""
        is_running = self._is_running()

        status_data = {
            "service_running": is_running,
            "pid": None,
            "stats": {
                "uptime_seconds": self.stats.uptime_seconds,
                "health_checks": self.stats.total_health_checks,
                "reconnections": self.stats.total_reconnections,
                "errors": self.stats.total_errors,
            },
        }

        if is_running and os.path.exists(self.PID_FILE):
            with open(self.PID_FILE, 'r') as f:
                status_data["pid"] = int(f.read().strip())

        # Load detailed status if available
        if os.path.exists(self.STATUS_FILE):
            try:
                with open(self.STATUS_FILE, 'r') as f:
                    file_status = json.load(f)
                    status_data["last_status"] = file_status
            except json.JSONDecodeError:
                pass

        return status_data

    def display_status(self):
        """Display service status in a table."""
        status = self.status()

        table = Table(title="Keepalive Service Status")
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Running", "Yes" if status["service_running"] else "No")
        table.add_row("PID", str(status.get("pid", "N/A")))
        table.add_row(
            "Uptime",
            f"{status['stats']['uptime_seconds']:.0f}s"
        )
        table.add_row(
            "Health Checks",
            str(status['stats']['health_checks'])
        )
        table.add_row(
            "Reconnections",
            str(status['stats']['reconnections'])
        )
        table.add_row(
            "Errors",
            str(status['stats']['errors'])
        )

        self.console.print(table)

    @classmethod
    def stop_running_service(cls):
        """Stop a running service instance."""
        if not os.path.exists(cls.PID_FILE):
            print("No PID file found, service may not be running")
            return False

        try:
            with open(cls.PID_FILE, 'r') as f:
                pid = int(f.read().strip())

            os.kill(pid, signal.SIGTERM)
            print(f"Sent shutdown signal to process {pid}")
            return True

        except (ValueError, ProcessLookupError):
            print("Process not found, cleaning up PID file")
            os.remove(cls.PID_FILE)
            return False

        except PermissionError:
            print("Permission denied. Try running with sudo.")
            return False
