"""Boss直聘 (BOSS Zhipin) automation skill package."""
from .boss_automation_skill import (
    BOSSAutomationSkill,
    DialogType,
    JobInfo,
    PageState,
    UIElement,
    normalize_card_title,
)

__all__ = [
    "BOSSAutomationSkill",
    "DialogType",
    "JobInfo",
    "PageState",
    "UIElement",
    "normalize_card_title",
]
