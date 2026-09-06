"""
PixelClaw - OpenClaw Automation System for Android

A vision-based automation system for controlling Android devices
using multi-level fallback strategies and ADB.

Version: 0.1.0
"""

__version__ = "0.1.0"
__author__ = "PixelClaw Team"

try:
    from .core.device_connector import DeviceConnector
    from .core.vision_agent import VisionAgent
    from .monitors.connection_monitor import ConnectionMonitor
    from .strategies.fallback_manager import FallbackManager
except ImportError:
    pass

__all__ = [
    "DeviceConnector",
    "VisionAgent",
    "ConnectionMonitor",
    "FallbackManager",
]
