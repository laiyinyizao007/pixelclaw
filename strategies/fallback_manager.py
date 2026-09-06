"""
Fallback Manager Module

Manages the fallback strategy hierarchy:
Level 1: Step-1V (Cloud VLM)
    ↓ [API failure/timeout/rate limit]
Level 2: MiniCPM-V (Local VLM)
    ↓ [Local model unavailable/memory insufficient]
Level 3: OCR + Rule Matching (OpenCV + PaddleOCR)
    ↓ [Complete failure]
Level 4: Human Intervention
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type

from PIL import Image

from .base import Action, BaseStrategy, StrategyLevel, StrategyResult


logger = logging.getLogger(__name__)


@dataclass
class FallbackStats:
    """Statistics for fallback operations."""
    total_attempts: int = 0
    level1_success: int = 0
    level2_success: int = 0
    level3_success: int = 0
    total_failures: int = 0
    fallback_count: int = 0  # How many times we fell back
    avg_processing_time: float = 0.0
    history: List[Dict[str, Any]] = field(default_factory=list)

    def record_attempt(
        self,
        level: StrategyLevel,
        success: bool,
        processing_time: float,
        fallback_used: bool = False
    ):
        """Record an attempt."""
        self.total_attempts += 1

        if success:
            if level == StrategyLevel.STEP1V:
                self.level1_success += 1
            elif level == StrategyLevel.MINICPM:
                self.level2_success += 1
            elif level == StrategyLevel.OCR:
                self.level3_success += 1
        else:
            self.total_failures += 1

        if fallback_used:
            self.fallback_count += 1

        # Update average processing time
        self.avg_processing_time = (
            (self.avg_processing_time * (self.total_attempts - 1) + processing_time)
            / self.total_attempts
        )

        # Keep last 100 history entries
        self.history.append({
            "timestamp": time.time(),
            "level": level.name,
            "success": success,
            "processing_time": processing_time,
            "fallback_used": fallback_used,
        })
        if len(self.history) > 100:
            self.history = self.history[-100:]

    def get_success_rate(self, level: Optional[StrategyLevel] = None) -> float:
        """Get success rate for a specific level or overall."""
        if self.total_attempts == 0:
            return 0.0

        if level == StrategyLevel.STEP1V:
            attempts = sum(1 for h in self.history if h["level"] == "STEP1V")
            return self.level1_success / max(attempts, 1)
        elif level == StrategyLevel.MINICPM:
            attempts = sum(1 for h in self.history if h["level"] == "MINICPM")
            return self.level2_success / max(attempts, 1)
        elif level == StrategyLevel.OCR:
            attempts = sum(1 for h in self.history if h["level"] == "OCR")
            return self.level3_success / max(attempts, 1)

        # Overall success rate
        successes = self.level1_success + self.level2_success + self.level3_success
        return successes / self.total_attempts


class FallbackManager:
    """
    Manages the fallback strategy chain.

    Attempts strategies in priority order (highest quality first),
    falling back to lower-quality alternatives when higher ones fail.
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        som_annotator=None,
    ):
        self.config = config or {}
        self.strategies: Dict[StrategyLevel, BaseStrategy] = {}
        self.stats = FallbackStats()
        self._strategy_order = [
            StrategyLevel.STEP1V,
            StrategyLevel.MINICPM,
            StrategyLevel.OCR,
        ]

        # Initialize enabled strategies
        self._init_strategies()

        # Wrap Level-1 strategy with SOMStrategy when SoM is enabled
        if som_annotator is not None and StrategyLevel.STEP1V in self.strategies:
            try:
                from .som_strategy import SOMStrategy
                self.strategies[StrategyLevel.STEP1V] = SOMStrategy(
                    self.strategies[StrategyLevel.STEP1V]
                )
                logger.debug("Step-1V strategy wrapped with SOMStrategy")
            except ImportError as e:
                logger.warning("SOMStrategy not available: %s", e)

    def _init_strategies(self):
        """Initialize strategy instances based on configuration."""
        # Step-1V
        step1v_config = self.config.get("step1v", {})
        if step1v_config.get("enabled", True):
            try:
                from .step1v_strategy import Step1VStrategy
                self.strategies[StrategyLevel.STEP1V] = Step1VStrategy(step1v_config)
            except ImportError as e:
                logger.warning("Step-1V strategy not available: %s", e)

        # MiniCPM
        minicpm_config = self.config.get("minicpm", {})
        if minicpm_config.get("enabled", False):
            try:
                from .minicpm_strategy import MiniCPMStrategy
                self.strategies[StrategyLevel.MINICPM] = MiniCPMStrategy(minicpm_config)
            except ImportError as e:
                logger.warning("MiniCPM strategy not available: %s", e)

        # OCR
        ocr_config = self.config.get("ocr", {})
        if ocr_config.get("enabled", True):
            try:
                from .ocr_strategy import OCRStrategy
                self.strategies[StrategyLevel.OCR] = OCRStrategy(ocr_config)
            except ImportError as e:
                logger.warning("OCR strategy not available: %s", e)

    def register_strategy(self, level: StrategyLevel, strategy: BaseStrategy):
        """Register a custom strategy."""
        self.strategies[level] = strategy

    async def analyze(
        self,
        screenshot: Image.Image,
        goal: str,
        context: Optional[Dict[str, Any]] = None
    ) -> StrategyResult:
        """
        Analyze a screenshot using the fallback chain.

        Tries each available strategy in order of quality,
        falling back when one fails.
        """
        last_error = None
        attempted_levels = []

        for level in self._strategy_order:
            strategy = self.strategies.get(level)

            if not strategy:
                continue

            if not strategy.is_available():
                continue

            if not strategy.is_healthy():
                continue

            attempted_levels.append(level.name)
            start_time = time.time()

            try:
                result = await strategy.analyze(screenshot, goal, context)
                processing_time = time.time() - start_time

                if result.success:
                    # Success! Record stats and return
                    fallback_used = len(attempted_levels) > 1
                    self.stats.record_attempt(
                        level, True, processing_time, fallback_used
                    )
                    return result
                else:
                    # Strategy failed, record and continue to next
                    last_error = result.error_message
                    self.stats.record_attempt(level, False, processing_time)

            except Exception as e:
                processing_time = time.time() - start_time
                last_error = str(e)
                self.stats.record_attempt(level, False, processing_time)
                continue

        # All strategies failed - return human intervention needed
        return StrategyResult(
            success=False,
            level=StrategyLevel.HUMAN,
            error_message=(
                f"All strategies failed. Attempted: {', '.join(attempted_levels)}. "
                f"Last error: {last_error}"
            ),
            metadata={
                "attempted_levels": attempted_levels,
                "last_error": last_error,
                "needs_human": True,
            }
        )

    def get_status(self) -> Dict[str, Any]:
        """Get the status of all strategies and the manager."""
        strategy_status = {}
        for level, strategy in self.strategies.items():
            strategy_status[level.name] = strategy.get_status()

        return {
            "strategies": strategy_status,
            "stats": {
                "total_attempts": self.stats.total_attempts,
                "success_rate": self.stats.get_success_rate(),
                "fallback_count": self.stats.fallback_count,
                "level1_success_rate": self.stats.get_success_rate(StrategyLevel.STEP1V),
                "level2_success_rate": self.stats.get_success_rate(StrategyLevel.MINICPM),
                "level3_success_rate": self.stats.get_success_rate(StrategyLevel.OCR),
                "avg_processing_time": self.stats.avg_processing_time,
            }
        }

    def get_recommended_strategy(self) -> Optional[StrategyLevel]:
        """
        Get the recommended strategy based on current conditions.

        Considers availability, health, and recent success rates.
        """
        for level in self._strategy_order:
            strategy = self.strategies.get(level)
            if strategy and strategy.is_available() and strategy.is_healthy():
                return level
        return None

    def reset_stats(self):
        """Reset all statistics."""
        self.stats = FallbackStats()
        for strategy in self.strategies.values():
            strategy._consecutive_failures = 0
            strategy._last_error = None