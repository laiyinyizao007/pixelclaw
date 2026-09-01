#!/usr/bin/env python3
"""
PixelClaw Main Entry Point

Usage:
    python -m pixelclaw
    python -m pixelclaw --connect
    python -m pixelclaw --monitor
    python -m pixelclaw --task "Open Settings app"
    python -m pixelclaw --interactive
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from .core.device_connector import DeviceConnector
from .core.vision_agent import VisionAgent
from .monitors.connection_monitor import ConnectionMonitor
from .services.keepalive_service import KeepaliveService
from .strategies.fallback_manager import FallbackManager


console = Console()


def print_banner():
    """Print the PixelClaw banner."""
    banner = """
    ╔═══════════════════════════════════════════════════════════╗
    ║                                                           ║
    ║   ██████╗ ██╗██╗  ██╗███████╗██╗      ██████╗ █████╗ ██╗  ║
    ║   ██╔══██╗██║╚██╗██╔╝██╔════╝██║     ██╔════╝██╔══██╗██║  ║
    ║   ██████╔╝██║ ╚███╔╝ █████╗  ██║     ██║     ███████║██║  ║
    ║   ██╔═══╝ ██║ ██╔██╗ ██╔══╝  ██║     ██║     ██╔══██║██║  ║
    ║   ██║     ██║██╔╝ ██╗███████╗███████╗╚██████╗██║  ██║███████╗
    ║   ╚═╝     ╚═╝╚═╝  ╚═╝╚══════╝╚══════╝ ╚═════╝╚═╝  ╚═╝╚══════╝
    ║                                                           ║
    ║        OpenClaw Automation System for Android            ║
    ║                     Version 0.1.0                         ║
    ╚═══════════════════════════════════════════════════════════╝
    """
    console.print(banner, style="cyan")


def load_config():
    """Load configuration from files."""
    config = {}

    # Load settings
    settings_path = Path("config/settings.yaml")
    if settings_path.exists():
        import yaml
        with open(settings_path) as f:
            config['settings'] = yaml.safe_load(f)

    # Load devices
    devices_path = Path("config/devices.json")
    if devices_path.exists():
        with open(devices_path) as f:
            config['devices'] = json.load(f)

    # Load API keys
    api_keys_path = Path("config/api_keys.json")
    if api_keys_path.exists():
        with open(api_keys_path) as f:
            config['api_keys'] = json.load(f)

    return config


async def cmd_connect(args):
    """Connect to device command."""
    connector = DeviceConnector()

    if connector.adb.is_device_connected():
        console.print("[green]Already connected to device[/green]")
        connector.test_connection()
        return 0

    console.print("[yellow]Connecting to device...[/yellow]")

    # Try pairing if needed
    if args.pair:
        if connector.pair():
            console.print("[green]Paired successfully[/green]")

    # Connect
    if connector.connect():
        console.print("[green]Connected successfully![/green]")

        if args.test:
            connector.test_connection()
        return 0
    else:
        console.print("[red]Connection failed[/red]")
        return 1


async def cmd_monitor(args):
    """Monitor connection command."""
    config = load_config()
    connector = DeviceConnector()

    monitor_config = {
        "health_check_interval": args.interval or 30,
        "device_ip": connector.get_device_ip(),
        "device_port": connector.get_device_port(),
    }

    monitor = ConnectionMonitor(
        connector.adb,
        connector.shizuku,
        monitor_config
    )

    # Connect first
    ip = connector.get_device_ip()
    port = connector.get_device_port()

    if not ip:
        console.print("[red]Device IP not configured[/red]")
        return 1

    console.print(f"[yellow]Connecting to {ip}:{port}...[/yellow]")

    if await monitor.connect():
        console.print("[green]Connected![/green]\n")
        console.print("Starting monitoring (Press Ctrl+C to stop)...\n")

        await monitor.start_monitoring()

        try:
            if args.panel:
                await monitor.run_status_panel()
            else:
                while True:
                    await asyncio.sleep(args.interval or 30)
                    monitor.display_status()
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopping...[/yellow]")

        await monitor.stop_monitoring()
        return 0
    else:
        console.print("[red]Failed to connect[/red]")
        return 1


async def cmd_task(args):
    """Execute a task command."""
    config = load_config()

    # Initialize components
    connector = DeviceConnector()

    # Check connection
    if not connector.adb.is_device_connected():
        console.print("[yellow]Not connected. Attempting to connect...[/yellow]")
        if not connector.connect():
            console.print("[red]Failed to connect to device[/red]")
            return 1

    # Initialize fallback manager with API keys
    strategies_config = config.get('settings', {}).get('strategies', {})

    # Inject API key
    if 'step1v' in strategies_config and 'api_keys' in config:
        strategies_config['step1v']['api_key'] = (
            config['api_keys'].get('step1v', {}).get('api_key', '')
        )

    fallback_manager = FallbackManager(strategies_config)

    # Initialize vision agent
    agent_config = config.get('settings', {}).get('vision_agent', {})
    agent = VisionAgent(connector, fallback_manager, agent_config)

    # Execute task
    goal = args.goal or args.task
    max_steps = args.max_steps or 30

    console.print(f"[bold]Task:[/bold] {goal}")
    console.print(f"[dim]Max steps: {max_steps}[/dim]\n")

    result = await agent.execute_task(goal, max_steps)

    # Print result
    if result.success:
        console.print(f"\n[green bold]✓ Task completed successfully![/green bold]")
    else:
        console.print(f"\n[red bold]✗ Task failed[/red bold]")
        console.print(f"[red]{result.error_message}[/red]")

    console.print(f"\n[dim]Steps taken: {result.steps_taken}")
    console.print(f"Execution time: {result.execution_time:.1f}s[/dim]")

    # Save history if requested
    if args.save_history:
        agent.export_history(args.save_history)

    return 0 if result.success else 1


async def cmd_interactive(args):
    """Interactive mode command."""
    print_banner()

    config = load_config()
    connector = DeviceConnector()

    # Check connection
    if not connector.adb.is_device_connected():
        console.print("[yellow]Not connected. Attempting to connect...[/yellow]")
        if not connector.connect():
            console.print("[red]Failed to connect. Some features may not work.[/red]")
        else:
            console.print("[green]Connected![/green]")
    else:
        console.print("[green]Already connected[/green]")

    # Initialize agent
    strategies_config = config.get('settings', {}).get('strategies', {})
    if 'step1v' in strategies_config and 'api_keys' in config:
        strategies_config['step1v']['api_key'] = (
            config['api_keys'].get('step1v', {}).get('api_key', '')
        )

    fallback_manager = FallbackManager(strategies_config)
    agent_config = config.get('settings', {}).get('vision_agent', {})
    agent = VisionAgent(connector, fallback_manager, agent_config)

    console.print("\n[bold cyan]Interactive Mode[/bold cyan]")
    console.print("Enter a goal or command (help for options, quit to exit)\n")

    while True:
        try:
            user_input = console.input("[bold green]pixelclaw>[/bold green] ").strip()

            if not user_input:
                continue

            if user_input.lower() in ('quit', 'exit', 'q'):
                console.print("[yellow]Goodbye![/yellow]")
                break

            if user_input.lower() in ('help', 'h', '?'):
                print_help()
                continue

            if user_input.lower() == 'status':
                connector.test_connection()
                continue

            if user_input.lower().startswith('tap '):
                # Direct tap command
                coords = user_input[4:].split()
                if len(coords) == 2:
                    x, y = map(int, coords)
                    connector.adb.tap(x, y)
                    console.print(f"[green]Tapped at ({x}, {y})[/green]")
                continue

            if user_input.lower() == 'screenshot':
                img = connector.adb.screenshot()
                if img:
                    img.save("screenshot.png")
                    console.print("[green]Screenshot saved to screenshot.png[/green]")
                continue

            # Treat as a goal for the agent
            result = await agent.execute_task(user_input, max_steps=20)

            if result.success:
                console.print("[green]✓ Done[/green]")
            else:
                console.print(f"[red]✗ Failed: {result.error_message}[/red]")

        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted. Type 'quit' to exit.[/yellow]")
        except EOFError:
            break

    return 0


def print_help():
    """Print help information."""
    help_text = """
Available commands:
  help          Show this help message
  status        Check device connection status
  screenshot    Capture and save a screenshot
  tap x y       Tap at coordinates (x, y)
  quit/exit     Exit interactive mode

You can also type any natural language goal, such as:
  - "Open the Settings app"
  - "Turn on WiFi"
  - "Search for 'weather' in Chrome"
    """
    console.print(help_text)


async def cmd_service(args):
    """Background service command."""
    connector = DeviceConnector()

    monitor_config = {
        "health_check_interval": args.interval or 30,
        "device_ip": connector.get_device_ip(),
        "device_port": connector.get_device_port(),
    }

    monitor = ConnectionMonitor(
        connector.adb,
        connector.shizuku,
        monitor_config
    )

    service = KeepaliveService(monitor)

    if args.stop:
        KeepaliveService.stop_running_service()
        return 0

    if args.status:
        service.display_status()
        return 0

    # Start service
    console.print("[yellow]Starting keepalive service...[/yellow]")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")

    return 0 if await service.start() else 1


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="PixelClaw - OpenClaw Automation System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          Show banner and basic info
  %(prog)s --connect                Connect to device
  %(prog)s --connect --test         Connect and run tests
  %(prog)s --monitor                Monitor connection
  %(prog)s --task "Open Settings"   Execute a task
  %(prog)s --interactive            Start interactive mode
        """
    )

    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0"
    )

    # Connection commands
    parser.add_argument(
        "--connect",
        action="store_true",
        help="Connect to device"
    )
    parser.add_argument(
        "--pair",
        action="store_true",
        help="Pair with device before connecting"
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run connection test after connecting"
    )

    # Monitor commands
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="Monitor device connection"
    )
    parser.add_argument(
        "--panel",
        action="store_true",
        help="Show live status panel"
    )
    parser.add_argument(
        "--interval",
        type=int,
        help="Health check interval in seconds"
    )

    # Task commands
    parser.add_argument(
        "--task",
        "--goal",
        dest="task",
        help="Task goal to execute"
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=30,
        help="Maximum steps for task execution"
    )
    parser.add_argument(
        "--save-history",
        help="Save action history to file"
    )

    # Interactive mode
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Start interactive mode"
    )

    # Service commands
    parser.add_argument(
        "--service",
        action="store_true",
        help="Run background service"
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop background service"
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show service status"
    )

    args = parser.parse_args()

    # Default: show banner
    if len(sys.argv) == 1:
        print_banner()

        # Show quick status
        connector = DeviceConnector()
        if connector.adb.is_device_connected():
            console.print("[green]✓ Device connected[/green]")
            device = connector.adb.get_device()
            if device:
                console.print(f"  Model: {device.model or 'Unknown'}")
                console.print(f"  IP: {device.ip or 'USB'}")
        else:
            console.print("[yellow]⚠ Device not connected[/yellow]")
            console.print("  Run: pixelclaw --connect")

        console.print("\n[dim]Use --help for available commands[/dim]")
        return 0

    # Dispatch commands
    if args.connect:
        return await cmd_connect(args)
    elif args.monitor:
        return await cmd_monitor(args)
    elif args.task:
        return await cmd_task(args)
    elif args.interactive:
        return await cmd_interactive(args)
    elif args.service or args.stop or args.status:
        return await cmd_service(args)

    return 0


def entry_point():
    """Entry point for console scripts."""
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    entry_point()
