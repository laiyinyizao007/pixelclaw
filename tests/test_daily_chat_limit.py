"""
Boss 每日沟通上限（150/天）判定与 sweep 计数修复的回归测试（2026-10-21）。

背景（实测）：
- 2026-10-21 跑到当天第 ~150 次「立即沟通」时，Boss 不弹窗，只弹 **toast**：
  「您今天已与150位BOSS沟通，休息一下，明天再来吧～」，随后聊天页自动关闭退回职位详情页。
- toast 文案拿不到：`uiautomator dump` 不含 toast 窗口；`dumpsys notification`
  只有 toast 队列没有文案；`logcat` 只有 `E/Toast: setGravity()`。只能靠行为特征判定。
- 后果：当天 19 个达标大厂岗位里 11 个卡在「聊天页 editText 4s 超时」被丢弃。

覆盖：
1. `ChatLoadGuard` 的连续失败计数语义（单次失败不终止 / 连续 N 次终止 / 成功清零）
2. `candidate_profile.yaml` 的 `daily_hard_limit` = 150（不是 0）
3. `sweep_big_tech._parse_sent` 在汇总行缺失时按逐条计数兜底（见 test_sweep_big_tech.py）
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from scenarios.boss.scripts.smart_match_greet import (  # noqa: E402
    ChatLoadGuard,
    _CHAT_LOAD_FAIL_MAX,
)


class TestChatLoadGuard:
    """连续聊天页加载失败 → 判定触达每日沟通上限。"""

    def test_default_max_fail(self):
        assert _CHAT_LOAD_FAIL_MAX == 3
        assert ChatLoadGuard().max_fail == 3

    def test_single_failure_does_not_trigger(self):
        """单次失败可能只是网络抖动，不能立刻终止。"""
        guard = ChatLoadGuard()
        assert guard.record_failure() is False
        assert guard.streak == 1

    def test_consecutive_failures_trigger_at_max(self):
        guard = ChatLoadGuard()
        assert guard.record_failure() is False
        assert guard.record_failure() is False
        assert guard.record_failure() is True, "第 3 次连续失败必须触发终止"
        assert guard.streak == 3

    def test_success_resets_streak(self):
        """中间成功一次 → 计数清零，说明不是持续性故障。"""
        guard = ChatLoadGuard()
        guard.record_failure()
        guard.record_failure()
        guard.record_success()
        assert guard.streak == 0
        assert guard.record_failure() is False

    def test_custom_max_fail(self):
        guard = ChatLoadGuard(max_fail=1)
        assert guard.record_failure() is True


class TestProfileDailyLimit:
    """yaml 的每日上限必须反映实测值 150（此前误设为 0）。"""

    def test_daily_hard_limit_is_150(self):
        import yaml

        profile = yaml.safe_load(
            (ROOT / "scenarios/boss/config/candidate_profile.yaml").read_text(encoding="utf-8")
        )
        assert profile["daily_hard_limit"] == 150, (
            "2026-10-21 实测 Boss 每日沟通上限 150，配置必须同步（否则脚本不会主动退出）"
        )
