"""
发送状态三态与「重复打招呼」防线（2026-10-21 code review 发现）。

问题
----
`send_greeting()` 返回 bool。发送按钮的 tap 一旦执行，消息就**可能**已经发出；
若此后 ADB 掉线导致「发送后验证失败」，调用方只看到 ``False``，无法区分：

- ``False`` = 压根没发出去（没找到输入框/发送按钮）
- ``False`` = **发了，但没验证上**

若一律当成「没发」，自愈链重跑整轮（或下一轮 sweep 再搜到同一岗位）会
给同一个 HR 再发一条一模一样的话——掉线最易发生的时刻恰恰就是发送之后。

防线
----
1. `BOSSAutomationSkill.send_greeting()` 增设 `last_send_state` 三态：
   ``sent`` / ``not_sent`` / ``unknown``。
2. `smart_match_greet.py` 在发送失败分支看这个状态：``unknown`` → 按
   **已发送** 记账（写 greetings + job_visits.greeted=1），让去重链认账。

覆盖
----
- `send_greeting` 三态的赋值（tap 失败 / verify 失败 / 成功 / 未找到按钮）
- `unknown` 状态会被记账为已发送（`_is_already_greeted` 认账）
- `not_sent` 状态不记账（不会凭空多出一条 greetings）
"""
import logging
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from scenarios.boss.scripts import smart_match_greet as smg  # noqa: E402


class TestSendGreetingState:
    """`send_greeting` 的三态标记。"""

    def _skill(self, connected: bool = True, tap_ok: bool = True, verified: bool = True,
               output_dir: str = "/tmp"):
        from skills.boss import boss_automation_skill as mod

        class _FakeADB:
            def is_device_connected(self, device_id=None):
                return connected

            def shell(self, cmd, device_id=None):
                return True, ""

        class _Skill(mod.BOSSAutomationSkill):
            def __init__(self):
                self.adb = _FakeADB()
                self.device_id = "10.32.7.135:5555"
                self.output_dir = output_dir
                self._ADB_IME = "com.android.adbkeyboard/.AdbIME"

            def tap_element(self, name):
                return True

            def _adb(self, cmd, device_id=None):
                return True, ""

            def tap(self, x, y):
                return tap_ok

            def _save_debug_screenshot(self, tag):
                return None

            def verify_message_sent(self, message):
                return verified

        # 跳过 UI 查找：输入框始终「有字」，发送按钮通过位置兜底找到
        skill = _Skill()
        skill._logger = logging.getLogger("test_send_state")

        def _find_element(*, resource_id=None, text=None, xml=None, **kw):
            if resource_id == skill.ELEMENTS["chat_input"]:
                return type("E", (), {"text": "hi 招呼文本", "center": (10, 20)})()
            return None

        skill.find_element = _find_element
        skill._find_send_btn_by_position = lambda xml, chat: type(
            "E", (), {"center": (100, 200), "bounds": "[0,0][200,300]"}
        )()
        skill.get_ui_hierarchy = lambda *a, **k: "<hierarchy/>"
        return skill

    def test_verify_failure_marks_unknown(self):
        """tap 已执行 + 验证失败 → unknown（可能已发出）。"""
        skill = self._skill(verified=False)
        assert skill.send_greeting("hi", verify=True) is False
        assert skill.last_send_state == "unknown"

    def test_verify_success_marks_sent(self):
        skill = self._skill(verified=True)
        assert skill.send_greeting("hi", verify=True) is True
        assert skill.last_send_state == "sent"

    def test_no_verify_marks_sent(self):
        skill = self._skill(verified=False)
        assert skill.send_greeting("hi", verify=False) is True
        assert skill.last_send_state == "sent"

    def test_tap_failure_offline_marks_unknown(self):
        """tap 失败且设备已不可达 → unknown（tap 可能实际送达了）。"""
        skill = self._skill(connected=False, tap_ok=False)
        assert skill.send_greeting("hi", verify=True) is False
        assert skill.last_send_state == "unknown"

    def test_tap_failure_online_marks_not_sent(self):
        """tap 失败且设备在线 → not_sent（确定没发）。"""
        skill = self._skill(connected=True, tap_ok=False)
        assert skill.send_greeting("hi", verify=True) is False
        assert skill.last_send_state == "not_sent"

    def test_no_send_button_marks_not_sent(self):
        """找不到发送按钮 → not_sent（确定没发）。"""
        skill = self._skill()
        skill._find_send_btn_by_position = lambda xml, chat: None
        assert skill.send_greeting("hi", verify=True) is False
        assert skill.last_send_state == "not_sent"


class TestUnknownSendIsRecorded:
    """`unknown` 状态必须被记账为已发送，去重链才认账。"""

    @pytest.fixture
    def conn(self, tmp_path):
        c = sqlite3.connect(tmp_path / "t.db")
        c.executescript(
            """
            CREATE TABLE job_details (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedup_key TEXT UNIQUE, description TEXT, hr_name TEXT,
                hr_title TEXT, hr_active TEXT);
            CREATE TABLE greetings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_details_id INTEGER NOT NULL, keyword TEXT, greeting_text TEXT,
                sent_at TEXT NOT NULL, action TEXT);
            CREATE TABLE job_visits (
                id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                company TEXT NOT NULL, hr_name TEXT, keyword TEXT NOT NULL,
                score INTEGER, greeted INTEGER NOT NULL DEFAULT 0,
                greeting_text TEXT, skip_reason TEXT, visited_at TEXT NOT NULL,
                salary TEXT, experience TEXT, company_info TEXT, description TEXT,
                top_matches TEXT, mismatch_concerns TEXT);
            """
        )
        yield c
        c.close()

    def test_unknown_state_records_greeting(self, conn, monkeypatch):
        """unknown → 写 greetings + job_visits.greeted=1 → _is_already_greeted 认账。"""
        recorded = {}

        monkeypatch.setattr(
            smg, "_record_greeting",
            lambda c, *a, **kw: recorded.setdefault("greeting", a),
        )
        monkeypatch.setattr(
            smg, "_record_visit",
            lambda c, *a, **kw: recorded.setdefault("visit", kw),
        )

        class _Skill:
            last_send_state = "unknown"

        smg._record_send_outcome(
            _Skill(), conn, "AI产品经理", "字节跳动", "陈先生", "字节跳动",
            "招呼文本", 9, job_dict=None, scored_result=None,
            result={"errors": []},
        )
        assert "greeting" in recorded, "unknown 状态必须写 greetings（否则去重链不认账）"
        assert recorded["visit"].get("greeted") is True

    def test_not_sent_state_does_not_record_greeting(self, conn, monkeypatch):
        """not_sent → 不写 greetings，只记 skip。"""
        recorded = {}
        monkeypatch.setattr(
            smg, "_record_greeting",
            lambda c, *a, **kw: recorded.setdefault("greeting", a),
        )
        monkeypatch.setattr(
            smg, "_record_visit",
            lambda c, *a, **kw: recorded.setdefault("visit", kw),
        )

        class _Skill:
            last_send_state = "not_sent"

        smg._record_send_outcome(
            _Skill(), conn, "AI产品经理", "字节跳动", "陈先生", "字节跳动",
            "招呼文本", 9, job_dict=None, scored_result=None,
            result={"errors": []},
        )
        assert "greeting" not in recorded
        assert recorded["visit"].get("greeted") is not True
