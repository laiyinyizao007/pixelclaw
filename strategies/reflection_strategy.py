"""
Reflection Strategy

Compares before/after screenshots using pixel-level RMS difference to judge
whether an action produced a visible screen change.  Used by VisionAgent to
implement Mobile-Agent-v2-style self-reflection and retry.
"""

from typing import Optional, Tuple

import numpy as np
from PIL import Image

# RMS ratio above this value is treated as an anomalous change (crash/black-screen).
# 0.95 allows legitimate full-range transitions while catching near-uniform sudden dumps.
_ANOMALY_THRESHOLD = 0.95


class ReflectionStrategy:
    """Evaluates action effectiveness via pixel diff."""

    def __init__(self, diff_threshold: float = 0.02):
        # Fraction of max pixel range (255) below which change is "no effect"
        self.diff_threshold = diff_threshold

    def evaluate(
        self,
        before_img: Optional[Image.Image],
        after_img: Optional[Image.Image],
        expected_outcome: str = "",
    ) -> Tuple[bool, str]:
        """
        Compare two screenshots to judge whether an action had visible effect.

        Args:
            before_img: Screenshot captured before the action (None → skip).
            after_img: Screenshot captured after the action (None → skip).
            expected_outcome: Optional human-readable description of what was
                              expected (injected into feedback on failure).

        Returns:
            (is_effective, feedback_message)
        """
        if before_img is None or after_img is None:
            return False, "截图获取失败，无法判断操作效果。"

        before = np.array(before_img.convert("RGB"), dtype=np.float32)
        after = np.array(after_img.convert("RGB"), dtype=np.float32)

        # Normalise to same size before comparing
        if before.shape != after.shape:
            target_size = (before_img.width, before_img.height)
            after_img = after_img.resize(target_size, Image.LANCZOS)
            after = np.array(after_img.convert("RGB"), dtype=np.float32)

        rms = float(np.sqrt(np.mean((after - before) ** 2)))
        ratio = rms / 255.0

        if ratio > _ANOMALY_THRESHOLD:
            return False, (
                f"屏幕异常变化（变化率 {ratio:.3%}），可能发生崩溃或黑屏，请检查设备状态。"
            )

        if ratio < self.diff_threshold:
            msg = (
                f"屏幕几乎未变化（变化率 {ratio:.3%}，阈值 {self.diff_threshold:.3%}）。"
                "操作可能未生效，请尝试其他元素或操作方式。"
            )
            if expected_outcome:
                msg += f" 预期结果：{expected_outcome}"
            return False, msg

        return True, f"屏幕已变化（变化率 {ratio:.3%}），操作生效。"
