"""
Vision Agent Module

The main Vision Agent that coordinates:
- Screen analysis
- Strategy-based decision making
- Action execution
- Task completion tracking
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable

from rich.console import Console
from rich.progress import Progress, TaskID

from ..strategies.base import Action
from ..strategies.fallback_manager import FallbackManager
from .action_executor import ActionExecutor
from .device_connector import DeviceConnector
from .screen_analyzer import ScreenAnalyzer
from ..memory import MemoryManager


@dataclass
class TaskResult:
    """Result of a task execution."""
    success: bool
    goal: str
    steps_taken: int
    execution_time: float
    final_screenshot: Optional[Any] = None
    action_history: List[Dict[str, Any]] = field(default_factory=list)
    error_message: Optional[str] = None


class VisionAgent:
    """
    Vision-based automation agent for Android devices.

    Combines computer vision, VLM-based decision making, and ADB control
to automate tasks on Android devices.

    Features:
    - Multi-level fallback strategy (Cloud VLM -> Local VLM -> OCR)
    - Automatic retry and error recovery
    - Action history and state tracking
    - Configurable step limits and timeouts
    """

    def __init__(
        self,
        device_connector: DeviceConnector,
        fallback_manager: Optional[FallbackManager] = None,
        config: Optional[Dict[str, Any]] = None
    ):
        self.device = device_connector
        self.adb = device_connector.adb
        self.fallback = fallback_manager or FallbackManager()
        self.config = config or {}
        self.console = Console()

        # Initialize memory system
        self.memory = MemoryManager(self.config.get("memory", {}))

        # Initialize components
        self.executor = ActionExecutor(self.adb, self.config)
        self.analyzer = ScreenAnalyzer()

        # Configuration
        self.max_steps = self.config.get("max_steps", 50)
        self.step_delay = self.config.get("step_delay", 1.0)
        self.error_recovery_attempts = self.config.get("error_recovery_attempts", 3)
        self.confirm_dangerous = self.config.get("confirm_dangerous_actions", True)

        # State
        self._running = False
        self._current_goal: Optional[str] = None
        self._action_history: List[Dict[str, Any]] = []
        self._step_count = 0
        self._callback: Optional[Callable] = None

    def set_progress_callback(self, callback: Callable[[int, int, str], None]):
        """Set a callback for progress updates."""
        self._callback = callback

    def _notify_progress(self, step: int, total: int, message: str):
        """Notify progress callback."""
        if self._callback:
            try:
                self._callback(step, total, message)
            except Exception as e:
                self.console.print(f"[red]Callback error: {e}[/red]")

    async def execute_task(
        self,
        goal: str,
        max_steps: Optional[int] = None
    ) -> TaskResult:
        """
        Execute a task using the vision agent.

        Args:
            goal: The task goal/description
            max_steps: Maximum number of steps (overrides config)

        Returns:
            TaskResult with execution details
        """
        start_time = time.time()
        max_steps = max_steps or self.max_steps

        self._current_goal = goal
        self._action_history = []
        self._step_count = 0
        self._running = True

        self.console.print(f"[bold blue]Starting task:[/bold blue] {goal}")
        self.console.print(f"[dim]Max steps: {max_steps}[/dim]\n")

        try:
            with Progress() as progress:
                task_id = progress.add_task("[cyan]Executing...", total=max_steps)

                while self._running and self._step_count < max_steps:
                    self._step_count += 1
                    progress.update(task_id, advance=1)

                    # Get screenshot
                    self._notify_progress(
                        self._step_count,
                        max_steps,
                        "Capturing screenshot..."
                    )

                    screenshot = self.adb.screenshot()
                    if screenshot is None:
                        error_msg = "Failed to capture screenshot"
                        self.console.print(f"[red]{error_msg}[/red]")

                        # Finalize memory record for screenshot failure
                        await self.memory.finalize_memory_record("failure")

                        return TaskResult(
                            success=False,
                            goal=goal,
                            steps_taken=self._step_count,
                            execution_time=time.time() - start_time,
                            error_message=error_msg,
                            action_history=self._action_history
                        )

                    # Analyze and decide action
                    self._notify_progress(
                        self._step_count,
                        max_steps,
                        "Analyzing screen..."
                    )

                    # Get enhanced context from memory system
                    enhanced_context = await self.memory.get_enhanced_context(
                        goal=goal,
                        action_history=self._action_history,
                        current_screenshot=screenshot
                    )

                    result = await self.fallback.analyze(
                        screenshot,
                        goal,
                        context=enhanced_context
                    )

                    if not result.success:
                        # Check if human intervention needed
                        if result.metadata.get("needs_human"):
                            error_msg = "Human intervention required"
                            self.console.print(f"[yellow]{error_msg}[/yellow]")

                            # Finalize memory record for human intervention needed
                            await self.memory.finalize_memory_record("needs_human")

                            return TaskResult(
                                success=False,
                                goal=goal,
                                steps_taken=self._step_count,
                                execution_time=time.time() - start_time,
                                error_message=error_msg,
                                final_screenshot=screenshot,
                                action_history=self._action_history
                            )

                        # Try error recovery
                        if self._step_count < self.error_recovery_attempts:
                            self.console.print(
                                f"[yellow]Strategy failed, retrying...[/yellow]"
                            )
                            await asyncio.sleep(1)
                            continue

                        error_msg = f"All strategies failed: {result.error_message}"
                        self.console.print(f"[red]{error_msg}[/red]")

                        # Finalize memory record for strategy failure
                        await self.memory.finalize_memory_record("failure")

                        return TaskResult(
                            success=False,
                            goal=goal,
                            steps_taken=self._step_count,
                            execution_time=time.time() - start_time,
                            error_message=error_msg,
                            final_screenshot=screenshot,
                            action_history=self._action_history
                        )

                    # Execute action
                    action = result.action
                    if action is None:
                        error_msg = "No action returned from strategy"
                        self.console.print(f"[red]{error_msg}[/red]")
                        continue

                    self._notify_progress(
                        self._step_count,
                        max_steps,
                        f"Executing: {action.action_type.name}"
                    )

                    self.console.print(
                        f"[green]Step {self._step_count}:[/green] "
                        f"{action.action_type.name} - {action.reasoning}"
                    )

                    # Check for dangerous actions
                    if self._is_dangerous_action(action):
                        self.console.print(
                            f"[yellow]Warning: Potentially dangerous action detected[/yellow]"
                        )
                        if self.confirm_dangerous:
                            # In practice, could prompt user here
                            self.console.print("[dim]Skipping confirmation in auto mode[/dim]")

                    # Execute the action
                    exec_result = await self.executor.execute(
                        action.action_type.name,
                        action.params
                    )

                    # Record action
                    self._action_history.append({
                        "step": self._step_count,
                        "action": action.to_dict(),
                        "success": exec_result.success,
                        "execution_time": exec_result.execution_time,
                        "strategy_level": result.level.name,
                    })

                    # Capture action for memory system learning
                    await self.memory.capture_action(
                        action=action.to_dict(),
                        result={
                            "success": exec_result.success,
                            "execution_time": exec_result.execution_time,
                            "error_message": exec_result.error_message
                        },
                        context={
                            "step": self._step_count,
                            "goal": goal,
                            "screenshot": screenshot,
                            "strategy_level": result.level.name
                        }
                    )

                    if not exec_result.success:
                        self.console.print(
                            f"[red]Action failed: {exec_result.error_message}[/red]"
                        )

                        # Try error recovery
                        if self._step_count < self.error_recovery_attempts:
                            self.console.print("[yellow]Attempting recovery...[/yellow]")
                            await asyncio.sleep(1)
                            continue

                        # Finalize memory record for action failure
                        await self.memory.finalize_memory_record("failure")

                        return TaskResult(
                            success=False,
                            goal=goal,
                            steps_taken=self._step_count,
                            execution_time=time.time() - start_time,
                            error_message=exec_result.error_message,
                            final_screenshot=screenshot,
                            action_history=self._action_history
                        )

                    # Check for task completion
                    if action.action_type.name == "COMPLETE":
                        self.console.print(
                            f"[bold green]Task completed successfully![/bold green]"
                        )

                        # Finalize memory record for successful completion
                        await self.memory.finalize_memory_record("success")

                        return TaskResult(
                            success=True,
                            goal=goal,
                            steps_taken=self._step_count,
                            execution_time=time.time() - start_time,
                            final_screenshot=exec_result.screenshot_after,
                            action_history=self._action_history
                        )

                    # Delay between steps
                    await asyncio.sleep(self.step_delay)

            # Max steps reached
            self.console.print(f"[yellow]Max steps ({max_steps}) reached[/yellow]")

            # Finalize memory record for partial completion
            await self.memory.finalize_memory_record("partial")

            return TaskResult(
                success=False,
                goal=goal,
                steps_taken=self._step_count,
                execution_time=time.time() - start_time,
                error_message="Max steps exceeded",
                action_history=self._action_history
            )

        except Exception as e:
            self.console.print(f"[red]Unexpected error: {e}[/red]")

            # Finalize memory record for failure
            try:
                await self.memory.finalize_memory_record("failure")
            except Exception as mem_error:
                self.console.print(f"[dim]Memory finalization error: {mem_error}[/dim]")

            return TaskResult(
                success=False,
                goal=goal,
                steps_taken=self._step_count,
                execution_time=time.time() - start_time,
                error_message=str(e),
                action_history=self._action_history
            )

        finally:
            self._running = False

    def _is_dangerous_action(self, action: Action) -> bool:
        """Check if an action is potentially dangerous."""
        dangerous_types = {"DELETE", "UNINSTALL", "FACTORY_RESET", "WIPE"}
        return action.action_type.name in dangerous_types

    def stop(self):
        """Stop the current task execution."""
        self._running = False
        self.console.print("[yellow]Stopping task execution...[/yellow]")

    def get_status(self) -> Dict[str, Any]:
        """Get the current agent status."""
        return {
            "running": self._running,
            "current_goal": self._current_goal,
            "step_count": self._step_count,
            "max_steps": self.max_steps,
            "action_history_count": len(self._action_history),
            "fallback_status": self.fallback.get_status(),
        }

    def export_history(self, filepath: str):
        """Export action history to a JSON file."""
        data = {
            "goal": self._current_goal,
            "steps": self._step_count,
            "actions": self._action_history,
        }

        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2, default=str)

        self.console.print(f"[green]History exported to {filepath}[/green]")
