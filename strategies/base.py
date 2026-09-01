"""
Base Strategy Module

Defines the abstract base class for all vision strategies and the result format.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, Optional, List
from PIL import Image
import time


class StrategyLevel(Enum):
    """Strategy priority levels for fallback ordering."""
    STEP1V = 1      # Cloud VLM - highest quality
    MINICPM = 2     # Local VLM - medium quality, no network dependency
    OCR = 3         # OCR + Rules - basic functionality
    HUMAN = 4       # Human intervention - last resort


class ActionType(Enum):
    """Types of actions that can be performed on the device."""
    TAP = auto()
    SWIPE = auto()
    LONG_PRESS = auto()
    TYPE = auto()
    BACK = auto()
    HOME = auto()
    RECENT = auto()
    WAIT = auto()
    COMPLETE = auto()
    UNKNOWN = auto()


@dataclass
class Action:
    """Represents a device action."""
    action_type: ActionType
    params: Dict[str, Any] = field(default_factory=dict)
    reasoning: str = ""
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action_type.name,
            "params": self.params,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Action":
        return cls(
            action_type=ActionType[data.get("action", "UNKNOWN")],
            params=data.get("params", {}),
            reasoning=data.get("reasoning", ""),
            confidence=data.get("confidence", 0.0),
        )


@dataclass
class StrategyResult:
    """Result from a strategy execution."""
    success: bool
    level: StrategyLevel
    action: Optional[Action] = None
    raw_response: Optional[str] = None
    processing_time: float = 0.0
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_actionable(self) -> bool:
        """Check if the result contains an actionable instruction."""
        return self.success and self.action is not None


class BaseStrategy(ABC):
    """
    Abstract base class for all vision strategies.

    All strategies must implement:
    - analyze(): Analyze a screenshot and return an action
    - is_available(): Check if the strategy is currently available
    - get_level(): Return the strategy's priority level
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.name = self.__class__.__name__
        self._last_error: Optional[str] = None
        self._consecutive_failures = 0
        self._max_consecutive_failures = self.config.get("max_failures", 3)

    @abstractmethod
    async def analyze(
        self,
        screenshot: Image.Image,
        goal: str,
        context: Optional[Dict[str, Any]] = None
    ) -> StrategyResult:
        """
        Analyze a screenshot and determine the next action.

        Args:
            screenshot: PIL Image of the current screen
            goal: User's goal or instruction
            context: Optional context about previous actions

        Returns:
            StrategyResult containing the recommended action
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this strategy is currently available for use."""
        pass

    @abstractmethod
    def get_level(self) -> StrategyLevel:
        """Return the strategy's priority level."""
        pass

    def record_failure(self, error: str):
        """Record a failure for this strategy."""
        self._consecutive_failures += 1
        self._last_error = error

    def record_success(self):
        """Record a success for this strategy."""
        self._consecutive_failures = 0
        self._last_error = None

    def is_healthy(self) -> bool:
        """Check if the strategy is healthy (not too many consecutive failures)."""
        return self._consecutive_failures < self._max_consecutive_failures

    def get_status(self) -> Dict[str, Any]:
        """Get the current status of this strategy."""
        return {
            "name": self.name,
            "level": self.get_level().name,
            "available": self.is_available(),
            "healthy": self.is_healthy(),
            "consecutive_failures": self._consecutive_failures,
            "last_error": self._last_error,
        }

    def _create_error_result(
        self,
        error: str,
        level: StrategyLevel
    ) -> StrategyResult:
        """Helper to create an error result."""
        self.record_failure(error)
        return StrategyResult(
            success=False,
            level=level,
            error_message=error,
        )