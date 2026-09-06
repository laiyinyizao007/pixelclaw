"""
SoM (Set-of-Mark) Strategy Wrapper

Resolves VLM-returned som_id values to real device coordinates using the
som_mapping injected into the strategy context by VisionAgent.
This module does NOT import from core/ to avoid circular dependencies.
"""

import copy
import logging
from typing import Any, Dict, Optional

from PIL import Image

from .base import BaseStrategy, StrategyLevel, StrategyResult


logger = logging.getLogger(__name__)


class SOMStrategy(BaseStrategy):
    """
    Decorator-style wrapper around any BaseStrategy.

    VisionAgent annotates the screenshot and stores {som_id: (cx, cy)} in
    context['som_mapping'] before calling analyze().  If the wrapped strategy
    returns an action with {'som_id': N} in its params, this wrapper resolves
    the index to the actual coordinates.
    """

    def __init__(self, base_strategy: BaseStrategy, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.base = base_strategy

    # ------------------------------------------------------------------
    # BaseStrategy interface
    # ------------------------------------------------------------------

    def get_level(self) -> StrategyLevel:
        return self.base.get_level()

    def is_available(self) -> bool:
        return self.base.is_available()

    async def analyze(
        self,
        screenshot: Image.Image,
        goal: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> StrategyResult:
        """Delegate to base strategy, then resolve som_id → (x, y)."""
        result = await self.base.analyze(screenshot, goal, context)

        if not result.success or result.action is None:
            return result

        som_mapping: Dict[int, tuple] = (context or {}).get("som_mapping", {})
        params = result.action.params

        if "som_id" not in params:
            return result

        som_id_raw = params.get("som_id")
        try:
            idx = int(som_id_raw)
            coords = som_mapping.get(idx)
        except (ValueError, TypeError):
            logger.warning(
                "SoM: cannot convert som_id %r to int; keeping action as-is", som_id_raw
            )
            return result

        if coords is None:
            if som_mapping:
                logger.warning(
                    "SoM: som_id %d not found in mapping (mapping size=%d); "
                    "keeping action as-is",
                    idx,
                    len(som_mapping),
                )
            else:
                logger.warning("SoM: som_mapping is empty; keeping action as-is")
            return result

        # Deep-copy params to avoid mutating the shared action reference stored in history
        new_params = copy.deepcopy(params)
        new_params.pop("som_id")
        new_params["x"], new_params["y"] = coords
        result.action.params = new_params

        return result
