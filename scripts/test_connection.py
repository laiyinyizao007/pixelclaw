#!/usr/bin/env python3
"""
Connection Test Script for PixelClaw

Tests the connection to Pixel 8a device and verifies all components are working.

Usage:
    python scripts/test_connection.py
    python scripts/test_connection.py --pair
    python scripts/test_connection.py --connect
    python scripts/test_connection.py --full
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.device_connector import DeviceConnector
from monitors.connection_monitor import ConnectionMonitor


async def test_basic_connection():
    """Test basic ADB connection."""
    print("\n🔍 Testing Basic Connection\n")

    connector = DeviceConnector()
    success = connector.test_connection()

    return success


async def test_pairing():
    """Test device pairing."""
    print("\n🔗 Testing Device Pairing\n")

    connector = DeviceConnector()

    # Check if already paired
    if connector.adb.is_device_connected():
        print("Device already connected, skipping pairing")
        return True

    # Attempt pairing
    success = connector.pair()
    return success


async def test_connection_and_pairing():
    """Test both pairing and connection."""
    connector = DeviceConnector()

    # First try to connect
    if connector.adb.is_device_connected():
        print("Device already connected!")
        connector.test_connection()
        return True

    # Try pairing first
    print("Attempting to pair...")
    if connector.pair():
        print("Pairing successful, connecting...")
        if connector.connect():
            connector.test_connection()
            return True

    # Direct connection attempt
    print("Attempting direct connection...")
    if connector.connect():
        connector.test_connection()
        return True

    return False


async def test_with_monitoring():
    """Test with connection monitoring."""
    print("\n📊 Testing with Connection Monitoring\n")

    connector = DeviceConnector()

    # Create monitor
    monitor_config = {
        "health_check_interval": 30,
        "device_ip": connector.get_device_ip(),
        "device_port": connector.get_device_port(),
    }

    monitor = ConnectionMonitor(
        connector.adb,
        connector.shizuku,
        monitor_config
    )

    # Connect
    ip = connector.get_device_ip()
    port = connector.get_device_port()

    if not ip:
        print("❌ Device IP not configured")
        return False

    print(f"Connecting to {ip}:{port}...")
    success = await monitor.connect()

    if success:
        print("\n✅ Connection successful!")
        print("\nStarting monitoring for 60 seconds...")
        print("(Press Ctrl+C to stop early)\n")

        await monitor.start_monitoring()

        # Display status periodically
        try:
            for i in range(6):
                await asyncio.sleep(10)
                monitor.display_status()
        except KeyboardInterrupt:
            print("\n\nStopped by user")

        await monitor.stop_monitoring()
        return True
    else:
        print("\n❌ Connection failed!")
        return False


async def run_full_test():
    """Run comprehensive test suite."""
    print("\n" + "=" * 60)
    print("🧪 PIXELCLAW FULL CONNECTION TEST")
    print("=" * 60)

    tests = [
        ("Device Configuration", test_config),
        ("Pairing", test_pairing),
        ("Connection", test_connection_and_pairing),
        ("Basic Functionality", test_basic_connection),
    ]

    results = []

    for test_name, test_func in tests:
        print(f"\n{'─' * 60}")
        print(f"Running: {test_name}")
        print('─' * 60)

        try:
            result = await test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ Test failed with error: {e}")
            results.append((test_name, False))

    # Summary
    print("\n" + "=" * 60)
    print("📋 TEST SUMMARY")
    print("=" * 60)

    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {test_name}")

    passed = sum(1 for _, r in results if r)
    total = len(results)

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All tests passed! Your Pixel 8a is ready.")
    else:
        print("\n⚠️  Some tests failed. Check the output above.")

    return passed == total


async def test_config():
    """Test device configuration loading."""
    print("\n⚙️  Testing Configuration\n")

    connector = DeviceConnector()
    config = connector.get_device_config()

    if not config:
        print("❌ No device configuration found")
        return False

    print(f"✅ Device ID: {config.get('id')}")
    print(f"✅ Device Name: {config.get('name')}")
    print(f"✅ IP Address: {config.get('ip')}")
    print(f"✅ Port: {config.get('port')}")

    if config.get('pairing_code'):
        print(f"✅ Pairing Code: {'*' * len(config.get('pairing_code', ''))}")
    else:
        print("⚠️  No pairing code configured")

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Test PixelClaw connection to Pixel 8a"
    )
    parser.add_argument(
        "--pair",
        action="store_true",
        help="Test pairing only"
    )
    parser.add_argument(
        "--connect",
        action="store_true",
        help="Test connection only"
    )
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="Test with monitoring"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Run full test suite"
    )
    parser.add_argument(
        "--config",
        action="store_true",
        help="Test configuration only"
    )

    args = parser.parse_args()

    if args.pair:
        result = asyncio.run(test_pairing())
    elif args.connect:
        result = asyncio.run(test_connection_and_pairing())
    elif args.monitor:
        result = asyncio.run(test_with_monitoring())
    elif args.config:
        result = asyncio.run(test_config())
    elif args.full:
        result = asyncio.run(run_full_test())
    else:
        # Default: basic connection test
        result = asyncio.run(test_basic_connection())

    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
