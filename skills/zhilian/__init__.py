"""智联招聘 (Zhilian Zhaopin) automation skill package."""
from .zhilian_automation_skill import (
    JobInfo,
    PageState,
    ZhilianAutomationSkill,
    normalize_card_title,
)

__all__ = [
    "ZhilianAutomationSkill",
    "JobInfo",
    "PageState",
    "normalize_card_title",
]
