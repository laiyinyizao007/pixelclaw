"""Tests for new page states and methods in BOSSAutomationSkill (second-round exploration)."""
import pytest
from unittest.mock import MagicMock, patch, call

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))

from skills.boss.boss_automation_skill import (
    BOSSAutomationSkill,
    ChatEntry,
    DialogType,
    JobInfo,
    PageState,
    BOSS_PACKAGE,
)

PKG = BOSS_PACKAGE

# ---------------------------------------------------------------------------
# XML fixtures
# ---------------------------------------------------------------------------

def _nav_bar(active_tab: int = 1) -> str:
    """Bottom nav bar XML with 4 tabs. active_tab is 1-based."""
    tabs = [("推荐", 1), ("职位", 2), ("消息", 3), ("我的", 4)]
    nodes = ""
    for label, n in tabs:
        x1 = (n - 1) * 270
        x2 = x1 + 270
        nodes += f"""
      <node bounds="[{x1},2200][{x2},2400]" resource-id="{PKG}:id/cl_tab_{n}" clickable="true">
        <node bounds="[{x1+100},2310][{x2-100},2380]"
              resource-id="{PKG}:id/tv_tab_{n}" text="{label}"/>
      </node>"""
    return nodes


# --- RECOMMEND tab -------------------------------------------------------
RECOMMEND_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[0,120][1080,1800]" resource-id="{PKG}:id/vp_fragment_tabs">
      <node bounds="[0,140][1080,500]" resource-id="{PKG}:id/boss_job_card_view">
        <node text="AI算法工程师"/>
        <node resource-id="{PKG}:id/tv_salary_statue" text="30-50K"/>
        <node resource-id="{PKG}:id/tv_employer" text="百度"/>
        <node resource-id="{PKG}:id/tv_active_status" text="刚刚活跃"/>
        <node resource-id="{PKG}:id/tv_distance" text="距您3公里"/>
      </node>
    </node>
    {_nav_bar(1)}
  </node>
</hierarchy>"""

# --- MESSAGES tab ---------------------------------------------------------
MESSAGES_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[0,60][1080,130]" resource-id="{PKG}:id/et_input" text="搜索"/>
    <node bounds="[0,130][1080,2200]" resource-id="{PKG}:id/contact_vp">
      <node bounds="[0,130][1080,2200]" resource-id="{PKG}:id/recyclerView">
        <node bounds="[0,130][1080,300]">
          <node bounds="[50,140][600,190]" resource-id="{PKG}:id/tv_name" text="李HR"/>
          <node resource-id="{PKG}:id/tv_position" text="Python工程师"/>
          <node resource-id="{PKG}:id/tv_msg" text="你好，请问有意向吗？"/>
          <node resource-id="{PKG}:id/tv_time" text="10:30"/>
        </node>
        <node bounds="[0,310][1080,480]">
          <node bounds="[50,320][600,370]" resource-id="{PKG}:id/tv_name" text="王总监"/>
          <node resource-id="{PKG}:id/tv_position" text="后端工程师"/>
          <node resource-id="{PKG}:id/tv_msg" text="薪资可以再谈"/>
          <node resource-id="{PKG}:id/tv_time" text="昨天"/>
        </node>
      </node>
    </node>
    {_nav_bar(3)}
  </node>
</hierarchy>"""

# single-entry messages page (for navigate_to_chat)
MESSAGES_ONE_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[0,130][1080,2200]" resource-id="{PKG}:id/contact_vp">
      <node bounds="[0,130][1080,2200]" resource-id="{PKG}:id/recyclerView">
        <node bounds="[0,150][1080,310]">
          <node bounds="[50,160][600,210]" resource-id="{PKG}:id/tv_name" text="张招聘"/>
          <node resource-id="{PKG}:id/tv_position" text="数据工程师"/>
          <node resource-id="{PKG}:id/tv_msg" text="期待你的回复"/>
          <node resource-id="{PKG}:id/tv_time" text="09:00"/>
        </node>
      </node>
    </node>
  </node>
</hierarchy>"""

# --- PROFILE tab ----------------------------------------------------------
PROFILE_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[0,0][1080,2200]" resource-id="{PKG}:id/myGeekRoot">
      <node resource-id="{PKG}:id/tv_my_resume" text="我的简历"/>
      <node resource-id="{PKG}:id/cl_post_resume_container" text="投递记录"/>
      <node resource-id="{PKG}:id/cl_interview_container" text="面试邀请"/>
      <node resource-id="{PKG}:id/iv_general_settings" content-desc="设置"/>
    </node>
    {_nav_bar(4)}
  </node>
</hierarchy>"""

# --- RESUME page ----------------------------------------------------------
RESUME_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[0,80][1080,400]" resource-id="{PKG}:id/basic_info">
      <node resource-id="{PKG}:id/sdv_avatar"/>
      <node resource-id="{PKG}:id/tvPosition" text="Python开发工程师"/>
      <node resource-id="{PKG}:id/tvSalary" text="20-30K"/>
      <node resource-id="{PKG}:id/tvCities" text="北京"/>
      <node resource-id="{PKG}:id/job_status" text="离职·随时到岗"/>
    </node>
    <node bounds="[0,420][1080,2200]" resource-id="{PKG}:id/rv_list"/>
  </node>
</hierarchy>"""

# --- FILTER_PANEL ---------------------------------------------------------
FILTER_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[0,400][1080,2200]">
      <node resource-id="{PKG}:id/img_close" bounds="[900,410][1000,510]"/>
      <node resource-id="{PKG}:id/ll_salary">
        <node resource-id="{PKG}:id/tv_option_name" bounds="[40,520][300,580]" text="5K以下"/>
        <node resource-id="{PKG}:id/tv_option_name" bounds="[320,520][580,580]" text="5-10K"/>
        <node resource-id="{PKG}:id/tv_option_name" bounds="[600,520][860,580]" text="10-20K"/>
        <node resource-id="{PKG}:id/tv_option_name" bounds="[40,600][300,660]" text="20-50K"/>
      </node>
      <node resource-id="{PKG}:id/ll_options">
        <node resource-id="{PKG}:id/tv_option_name" bounds="[40,700][300,760]" text="不限经验"/>
        <node resource-id="{PKG}:id/tv_option_name" bounds="[320,700][580,760]" text="1-3年"/>
        <node resource-id="{PKG}:id/tv_option_name" bounds="[600,700][860,760]" text="3-5年"/>
      </node>
    </node>
    <node bounds="[0,2100][1080,2250]">
      <node resource-id="{PKG}:id/btn_reset" bounds="[50,2110][480,2200]" text="重置"/>
      <node resource-id="{PKG}:id/btn_confirm" bounds="[530,2110][1030,2200]" text="确定"/>
    </node>
  </node>
</hierarchy>"""

# filter panel after salary "10-20K" is selected
FILTER_SELECTED_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[0,400][1080,2200]">
      <node resource-id="{PKG}:id/ll_salary">
        <node resource-id="{PKG}:id/tv_option_name" bounds="[40,520][300,580]" text="5K以下"/>
        <node resource-id="{PKG}:id/tv_option_name" bounds="[600,520][860,580]"
              text="10-20K" selected="true"/>
      </node>
      <node resource-id="{PKG}:id/ll_options">
        <node resource-id="{PKG}:id/tv_option_name" bounds="[320,700][580,760]" text="1-3年"/>
      </node>
    </node>
    <node bounds="[0,2100][1080,2250]">
      <node resource-id="{PKG}:id/btn_reset" bounds="[50,2110][480,2200]" text="重置"/>
      <node resource-id="{PKG}:id/btn_confirm" bounds="[530,2110][1030,2200]" text="确定"/>
    </node>
  </node>
</hierarchy>"""

# --- Nav-bar helpers (no content page) ------------------------------------
NAV_ONLY_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    {_nav_bar(1)}
  </node>
</hierarchy>"""


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def skill():
    adb = MagicMock()
    adb.tap.return_value = True
    adb.type_text.return_value = True
    adb.press_key.return_value = True
    s = BOSSAutomationSkill(adb_manager=adb)
    # get_ui_hierarchy calls self.adb.shell twice; mock it to return empty by default
    adb.shell.return_value = (True, "")
    return s


def _mock_xml(skill, xml: str):
    """Make skill.get_ui_hierarchy() return the given XML string."""
    skill.adb.shell.return_value = (True, xml)


def _tap_calls(skill):
    """The `shell input tap X Y` commands issued through adb.shell."""
    return [
        c.args[0]
        for c in skill.adb.shell.call_args_list
        if c.args and c.args[0].startswith("shell input tap ")
    ]


# ===========================================================================
# 1. PageState detection — new states
# ===========================================================================

class TestNewPageStateDetection:
    def test_recommend_tab(self, skill):
        assert skill.get_current_page(xml=RECOMMEND_XML) == PageState.RECOMMEND

    def test_messages_tab(self, skill):
        assert skill.get_current_page(xml=MESSAGES_XML) == PageState.MESSAGES

    def test_profile_tab(self, skill):
        assert skill.get_current_page(xml=PROFILE_XML) == PageState.PROFILE

    def test_resume_page(self, skill):
        assert skill.get_current_page(xml=RESUME_XML) == PageState.RESUME

    def test_filter_panel(self, skill):
        assert skill.get_current_page(xml=FILTER_XML) == PageState.FILTER_PANEL

    def test_filter_panel_priority_over_others(self, skill):
        """Filter panel should be detected even if other elements are present."""
        combined = FILTER_XML.replace(
            "</hierarchy>",
            f'<node resource-id="{PKG}:id/et_search"/></hierarchy>',
        )
        assert skill.get_current_page(xml=combined) == PageState.FILTER_PANEL

    def test_recommend_not_confused_with_job_list(self, skill):
        """RECOMMEND must be detected before JOB_LIST."""
        # RECOMMEND_XML has boss_job_card_view but no search filter bar
        result = skill.get_current_page(xml=RECOMMEND_XML)
        assert result == PageState.RECOMMEND
        assert result != PageState.JOB_LIST

    def test_resume_requires_both_elements(self, skill):
        """Without rv_list, resume page should not be RESUME."""
        no_rv = RESUME_XML.replace(
            f'resource-id="{PKG}:id/rv_list"', 'resource-id="other:id/rv"'
        )
        result = skill.get_current_page(xml=no_rv)
        assert result != PageState.RESUME

    def test_profile_without_mygeekroot(self, skill):
        no_root = PROFILE_XML.replace(
            f'resource-id="{PKG}:id/myGeekRoot"', 'resource-id="other:id/root"'
        )
        result = skill.get_current_page(xml=no_root)
        assert result != PageState.PROFILE

    def test_unknown_on_empty(self, skill):
        empty = '<?xml version="1.0"?><hierarchy rotation="0"/>'
        assert skill.get_current_page(xml=empty) == PageState.UNKNOWN


# ===========================================================================
# 2. navigate_to_tab
# ===========================================================================

class TestNavigateToTab:
    def test_navigate_to_messages_tab(self, skill):
        _mock_xml(skill, MESSAGES_XML)
        result = skill.navigate_to_tab("messages")
        assert result is True
        # cl_tab_3 center: bounds=[540,2200][810,2400] → x=675, y=2300
        assert _tap_calls(skill) == ["shell input tap 675 2300"]

    def test_navigate_to_profile_tab(self, skill):
        _mock_xml(skill, PROFILE_XML)
        result = skill.navigate_to_tab("profile")
        assert result is True
        assert _tap_calls(skill) == ["shell input tap 945 2300"]

    def test_navigate_to_recommend_tab(self, skill):
        _mock_xml(skill, RECOMMEND_XML)
        result = skill.navigate_to_tab("recommend")
        assert result is True
        assert _tap_calls(skill) == ["shell input tap 135 2300"]

    def test_navigate_to_jobs_tab(self, skill):
        _mock_xml(skill, NAV_ONLY_XML)
        result = skill.navigate_to_tab("jobs")
        assert result is True
        assert _tap_calls(skill) == ["shell input tap 405 2300"]

    def test_unknown_tab_name_returns_false(self, skill):
        _mock_xml(skill, NAV_ONLY_XML)
        result = skill.navigate_to_tab("nonexistent_tab")
        assert result is False
        assert _tap_calls(skill) == []

    def test_returns_false_on_empty_xml(self, skill):
        _mock_xml(skill, "")
        assert skill.navigate_to_tab("messages") is False

    def test_returns_false_on_invalid_xml(self, skill):
        _mock_xml(skill, "<broken>")
        assert skill.navigate_to_tab("messages") is False


# ===========================================================================
# 3. get_message_list
# ===========================================================================

class TestGetMessageList:
    def test_parses_two_entries(self, skill):
        entries = skill.get_message_list(xml=MESSAGES_XML)
        assert len(entries) == 2

    def test_first_entry_fields(self, skill):
        entries = skill.get_message_list(xml=MESSAGES_XML)
        e = entries[0]
        assert e.hr_name == "李HR"
        assert e.position == "Python工程师"
        assert e.last_msg == "你好，请问有意向吗？"
        assert e.time_str == "10:30"

    def test_second_entry_fields(self, skill):
        entries = skill.get_message_list(xml=MESSAGES_XML)
        e = entries[1]
        assert e.hr_name == "王总监"
        assert e.position == "后端工程师"
        assert e.last_msg == "薪资可以再谈"
        assert e.time_str == "昨天"

    def test_tap_coordinates_nonzero(self, skill):
        entries = skill.get_message_list(xml=MESSAGES_XML)
        # tap coords come from the parent node bounds; at least one must be nonzero
        for e in entries:
            assert (e.tap_x != 0) or (e.tap_y != 0)

    def test_tap_y_differs_between_entries(self, skill):
        entries = skill.get_message_list(xml=MESSAGES_XML)
        # two chat rows at different y-positions
        assert entries[0].tap_y != entries[1].tap_y or entries[0].tap_x != entries[1].tap_x

    def test_empty_on_no_tv_name(self, skill):
        xml = f"""<?xml version="1.0"?>
<hierarchy rotation="0">
  <node resource-id="{PKG}:id/contact_vp">
    <node resource-id="{PKG}:id/recyclerView"/>
  </node>
</hierarchy>"""
        entries = skill.get_message_list(xml=xml)
        assert entries == []

    def test_returns_list_type(self, skill):
        entries = skill.get_message_list(xml=MESSAGES_XML)
        assert isinstance(entries, list)
        assert all(isinstance(e, ChatEntry) for e in entries)

    def test_uses_live_xml_when_not_provided(self, skill):
        _mock_xml(skill, MESSAGES_XML)
        entries = skill.get_message_list()
        assert len(entries) == 2
        assert skill.adb.shell.called

    def test_empty_on_invalid_xml(self, skill):
        entries = skill.get_message_list(xml="<bad")
        assert entries == []


# ===========================================================================
# 4. navigate_to_chat
# ===========================================================================

class TestNavigateToChat:
    def test_taps_correct_coordinates(self, skill):
        entry = ChatEntry(hr_name="张招聘", position="数据工程师",
                          last_msg="期待你的回复", time_str="09:00",
                          tap_x=540, tap_y=230)
        result = skill.navigate_to_chat(entry)
        assert result is True
        assert _tap_calls(skill) == ["shell input tap 540 230"]

    def test_returns_false_when_coordinates_zero(self, skill):
        entry = ChatEntry(tap_x=0, tap_y=0)
        result = skill.navigate_to_chat(entry)
        assert result is False
        assert _tap_calls(skill) == []

    def test_returns_false_when_tap_fails(self, skill):
        skill.adb.shell.return_value = (False, "")
        entry = ChatEntry(tap_x=540, tap_y=300)
        result = skill.navigate_to_chat(entry)
        assert result is False

    def test_navigate_to_chat_from_parsed_list(self, skill):
        entries = skill.get_message_list(xml=MESSAGES_ONE_XML)
        assert len(entries) == 1
        e = entries[0]
        result = skill.navigate_to_chat(e)
        assert result is True
        assert _tap_calls(skill) == [f"shell input tap {e.tap_x} {e.tap_y}"]

    def test_navigate_to_chat_then_can_apply(self, skill):
        """apply 按钮仅在 HR 回复后的聊天页出现，can_apply() 检测它"""
        entry = ChatEntry(hr_name="王HR", position="测试工程师",
                          last_msg="您好", time_str="10:00", tap_x=325, tap_y=165)
        CHAT_WITH_APPLY = f"""<hierarchy>
  <node resource-id="{PKG}:id/editText_with_scrollbar" text=""/>
  <node resource-id="{PKG}:id/btn_apply" text="投递简历"/>
</hierarchy>"""
        _mock_xml(skill, MESSAGES_ONE_XML)
        skill.navigate_to_chat(entry)
        _mock_xml(skill, CHAT_WITH_APPLY)
        assert skill.can_apply() is True


# ===========================================================================
# 5. set_filter
# ===========================================================================

class TestSetFilter:
    def _prep(self, skill, filter_xml=None):
        _mock_xml(skill, filter_xml if filter_xml is not None else FILTER_XML)
        return skill

    def test_set_salary_taps_option_and_confirms(self, skill):
        s = self._prep(skill)
        result = s.set_filter(salary="10-20K")
        assert result is True
        # Should tap btn_confirm after selecting
        assert len(_tap_calls(skill)) >= 2  # option tap + confirm tap

    def test_set_experience_taps_option_and_confirms(self, skill):
        s = self._prep(skill)
        result = s.set_filter(experience="1-3年")
        assert result is True

    def test_set_multiple_filters(self, skill):
        s = self._prep(skill)
        result = s.set_filter(salary="10-20K", experience="1-3年")
        assert result is True

    def test_nonexistent_option_still_confirms(self, skill):
        """If an option text isn't found, set_filter should still try to confirm."""
        s = self._prep(skill)
        # "100K+" doesn't exist in FILTER_XML
        result = s.set_filter(salary="100K+")
        # confirm button exists, so True is expected
        assert result is True

    def test_returns_false_when_no_confirm_btn_and_no_option_found(self, skill):
        """No confirm btn and no matching option → returns False (nothing changed)."""
        xml_no_confirm = FILTER_XML.replace(
            f'resource-id="{PKG}:id/btn_confirm"',
            'resource-id="other:id/btn"',
        ).replace('text="确定"', 'text="GONE"')
        _mock_xml(skill, xml_no_confirm)
        result = skill.set_filter(salary="不存在的薪资选项XYZ")
        assert result is False

    def test_empty_filter_call_confirms(self, skill):
        """set_filter() with no args should still confirm the panel."""
        s = self._prep(skill)
        result = s.set_filter()
        assert result is True

    def test_returns_false_on_empty_xml(self, skill):
        _mock_xml(skill, "")
        result = skill.set_filter(salary="10-20K")
        assert result is False


# ===========================================================================
# 6. PageState constants coverage
# ===========================================================================

class TestPageStateConstants:
    def test_all_new_constants_are_strings(self):
        new_states = [
            PageState.RECOMMEND,
            PageState.MESSAGES,
            PageState.PROFILE,
            PageState.FILTER_PANEL,
            PageState.COMPANY_DETAIL,
            PageState.RESUME,
            PageState.APPLICATIONS,
        ]
        for state in new_states:
            assert isinstance(state, str)

    def test_new_constants_distinct_from_each_other(self):
        new_states = [
            PageState.RECOMMEND,
            PageState.MESSAGES,
            PageState.PROFILE,
            PageState.FILTER_PANEL,
            PageState.COMPANY_DETAIL,
            PageState.RESUME,
            PageState.APPLICATIONS,
        ]
        assert len(set(new_states)) == len(new_states)

    def test_new_constants_distinct_from_old(self):
        old = {PageState.HOME, PageState.JOB_LIST, PageState.JOB_DETAIL,
               PageState.CHAT, PageState.DIALOG, PageState.UNKNOWN}
        new = {PageState.RECOMMEND, PageState.MESSAGES, PageState.PROFILE,
               PageState.FILTER_PANEL, PageState.COMPANY_DETAIL,
               PageState.RESUME, PageState.APPLICATIONS}
        assert old.isdisjoint(new)


# ===========================================================================
# 7. ChatEntry dataclass
# ===========================================================================

class TestChatEntry:
    def test_default_values(self):
        e = ChatEntry()
        assert e.hr_name == ""
        assert e.position == ""
        assert e.last_msg == ""
        assert e.time_str == ""
        assert e.tap_x == 0
        assert e.tap_y == 0

    def test_constructor_kwargs(self):
        e = ChatEntry(hr_name="张三", position="工程师",
                      last_msg="你好", time_str="10:00",
                      tap_x=100, tap_y=200)
        assert e.hr_name == "张三"
        assert e.tap_y == 200

    def test_is_dataclass(self):
        import dataclasses
        assert dataclasses.is_dataclass(ChatEntry)


# ===========================================================================
# 8. Tab texts mapping (_TAB_TEXTS)
# ===========================================================================

class TestTabTexts:
    def test_all_tabs_have_text_mappings(self, skill):
        for tab in ("recommend", "jobs", "messages", "profile"):
            assert tab in skill._TAB_TEXTS
            assert isinstance(skill._TAB_TEXTS[tab], list)
            assert len(skill._TAB_TEXTS[tab]) > 0

    def test_recommend_maps_to_chinese(self, skill):
        assert "推荐" in skill._TAB_TEXTS["recommend"]

    def test_messages_maps_to_chinese(self, skill):
        assert "消息" in skill._TAB_TEXTS["messages"]

    def test_profile_maps_to_chinese(self, skill):
        assert "我的" in skill._TAB_TEXTS["profile"]


# ===========================================================================
# 9. ensure_ready() — error recovery health check
# ===========================================================================

class TestEnsureReady:
    def test_ensure_ready_adb_disconnected(self, skill):
        skill.adb.is_device_connected.return_value = False
        assert skill.ensure_ready() is False

    def test_ensure_ready_unknown_page_relaunch_ok(self, skill):
        skill.adb.is_device_connected.return_value = True
        with patch.object(skill, "_is_screen_on", return_value=True):
            with patch.object(skill, "get_current_page", side_effect=[PageState.UNKNOWN, PageState.HOME]):
                with patch.object(skill, "launch", return_value=True):
                    assert skill.ensure_ready() is True

    def test_ensure_ready_unknown_page_relaunch_fails(self, skill):
        skill.adb.is_device_connected.return_value = True
        with patch.object(skill, "_is_screen_on", return_value=True):
            with patch.object(skill, "get_current_page", return_value=PageState.UNKNOWN):
                with patch.object(skill, "launch", return_value=True):
                    assert skill.ensure_ready() is False

    def test_ensure_ready_fatal_dialog(self, skill):
        skill.adb.is_device_connected.return_value = True
        with patch.object(skill, "_is_screen_on", return_value=True):
            with patch.object(skill, "get_current_page", return_value=PageState.DIALOG):
                with patch.object(skill, "detect_dialog", return_value=DialogType.DAILY_LIMIT):
                    with patch.object(skill, "dismiss_dialog") as mock_dismiss:
                        assert skill.ensure_ready() is False
                        mock_dismiss.assert_not_called()

    def test_ensure_ready_dismissible_dialog(self, skill):
        skill.adb.is_device_connected.return_value = True
        with patch.object(skill, "_is_screen_on", return_value=True):
            with patch.object(skill, "get_current_page", return_value=PageState.DIALOG):
                with patch.object(skill, "detect_dialog", return_value=DialogType.EXISTING_CHAT):
                    with patch.object(skill, "dismiss_dialog", return_value=True) as mock_dismiss:
                        assert skill.ensure_ready() is True
                        mock_dismiss.assert_called_once_with(DialogType.EXISTING_CHAT)
