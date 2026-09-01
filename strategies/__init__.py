"""
PixelClaw Strategy Modules

Provides fallback strategies for vision-based mobile automation:
- Level 1: Step-1V (Cloud VLM)
- Level 2: MiniCPM-V (Local VLM)
- Level 3: OCR + Rule Matching (OpenCV + PaddleOCR)
- Level 4: Human Intervention
"""

from .base import BaseStrategy, StrategyResult, StrategyLevel
from .fallback_manager import FallbackManager

try:
    from .step1v_strategy import Step1VStrategy
except ImportError:
    Step1VStrategy = None  # type: ignore

try:
    from .minicpm_strategy import MiniCPMStrategy
except ImportError:
    MiniCPMStrategy = None  # type: ignore

try:
    from .ocr_strategy import OCRStrategy
except ImportError:
    OCRStrategy = None  # type: ignore

__all__ = [
    "BaseStrategy",
    "StrategyResult",
    "StrategyLevel",
    "FallbackManager",
    "Step1VStrategy",
    "MiniCPMStrategy",
    "OCRStrategy",
]