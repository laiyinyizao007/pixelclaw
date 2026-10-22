"""聊天页加载判定（区分「触顶」与「慢渲染」）的回归测试（2026-10-22）。

背景：
- 2026-10-21 旧逻辑把两种成因混为一谈：
  A) 触顶 —— 聊天页被 Boss 自动关闭（toast「您今天已与150位BOSS沟通」），前台 Activity
     已不是 ChatRoomActivity。旧逻辑要空转 3 次才判定收工，白丢 2 个候选。
  B) 慢渲染 —— 还停在 ChatRoomActivity，只是 editText 还没渲染出来。旧逻辑直接丢弃
     该候选，白丢一个能发的岗位。
- 新逻辑用**前台 Activity**分辨 A/B：A 立刻判定触顶并截图取证；B 延长等待后重试。

覆盖：
1. `_is_chat_activity` 识别 ChatRoomActivity / 排除职位详情页
2. `_foreground_activity` 解析 dumpsys 输出，取不到时返回空串
3. `_open_chat_page` 四种返回态：OK / SLOW / CAPPED / FAILED
4. 触顶时**第 1 次**就判定（不再等 3 次）
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from scenarios.boss.scripts.smart_match_greet import (  # noqa: E402
    CHAT_CAPPED,
    CHAT_FAILED,
    CHAT_OK,
    CHAT_SLOW,
    _CHAT_EDITTEXT_ID,
    _foreground_activity,
    _is_chat_activity,
    _open_chat_page,
)

CHAT_ACT = "com.hpbr.bosszhipin.module.chat.single.activity.ChatRoomActivity"
DETAIL_ACT = "com.hpbr.bosszhipin.module.job.detail.activity.JobDetailActivity"


class FakeSkill:
    """按剧本回放的 skill 替身。

    wait_responses: 每次 wait_for_element 的返回值（依次消费）
    activities:    每次 _foreground_activity 的查询结果（依次消费）
    """

    def __init__(self, wait_responses=None, activities=None, dump="mCurrentFocus: x"):
        self.wait_responses = list(wait_responses or [])
        self.activities = list(activities or [])
        self.dump = dump
        self.shots = []
        self.wait_calls = []

    def wait_for_element(self, resource_id, timeout=5.0, interval=0.5):
        self.wait_calls.append((resource_id, timeout))
        if self.wait_responses:
            return self.wait_responses.pop(0)
        return None

    def _adb(self, cmd, timeout=30):
        if "dumpsys window" in cmd:
            if self.activities:
                act = self.activities.pop(0)
                # 与真机 dumpsys window 一致的格式：mCurrentFocus=Window{... pkg/cls}
                pkg = act.rsplit(".", 1)[0]
                return True, f"  mCurrentFocus=Window{{42ab u0 {pkg}/{act}}}"
            return True, self.dump
        return True, ""

    def screenshot(self, filename="screenshot.png"):
        self.shots.append(filename)
        return f"/tmp/{filename}"


class TestIsChatActivity:
    def test_chat_room_is_chat(self):
        assert _is_chat_activity(CHAT_ACT) is True

    def test_detail_page_is_not_chat(self):
        assert _is_chat_activity(DETAIL_ACT) is False

    def test_empty_is_not_chat(self):
        assert _is_chat_activity("") is False

    def test_case_insensitive(self):
        assert _is_chat_activity("com.x.chatroomactivity") is True


class TestForegroundActivity:
    def test_parses_current_focus(self):
        skill = FakeSkill(activities=[CHAT_ACT])
        assert _foreground_activity(skill) == CHAT_ACT

    def test_returns_empty_when_adb_fails(self):
        class Broken(FakeSkill):
            def _adb(self, cmd, timeout=30):
                return False, ""

        assert _foreground_activity(Broken()) == ""

    def test_returns_empty_when_unparsable(self):
        skill = FakeSkill(dump="mCurrentFocus=Window{abc}")
        assert _foreground_activity(skill) == ""


class TestOpenChatPage:
    def test_ok_when_edittext_appears_first_round(self):
        skill = FakeSkill(wait_responses=[object()])
        state, detail = _open_chat_page(skill)
        assert state == CHAT_OK
        assert "首轮" in detail
        assert len(skill.wait_calls) == 1, "成功时不应有第二轮等待"

    def test_slow_render_recovers_after_extended_wait(self):
        """慢渲染：还在聊天页 → 延长等待后成功，不能丢候选。"""
        skill = FakeSkill(wait_responses=[None, object()], activities=[CHAT_ACT])
        state, _ = _open_chat_page(skill)
        assert state == CHAT_SLOW
        assert len(skill.wait_calls) == 2
        assert skill.wait_calls[0][1] < skill.wait_calls[1][1], "第二轮等待应更长"

    def test_capped_detected_on_first_failure(self):
        """触顶：聊天页被自动关闭 → 第 1 次就判定，不需要连等 3 次。"""
        skill = FakeSkill(wait_responses=[None], activities=[DETAIL_ACT])
        state, detail = _open_chat_page(skill, screenshot_name="probe.png")
        assert state == CHAT_CAPPED
        assert DETAIL_ACT in detail
        assert skill.shots == ["probe.png"], "触顶时应截图取证"

    def test_capped_detected_after_extended_wait(self):
        """延长等待期间聊天页被关掉 → 同样判定触顶。"""
        skill = FakeSkill(wait_responses=[None, None], activities=[CHAT_ACT, DETAIL_ACT])
        state, detail = _open_chat_page(skill)
        assert state == CHAT_CAPPED
        assert DETAIL_ACT in detail

    def test_failed_when_stuck_in_chat_page(self):
        """一直在聊天页但 editText 始终不出现 → 真·加载失败，可跳过候选。"""
        skill = FakeSkill(wait_responses=[None, None], activities=[CHAT_ACT, CHAT_ACT])
        state, detail = _open_chat_page(skill)
        assert state == CHAT_FAILED
        assert "未出现 editText" in detail

    def test_screenshot_failure_does_not_break_flow(self):
        class ShotFail(FakeSkill):
            def screenshot(self, filename="screenshot.png"):
                raise RuntimeError("screencap 失败")

        skill = ShotFail(wait_responses=[None], activities=[DETAIL_ACT])
        state, detail = _open_chat_page(skill, screenshot_name="probe.png")
        assert state == CHAT_CAPPED
        assert "截图=无" in detail

    def test_uses_expected_resource_id(self):
        skill = FakeSkill(wait_responses=[object()])
        _open_chat_page(skill)
        assert skill.wait_calls[0][0] == _CHAT_EDITTEXT_ID


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
