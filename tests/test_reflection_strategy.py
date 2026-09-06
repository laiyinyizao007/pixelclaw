"""Tests for strategies/reflection_strategy.py"""
import numpy as np
import pytest
from PIL import Image

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))

from strategies.reflection_strategy import ReflectionStrategy


def solid_image(color: tuple, size=(100, 100)) -> Image.Image:
    arr = np.full((*size[::-1], 3), color, dtype=np.uint8)
    return Image.fromarray(arr, "RGB")


@pytest.fixture
def strategy():
    return ReflectionStrategy(diff_threshold=0.02)


class TestNoneGuards:
    def test_none_before_returns_false(self, strategy):
        after = solid_image((200, 200, 200))
        is_eff, msg = strategy.evaluate(None, after)
        assert is_eff is False
        assert "截图获取失败" in msg

    def test_none_after_returns_false(self, strategy):
        before = solid_image((200, 200, 200))
        is_eff, msg = strategy.evaluate(before, None)
        assert is_eff is False
        assert "截图获取失败" in msg

    def test_both_none_returns_false(self, strategy):
        is_eff, _ = strategy.evaluate(None, None)
        assert is_eff is False


class TestIdenticalImages:
    def test_identical_images_ineffective(self, strategy):
        img = solid_image((128, 128, 128))
        is_eff, _ = strategy.evaluate(img, img.copy())
        assert is_eff is False

    def test_nearly_identical_below_threshold(self, strategy):
        before = solid_image((100, 100, 100))
        # Change only a single pixel — ratio stays well below 2%
        after = before.copy()
        after.putpixel((0, 0), (101, 101, 101))
        is_eff, _ = strategy.evaluate(before, after)
        assert is_eff is False


class TestDifferentImages:
    def test_significantly_different_is_effective(self, strategy):
        # ratio = 128/255 ≈ 0.502; above diff_threshold (0.02) and below anomaly (0.95)
        before = solid_image((0, 0, 0))
        after = solid_image((128, 128, 128))
        is_eff, _ = strategy.evaluate(before, after)
        assert is_eff is True

    def test_partial_change_above_threshold(self, strategy):
        before = solid_image((0, 0, 0), size=(100, 100))
        arr = np.zeros((100, 100, 3), dtype=np.uint8)
        arr[40:60, 40:60] = 200  # 4% of pixels changed significantly
        after = Image.fromarray(arr, "RGB")
        is_eff, _ = strategy.evaluate(before, after)
        assert is_eff is True


class TestAnomalyDetection:
    def test_near_identical_then_full_white_triggers_anomaly(self, strategy):
        # ratio = sqrt(mean((255-0)^2)) / 255 = 1.0, exceeds threshold of 0.95
        before = solid_image((1, 1, 1))    # near black
        after = solid_image((255, 255, 255))  # full white → ratio ≈ 0.999
        is_eff, msg = strategy.evaluate(before, after)
        assert is_eff is False
        assert "异常" in msg

    def test_normal_color_change_not_anomaly(self, strategy):
        # ratio = sqrt(mean((200-100)^2)) / 255 = 100/255 ≈ 0.39; well below threshold
        before = solid_image((100, 100, 100))
        after = solid_image((200, 200, 200))
        is_eff, _ = strategy.evaluate(before, after)
        assert is_eff is True


class TestSizeMismatch:
    def test_different_sizes_handled(self, strategy):
        before = solid_image((100, 100, 100), size=(100, 100))
        after = solid_image((100, 100, 100), size=(200, 200))
        # Same colour so still no change after resize
        is_eff, _ = strategy.evaluate(before, after)
        assert is_eff is False

    def test_different_sizes_with_change(self, strategy):
        before = solid_image((0, 0, 0), size=(100, 100))
        # After a big change but different size
        after = solid_image((128, 128, 128), size=(200, 200))
        is_eff, _ = strategy.evaluate(before, after)
        assert is_eff is True


class TestFeedbackMessage:
    def test_expected_outcome_in_message(self, strategy):
        img = solid_image((128, 128, 128))
        _, msg = strategy.evaluate(img, img.copy(), expected_outcome="弹出确认对话框")
        assert "弹出确认对话框" in msg

    def test_no_expected_outcome_no_crash(self, strategy):
        img = solid_image((128, 128, 128))
        _, msg = strategy.evaluate(img, img.copy())
        assert isinstance(msg, str)
