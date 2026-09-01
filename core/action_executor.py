"""
Action Executor Module

Executes actions on the Android device:
- Tap, swipe, long press
- Text input
- Key events (home, back, etc.)
- Screenshot capture
- Waits and synchronization
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console

from ..monitors.adb_manager import ADBManager
from .screen_analyzer import ScreenAnalyzer


@dataclass
class ExecutionResult:
    """Result of an action execution."""
    success: bool
    action: str
    params: Dict[str, Any]
    execution_time: float
    error_message: Optional[str] = None
    screenshot_before: Optional[Any] = None
    screenshot_after: Optional[Any] = None


class ActionExecutor:
    """
    Executes actions on an Android device via ADB.

    Provides high-level methods for common device interactions
    with automatic retries and error handling.
    """

    def __init__(
        self,
        adb_manager: ADBManager,
        config: Optional[Dict[str, Any]] = None
    ):
        self.adb = adb_manager
        self.config = config or {}
        self.analyzer = ScreenAnalyzer()
        self.console = Console()

        # Configuration
        self.action_timeout = self.config.get("action_timeout", 30)
        self.default_tap_duration = self.config.get("tap_duration", 100)
        self.default_swipe_duration = self.config.get("swipe_duration", 300)
        self.step_delay = self.config.get("step_delay", 1.0)
        self.max_retries = self.config.get("max_retries", 3)

    async def execute(
        self,
        action: str,
        params: Optional[Dict[str, Any]] = None
    ) -> ExecutionResult:
        """
        Execute an action by name.

        Args:
            action: Action type (TAP, SWIPE, TYPE, etc.)
            params: Action parameters

        Returns:
            ExecutionResult
        """
        params = params or {}
        start_time = time.time()

        # Take screenshot before
        screenshot_before = self.adb.screenshot()

        # Map action to method
        action_methods = {
            "TAP": self.tap,
            "SWIPE": self.swipe,
            "LONG_PRESS": self.long_press,
            "TYPE": self.type_text,
            "BACK": self.press_back,
            "HOME": self.press_home,
            "RECENT": self.press_recent,
            "WAIT": self.wait,
        }

        method = action_methods.get(action.upper())

        if not method:
            return ExecutionResult(
                success=False,
                action=action,
                params=params,
                execution_time=time.time() - start_time,
                error_message=f"Unknown action: {action}"
            )

        # Execute with retries
        for attempt in range(self.max_retries):
            try:
                success = await method(**params)

                if success:
                    # Take screenshot after
                    await asyncio.sleep(0.5)  # Brief wait for UI update
                    screenshot_after = self.adb.screenshot()

                    return ExecutionResult(
                        success=True,
                        action=action,
                        params=params,
                        execution_time=time.time() - start_time,
                        screenshot_before=screenshot_before,
                        screenshot_after=screenshot_after,
                    )

                if attempt < self.max_retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))

            except Exception as e:
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    return ExecutionResult(
                        success=False,
                        action=action,
                        params=params,
                        execution_time=time.time() - start_time,
                        error_message=str(e),
                        screenshot_before=screenshot_before,
                    )

        return ExecutionResult(
            success=False,
            action=action,
            params=params,
            execution_time=time.time() - start_time,
            error_message="Max retries exceeded",
            screenshot_before=screenshot_before,
        )

    async def tap(self, x: int, y: int, **kwargs) -> bool:
        """Tap at coordinates."""
        return self.adb.tap(int(x), int(y))

    async def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration: Optional[int] = None,
        **kwargs
    ) -> bool:
        """Swipe from (x1, y1) to (x2, y2)."""
        duration = duration or self.default_swipe_duration
        return self.adb.swipe(int(x1), int(y1), int(x2), int(y2), int(duration))

    async def long_press(
        self,
        x: int,
        y: int,
        duration: Optional[int] = None,
        **kwargs
    ) -> bool:
        """Long press at coordinates."""
        duration = duration or 1000
        return self.adb.long_press(int(x), int(y), int(duration))

    async def type_text(self, text: str, **kwargs) -> bool:
        """Type text on the device."""
        return self.adb.type_text(text)

    async def press_back(self, **kwargs) -> bool:
        """Press back button."""
        return self.adb.press_back()

    async def press_home(self, **kwargs) -> bool:
        """Press home button."""
        return self.adb.press_home()

    async def press_recent(self, **kwargs) -> bool:
        """Show recent apps."""
        return self.adb.press_recent()

    async def wait(self, seconds: float = 1.0, **kwargs) -> bool:
        """Wait for specified seconds."""
        await asyncio.sleep(float(seconds))
        return True

    async def tap_text(self, text: str, **kwargs) -> bool:
        """Tap on text element containing the specified text."""
        screenshot = self.adb.screenshot()
        if not screenshot:
            return False

        center = self.analyzer.find_element_by_text(screenshot, text)
        if center:
            x, y = center
            return await self.tap(x, y)

        return False

    async def scroll_down(
        self,
        start_y: Optional[int] = None,
        end_y: Optional[int] = None,
        x: Optional[int] = None,
        **kwargs
    ) -> bool:
        """Scroll down the screen."""
        # Get screen size
        size = self.adb.get_screen_size()
        if not size:
            return False

        width, height = size

        x = x or width // 2
        start_y = start_y or int(height * 0.7)
        end_y = end_y or int(height * 0.3)

        return await self.swipe(x, start_y, x, end_y)

    async def scroll_up(
        self,
        start_y: Optional[int] = None,
        end_y: Optional[int] = None,
        x: Optional[int] = None,
        **kwargs
    ) -> bool:
        """Scroll up the screen."""
        size = self.adb.get_screen_size()
        if not size:
            return False

        width, height = size

        x = x or width // 2
        start_y = start_y or int(height * 0.3)
        end_y = end_y or int(height * 0.7)

        return await self.swipe(x, start_y, x, end_y)

    async def find_and_tap(
        self,
        text: Optional[str] = None,
        element_type: Optional[str] = None,
        **kwargs
    ) -> bool:
        """
        Find an element and tap it.

        Args:
            text: Text content to search for
            element_type: Type of element to find
        """
        screenshot = self.adb.screenshot()
        if not screenshot:
            return False

        analysis = self.analyzer.analyze(screenshot)

        for element in analysis.elements:
            if text and element.text and text.lower() in element.text.lower():
                x, y = element.center
                return await self.tap(x, y)

            if element_type and element.element_type == element_type:
                x, y = element.center
                return await self.tap(x, y)

        return False

    def get_screen_size(self) -> Optional[Tuple[int, int]]:
        """Get the device screen size."""
        return self.adb.get_screen_size()

    def take_screenshot(self):
        """Capture a screenshot."""
        return self.adb.screenshot()

    def start_app(self, package: str, activity: Optional[str] = None) -> bool:
        """Start an app."""
        return self.adb.start_app(package, activity)

    def stop_app(self, package: str) -> bool:
        """Force stop an app."""
        return self.adb.stop_app(package)
