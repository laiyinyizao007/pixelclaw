"""Enhanced tests for skills/boss/boss_automation_skill.py (new features)."""
import itertools
import time
import pytest
from unittest.mock import MagicMock, call, patch

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))

from skills.boss.boss_automation_skill import (
    BOSSAutomationSkill,
    DialogType,
    JobInfo,
    PageState,
    BOSS_PACKAGE,
    normalize_card_title,
)
from skills.android.ui_types import UIElement


# ---------------------------------------------------------------------------
# XML fixtures
# ---------------------------------------------------------------------------

PKG = BOSS_PACKAGE

# Job list with a single rich card (company/salary/location/hr fields)
RICH_CARD_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]" resource-id="{PKG}:id/recycler_view">
    <node bounds="[0,100][1080,400]" resource-id="{PKG}:id/job_card_view">
      <node bounds="[50,120][700,180]" resource-id="{PKG}:id/tv_position_name" text="Python工程师"/>
      <node bounds="[50,190][600,230]" resource-id="{PKG}:id/tv_company_name" text="字节跳动"/>
      <node bounds="[700,120][1000,160]" resource-id="{PKG}:id/tv_salary_desc" text="20-30K"/>
      <node bounds="[50,240][500,280]" resource-id="{PKG}:id/tv_job_area" text="北京·朝阳"/>
      <node bounds="[700,350][900,390]" resource-id="{PKG}:id/tv_boss_name" text="张三"/>
      <node bounds="[700,390][950,420]" resource-id="{PKG}:id/tv_boss_status" text="刚刚活跃"/>
    </node>
  </node>
</hierarchy>"""

# Two job cards with different coordinates
TWO_JOBS_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[0,100][1080,400]" resource-id="{PKG}:id/job_card_view">
      <node bounds="[50,150][700,210]" resource-id="{PKG}:id/tv_position_name" text="Python工程师"/>
    </node>
    <node bounds="[0,420][1080,720]" resource-id="{PKG}:id/job_card_view">
      <node bounds="[50,470][700,530]" resource-id="{PKG}:id/tv_position_name" text="后端工程师"/>
    </node>
  </node>
</hierarchy>"""

# Home page (has search bar)
HOME_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[50,50][900,120]" resource-id="{PKG}:id/et_search" text="搜索职位/公司"/>
  </node>
</hierarchy>"""

# Job list page (has tv_position_name + filter bar)
JOB_LIST_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[0,80][1080,140]" resource-id="{PKG}:id/filterBarRightTabView" text="筛选"/>
    <node bounds="[50,200][700,260]" resource-id="{PKG}:id/tv_position_name" text="Python工程师"/>
  </node>
</hierarchy>"""

# Job detail page (has tv_job_name + btn_chat)
JOB_DETAIL_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[50,100][900,160]" resource-id="{PKG}:id/tv_job_name" text="Python工程师"/>
    <node bounds="[200,1800][880,1900]" resource-id="{PKG}:id/btn_chat" text="立即沟通"/>
  </node>
</hierarchy>"""

# Chat page (has chat input)
CHAT_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[50,2200][900,2350]" resource-id="{PKG}:id/editText_with_scrollbar" text=""/>
  </node>
</hierarchy>"""

# Daily limit dialog
DAILY_LIMIT_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[100,800][980,1000]" resource-id="android:id/message"
          text="您今日的免费沟通名额已使用完，明日再来吧"/>
    <node bounds="[300,1050][780,1130]" resource-id="" text="我知道了"/>
  </node>
</hierarchy>"""

# Login dialog
LOGIN_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[100,800][980,900]" text="请登录后操作"/>
    <node bounds="[300,950][780,1030]" text="立即登录"/>
    <node bounds="[300,1050][780,1130]" text="取消"/>
  </node>
</hierarchy>"""

# Job offline dialog
JOB_OFFLINE_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[100,800][980,900]" text="该职位已下线，暂时无法投递"/>
    <node bounds="[300,950][780,1030]" text="确定"/>
  </node>
</hierarchy>"""

# Existing chat dialog
EXISTING_CHAT_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[100,800][980,900]" text="已和对方建立沟通，继续沟通"/>
    <node bounds="[300,950][780,1030]" text="确定"/>
  </node>
</hierarchy>"""

# Chat page with sent message bubble
CHAT_WITH_MSG_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[50,200][900,260]" resource-id="{PKG}:id/tv_chat_content"
          text="您好！我对这个职位很感兴趣，期待与您进一步沟通。"/>
    <node bounds="[50,2200][900,2350]" resource-id="{PKG}:id/editText_with_scrollbar" text=""/>
  </node>
</hierarchy>"""

# Empty/unknown page
EMPTY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]"/>
</hierarchy>"""

# Second page of job listings (different jobs, used for scroll tests)
SCROLLED_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[0,100][1080,400]" resource-id="{PKG}:id/job_card_view">
      <node bounds="[50,150][700,210]" resource-id="{PKG}:id/tv_position_name" text="数据工程师"/>
    </node>
    <node bounds="[0,420][1080,720]" resource-id="{PKG}:id/job_card_view">
      <node bounds="[50,470][700,530]" resource-id="{PKG}:id/tv_position_name" text="算法工程师"/>
    </node>
  </node>
</hierarchy>"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_adb():
    adb = MagicMock()
    adb.shell.return_value = (True, "")
    adb.tap.return_value = True
    adb.swipe.return_value = True
    adb.type_text.return_value = True
    return adb


@pytest.fixture
def skill(mock_adb):
    return BOSSAutomationSkill(mock_adb, device_id="test_device", action_delay=0.0)


def _xml_shell(xml):
    """Return an adb.shell side_effect that yields the XML on the second call."""
    return [(True, ""), (True, xml)]


def _make_shell_seq(*xmls):
    """Return an adb.shell side_effect list: dump→"", cat→xml, repeated."""
    calls = []
    for xml in xmls:
        calls.extend([(True, ""), (True, xml)])
    return calls


def _paged_shell(*xmls):
    """
    Return an adb.shell side_effect callable that answers `shell cat` with the
    next XML page (repeating the last one forever) and everything else with "".
    Immune to call-budget drift from taps/swipes.
    """
    pages = iter(xmls)
    state = {"cur": next(pages)}

    def _side_effect(cmd, *args, **kwargs):
        if cmd.startswith("shell cat"):
            xml = state["cur"]
            state["cur"] = next(pages, state["cur"])
            return (True, xml)
        return (True, "")

    return _side_effect


def _cycling_shell(initial_calls, cycle_xml):
    """Return a side_effect function: serves initial_calls then cycles over cycle_xml."""
    it = iter(initial_calls)
    cyc = itertools.cycle([(True, ""), (True, cycle_xml)])

    def _side_effect(*args, **kwargs):
        try:
            return next(it)
        except StopIteration:
            return next(cyc)

    return _side_effect


# ---------------------------------------------------------------------------
# TestJobInfoCoordinates
# ---------------------------------------------------------------------------

class TestJobInfoCoordinates:
    def test_get_job_list_records_tap_coordinates(self, skill, mock_adb):
        mock_adb.shell.side_effect = _xml_shell(RICH_CARD_XML)
        jobs = skill.get_job_list()
        assert len(jobs) == 1
        # bounds [50,120][700,180] → center (375, 150)
        assert jobs[0].tap_x == 375
        assert jobs[0].tap_y == 150

    def test_second_job_has_different_coordinates(self, skill, mock_adb):
        mock_adb.shell.side_effect = _xml_shell(TWO_JOBS_XML)
        jobs = skill.get_job_list()
        assert len(jobs) == 2
        # First job bounds [50,150][700,210] → (375, 180)
        # Second job bounds [50,470][700,530] → (375, 500)
        assert jobs[0].tap_y != jobs[1].tap_y

    def test_navigate_to_job_uses_stored_coordinates(self, skill, mock_adb):
        mock_adb.shell.side_effect = _xml_shell(TWO_JOBS_XML) + [(True, "")]
        jobs = skill.get_job_list()
        skill.navigate_to_job(jobs[1])
        assert mock_adb.shell.call_args_list[-1].args[0] == "shell input tap 375 500"


# ---------------------------------------------------------------------------
# TestJobInfoRichFields
# ---------------------------------------------------------------------------

class TestJobInfoRichFields:
    def _get_first_job(self, skill, mock_adb, xml):
        mock_adb.shell.side_effect = _xml_shell(xml)
        jobs = skill.get_job_list()
        assert jobs, "Expected at least one job"
        return jobs[0]

    def test_extracts_company(self, skill, mock_adb):
        job = self._get_first_job(skill, mock_adb, RICH_CARD_XML)
        assert job.company == "字节跳动"

    def test_extracts_salary(self, skill, mock_adb):
        job = self._get_first_job(skill, mock_adb, RICH_CARD_XML)
        assert job.salary == "20-30K"

    def test_extracts_location(self, skill, mock_adb):
        job = self._get_first_job(skill, mock_adb, RICH_CARD_XML)
        assert job.location == "北京·朝阳"

    def test_extracts_hr_name(self, skill, mock_adb):
        job = self._get_first_job(skill, mock_adb, RICH_CARD_XML)
        assert job.hr_name == "张三"

    def test_extracts_hr_active(self, skill, mock_adb):
        job = self._get_first_job(skill, mock_adb, RICH_CARD_XML)
        assert job.hr_active == "刚刚活跃"

    def test_title_only_job_has_empty_extra_fields(self, skill, mock_adb):
        mock_adb.shell.side_effect = _xml_shell(TWO_JOBS_XML)
        jobs = skill.get_job_list()
        # Minimal XML: no sibling fields — graceful empty strings
        assert jobs[0].company == ""
        assert jobs[0].salary == ""

    def test_excludes_detail_page_tv_job_name(self, skill, mock_adb):
        # JOB_DETAIL_XML has tv_job_name, not tv_position_name — must return empty list
        mock_adb.shell.side_effect = _xml_shell(JOB_DETAIL_XML)
        jobs = skill.get_job_list()
        assert jobs == []


# ---------------------------------------------------------------------------
# TestPageDetection
# ---------------------------------------------------------------------------

class TestPageDetection:
    def test_home(self, skill, mock_adb):
        mock_adb.shell.side_effect = _xml_shell(HOME_XML)
        assert skill.get_current_page() == PageState.HOME

    def test_job_list(self, skill, mock_adb):
        mock_adb.shell.side_effect = _xml_shell(JOB_LIST_XML)
        assert skill.get_current_page() == PageState.JOB_LIST

    def test_job_detail(self, skill, mock_adb):
        mock_adb.shell.side_effect = _xml_shell(JOB_DETAIL_XML)
        assert skill.get_current_page() == PageState.JOB_DETAIL

    def test_chat(self, skill, mock_adb):
        mock_adb.shell.side_effect = _xml_shell(CHAT_XML)
        assert skill.get_current_page() == PageState.CHAT

    def test_dialog_has_highest_priority(self, skill, mock_adb):
        # daily_limit dialog wins over job_list even if both matched somehow
        mock_adb.shell.side_effect = _xml_shell(DAILY_LIMIT_XML)
        assert skill.get_current_page() == PageState.DIALOG

    def test_unknown_on_empty_page(self, skill, mock_adb):
        mock_adb.shell.side_effect = _xml_shell(EMPTY_XML)
        assert skill.get_current_page() == PageState.UNKNOWN

    def test_accepts_pre_fetched_xml(self, skill, mock_adb):
        # No shell calls should occur when xml is supplied
        mock_adb.shell.reset_mock()
        result = skill.get_current_page(xml=HOME_XML)
        mock_adb.shell.assert_not_called()
        assert result == PageState.HOME


# ---------------------------------------------------------------------------
# TestDialogDetection
# ---------------------------------------------------------------------------

class TestDialogDetection:
    def test_daily_limit(self, skill, mock_adb):
        mock_adb.shell.side_effect = _xml_shell(DAILY_LIMIT_XML)
        assert skill.detect_dialog() == DialogType.DAILY_LIMIT

    def test_login_required(self, skill, mock_adb):
        mock_adb.shell.side_effect = _xml_shell(LOGIN_XML)
        assert skill.detect_dialog() == DialogType.LOGIN_REQUIRED

    def test_job_offline(self, skill, mock_adb):
        mock_adb.shell.side_effect = _xml_shell(JOB_OFFLINE_XML)
        assert skill.detect_dialog() == DialogType.JOB_OFFLINE

    def test_existing_chat(self, skill, mock_adb):
        mock_adb.shell.side_effect = _xml_shell(EXISTING_CHAT_XML)
        assert skill.detect_dialog() == DialogType.EXISTING_CHAT

    def test_no_dialog_on_normal_page(self, skill, mock_adb):
        mock_adb.shell.side_effect = _xml_shell(JOB_LIST_XML)
        assert skill.detect_dialog() == DialogType.NONE

    def test_accepts_pre_fetched_xml(self, skill, mock_adb):
        mock_adb.shell.reset_mock()
        result = skill.detect_dialog(xml=DAILY_LIMIT_XML)
        mock_adb.shell.assert_not_called()
        assert result == DialogType.DAILY_LIMIT


# ---------------------------------------------------------------------------
# TestDialogDismissal
# ---------------------------------------------------------------------------

class TestDialogDismissal:
    def test_daily_limit_taps_confirm_button(self, skill, mock_adb):
        # DAILY_LIMIT_XML has a "我知道了" text node
        mock_adb.shell.return_value = (True, "")
        result = skill.dismiss_dialog(DialogType.DAILY_LIMIT, xml=DAILY_LIMIT_XML)
        assert result is True
        assert any(
            call.args[0].startswith("shell input tap ")
            for call in mock_adb.shell.call_args_list
        )

    def test_login_required_presses_back(self, skill, mock_adb):
        mock_adb.shell.return_value = (True, "")
        skill.dismiss_dialog(DialogType.LOGIN_REQUIRED)
        mock_adb.shell.assert_called_with("shell input keyevent 4", "test_device")

    def test_job_offline_presses_back(self, skill, mock_adb):
        mock_adb.shell.return_value = (True, "")
        skill.dismiss_dialog(DialogType.JOB_OFFLINE)
        mock_adb.shell.assert_called_with("shell input keyevent 4", "test_device")

    def test_existing_chat_returns_true_without_action(self, skill, mock_adb):
        mock_adb.reset_mock()
        result = skill.dismiss_dialog(DialogType.EXISTING_CHAT)
        assert result is True
        mock_adb.shell.assert_not_called()

    def test_none_dialog_returns_false(self, skill, mock_adb):
        result = skill.dismiss_dialog(DialogType.NONE)
        assert result is False


# ---------------------------------------------------------------------------
# TestApplyToJob
# ---------------------------------------------------------------------------

class TestApplyToJob:
    def test_apply_returns_false_when_btn_absent(self, skill, mock_adb):
        # JOB_DETAIL_XML has btn_chat but NOT btn_apply
        mock_adb.shell.side_effect = _xml_shell(JOB_DETAIL_XML)
        assert skill.apply_to_job() is False
        assert not any(
            call.args[0].startswith("shell input tap ")
            for call in mock_adb.shell.call_args_list
        )

    def test_can_apply_false_when_btn_absent(self, skill, mock_adb):
        mock_adb.shell.side_effect = _xml_shell(JOB_DETAIL_XML)
        assert skill.can_apply() is False

    def test_can_apply_true_when_btn_present(self, skill, mock_adb):
        apply_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[50,100][900,160]" resource-id="{PKG}:id/tv_job_name" text="工程师"/>
    <node bounds="[200,1800][880,1900]" resource-id="{PKG}:id/btn_apply" text="投递简历"/>
  </node>
</hierarchy>"""
        mock_adb.shell.side_effect = _xml_shell(apply_xml)
        assert skill.can_apply() is True

    def test_apply_taps_when_btn_present(self, skill, mock_adb):
        apply_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[200,1800][880,1900]" resource-id="{PKG}:id/btn_apply" text="投递简历"/>
  </node>
</hierarchy>"""
        mock_adb.shell.side_effect = _xml_shell(apply_xml) + [(True, "")]
        result = skill.apply_to_job()
        assert result is True
        assert any(
            call.args[0].startswith("shell input tap ")
            for call in mock_adb.shell.call_args_list
        )


# ---------------------------------------------------------------------------
# TestScrollJobList
# ---------------------------------------------------------------------------

class TestScrollJobList:
    def test_collects_jobs_up_to_limit(self, skill, mock_adb):
        # First dump: 2 jobs; limit is 2 → no scroll needed
        mock_adb.shell.side_effect = _make_shell_seq(TWO_JOBS_XML)
        jobs = skill.scroll_job_list(n_jobs=2)
        assert len(jobs) == 2

    def test_scrolls_when_initial_page_has_fewer_jobs(self, skill, mock_adb):
        # First page: 2 jobs; need 4 → scroll once → 2 more different jobs
        mock_adb.shell.side_effect = _paged_shell(TWO_JOBS_XML, SCROLLED_XML)
        jobs = skill.scroll_job_list(n_jobs=4)
        assert len(jobs) == 4
        assert any(
            call.args[0].startswith("shell input swipe ")
            for call in mock_adb.shell.call_args_list
        )

    def test_deduplicates_across_pages(self, skill, mock_adb):
        # Both pages return the same XML → deduplicated to 2 titles
        mock_adb.shell.side_effect = _paged_shell(TWO_JOBS_XML)
        jobs = skill.scroll_job_list(n_jobs=10, max_scrolls=3)
        titles = [j.title for j in jobs]
        assert len(titles) == len(set(titles)), "Duplicate job titles found"

    def test_stops_when_no_new_jobs(self, skill, mock_adb):
        # Identical XML repeated → stops after detecting 0 new jobs
        calls = _make_shell_seq(TWO_JOBS_XML) * 5
        mock_adb.shell.side_effect = calls
        jobs = skill.scroll_job_list(n_jobs=20, max_scrolls=10)
        # Should stop early, not scroll 10 times
        assert mock_adb.swipe.call_count < 10

    def test_respects_max_scrolls_cap(self, skill, mock_adb):
        # Each page returns 1 new job; need 100 but cap is 3 scrolls
        xmls = []
        for i in range(10):
            xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[0,100][1080,400]">
      <node bounds="[50,150][700,210]" resource-id="{PKG}:id/tv_position_name" text="职位{i}"/>
    </node>
  </node>
</hierarchy>"""
            xmls.append(xml)
        mock_adb.shell.side_effect = [item for xml in xmls for item in [(True, ""), (True, xml)]]
        jobs = skill.scroll_job_list(n_jobs=100, max_scrolls=3)
        assert mock_adb.swipe.call_count <= 3


# ---------------------------------------------------------------------------
# TestWaitForElement
# ---------------------------------------------------------------------------

class TestWaitForElement:
    def test_returns_element_when_found_immediately(self, skill, mock_adb):
        mock_adb.shell.side_effect = _xml_shell(JOB_DETAIL_XML)
        elem = skill.wait_for_element(
            f"{PKG}:id/btn_chat", timeout=2.0, interval=0.1
        )
        assert elem is not None

    def test_returns_none_on_timeout(self, skill, mock_adb):
        # Always return empty XML → element never found
        mock_adb.shell.return_value = (True, EMPTY_XML)
        elem = skill.wait_for_element(
            f"{PKG}:id/btn_chat", timeout=0.3, interval=0.1
        )
        assert elem is None


# ---------------------------------------------------------------------------
# TestSendGreetingVerification
# ---------------------------------------------------------------------------

MSG = "您好！我对这个职位很感兴趣，期待与您进一步沟通。"


class TestSendGreetingVerification:
    def test_send_without_verify_does_not_poll_after_send(self, skill, mock_adb):
        # Provide chat_input XML for tap_element, then keyevent
        mock_adb.shell.side_effect = _paged_shell(CHAT_XML)
        mock_adb.type_text.return_value = True
        skill.send_greeting(MSG, verify=False)
        # After keyevent, no further shell calls for hierarchy dump
        shell_calls = [str(c) for c in mock_adb.shell.call_args_list]
        hierarchy_calls = [c for c in shell_calls if "uiautomator" in c]
        # Only one hierarchy dump (for tap_element) — no second dump for verify
        assert len(hierarchy_calls) == 1

    def test_send_with_verify_true_returns_true_when_found(self, skill, mock_adb):
        mock_adb.shell.side_effect = _paged_shell(CHAT_XML, CHAT_WITH_MSG_XML)
        mock_adb.type_text.return_value = True
        with patch("time.sleep"):
            result = skill.send_greeting(MSG, verify=True)
        assert result is True

    def test_send_with_verify_true_returns_false_when_not_found(self, skill, mock_adb):
        # Chat page after send never shows the message bubble — verify times out
        initial = _xml_shell(CHAT_XML) + [(True, "")]   # tap_element + keyevent
        mock_adb.shell.side_effect = _cycling_shell(initial, CHAT_XML)
        mock_adb.type_text.return_value = True
        with patch("time.sleep"):
            result = skill.send_greeting(MSG, verify=True)
        # verify_message_sent times out → False
        assert result is False

    def test_verify_message_sent_finds_exact_text(self, skill, mock_adb):
        mock_adb.shell.side_effect = _xml_shell(CHAT_WITH_MSG_XML)
        with patch("time.sleep"):
            assert skill.verify_message_sent(MSG, timeout=0.6) is True

    def test_verify_message_sent_returns_false_on_timeout(self, skill, mock_adb):
        mock_adb.shell.return_value = (True, EMPTY_XML)
        with patch("time.sleep"):
            assert skill.verify_message_sent(MSG, timeout=0.3) is False


# ---------------------------------------------------------------------------
# Card title normalization + edge-card field merge
# ---------------------------------------------------------------------------

def _card(title, company="", salary="", area="", top=100):
    """Build one job card node; empty fields are omitted like a partial render."""
    rows = [f'<node bounds="[50,{top+20}][700,{top+80}]" '
            f'resource-id="{PKG}:id/tv_position_name" text="{title}"/>']
    if company:
        rows.append(f'<node bounds="[50,{top+90}][600,{top+130}]" '
                    f'resource-id="{PKG}:id/tv_company_name" text="{company}"/>')
    if salary:
        rows.append(f'<node bounds="[700,{top+20}][1000,{top+60}]" '
                    f'resource-id="{PKG}:id/tv_salary_desc" text="{salary}"/>')
    if area:
        rows.append(f'<node bounds="[50,{top+140}][500,{top+180}]" '
                    f'resource-id="{PKG}:id/tv_job_area" text="{area}"/>')
    inner = "\n      ".join(rows)
    # Real dumps name the card container view_job_card; get_job_list's ancestor
    # climb stops there, which is what scopes fields to a single card.
    return (f'<node bounds="[0,{top}][1080,{top+300}]" '
            f'resource-id="{PKG}:id/view_job_card">\n      {inner}\n    </node>')


def _page(*cards):
    body = "\n    ".join(cards)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<hierarchy rotation="0">\n'
            f'  <node bounds="[0,0][1080,2400]">\n    {body}\n  </node>\n'
            '</hierarchy>')


class TestNormalizeCardTitle:
    """The async badge span renders as a literal ' &@ ' suffix on list cards."""

    def test_strips_trailing_badge_placeholder(self):
        assert normalize_card_title("AI Agent 产品经理 &@  ") == "AI Agent 产品经理"

    def test_strips_repeated_badge_placeholders(self):
        assert normalize_card_title("AI Builder - 产品 &@  &@  ") == "AI Builder - 产品"

    def test_leaves_clean_title_untouched(self):
        assert normalize_card_title("AI产品经理-豆包") == "AI产品经理-豆包"

    def test_preserves_ellipsis_truncation(self):
        assert normalize_card_title("【2027届秋招】AI Agent 产… &@  ") == "【2027届秋招】AI Agent 产…"

    def test_preserves_mid_title_ampersand_and_at(self):
        # Only the trailing run is a badge; & / @ inside the title are real text.
        assert normalize_card_title("R&D @Home 产品经理") == "R&D @Home 产品经理"

    def test_handles_empty_and_none(self):
        assert normalize_card_title("") == ""
        assert normalize_card_title(None) == ""


class TestScrollJobListDedupAndMerge:
    """A card seen twice must dedup across badge variance and top up empty fields."""

    PAGE1 = _page(
        _card("AI产品经理", company="字节", salary="30-50K", area="北京", top=100),
        # Edge card: partially recycled, only the title rendered.
        _card("AI Agent 产品经理", top=500),
    )
    PAGE2 = _page(
        # Same card, now fully rendered AND carrying the async badge suffix.
        _card("AI Agent 产品经理 &amp;@  ", company="阶跃星辰",
              salary="40-60K", area="上海", top=100),
        _card("数据工程师", company="美团", top=500),
    )

    def _run(self, skill, mock_adb, n_jobs):
        # get_ui_hierarchy = 2 shell calls (dump, cat); swipe = 1 shell call.
        mock_adb.shell.side_effect = [
            (True, ""), (True, self.PAGE1),
            (True, ""),                       # swipe
            (True, ""), (True, self.PAGE2),
        ]
        with patch("time.sleep"):
            return skill.scroll_job_list(n_jobs=n_jobs, max_scrolls=3)

    def test_badge_variant_is_not_a_duplicate(self, skill, mock_adb):
        jobs = self._run(skill, mock_adb, n_jobs=3)
        titles = [j.title for j in jobs]
        assert titles == ["AI产品经理", "AI Agent 产品经理", "数据工程师"]

    def test_stored_title_is_normalized(self, skill, mock_adb):
        jobs = self._run(skill, mock_adb, n_jobs=3)
        assert all("&@" not in j.title for j in jobs)

    def test_empty_fields_are_topped_up_on_second_sighting(self, skill, mock_adb):
        jobs = self._run(skill, mock_adb, n_jobs=3)
        edge = next(j for j in jobs if j.title == "AI Agent 产品经理")
        assert edge.company == "阶跃星辰"
        assert edge.salary == "40-60K"
        assert edge.location == "上海"

    def test_already_populated_fields_are_not_overwritten(self, skill, mock_adb):
        jobs = self._run(skill, mock_adb, n_jobs=3)
        first = next(j for j in jobs if j.title == "AI产品经理")
        assert first.company == "字节"


class TestSubmitSearchRecovery:
    """
    提交搜索后有两种卡死形态，都只能靠点击页面上的按钮恢复：
      ① 结果页停在「网络异常」占位页 → 点「重新加载」
      ② 回车未提交，停在搜索建议浮层 → 点浮层的 tv_search「搜索」
    """

    NETWORK_ERROR_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[100,900][980,960]" resource-id="{PKG}:id/tv_title" text="网络异常，请点击按钮刷新"/>
    <node bounds="[400,1000][680,1080]" resource-id="{PKG}:id/btn_bottom" text="重新加载"/>
  </node>
</hierarchy>"""

    OVERLAY_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[130,190][1027,249]" resource-id="{PKG}:id/et_search" text="FDE"/>
    <node bounds="[890,281][1006,344]" resource-id="{PKG}:id/tv_search" text="搜索"/>
    <node bounds="[0,400][1080,2400]" resource-id="{PKG}:id/recyclerView_list"/>
  </node>
</hierarchy>"""

    BLANK_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0"><node bounds="[0,0][1080,2400]"/></hierarchy>"""

    @staticmethod
    def _prep(skill, waits, xmls):
        """Stub out the polling + dump layers so only the recovery logic runs."""
        skill.wait_for_element = MagicMock(side_effect=waits)
        skill.get_ui_hierarchy = MagicMock(side_effect=xmls)
        skill.tap = MagicMock(return_value=True)
        skill.type_text = MagicMock(return_value=True)

    def test_results_appear_immediately_no_recovery_tap(self, skill):
        found = UIElement(resource_id=f"{PKG}:id/tv_position_name", bounds=(0, 0, 10, 10))
        self._prep(skill, waits=[found], xmls=[])
        with patch("time.sleep"):
            assert skill._submit_search("FDE") is True
        skill.tap.assert_not_called()

    def test_network_error_page_taps_reload_then_succeeds(self, skill):
        found = UIElement(resource_id=f"{PKG}:id/tv_position_name", bounds=(0, 0, 10, 10))
        self._prep(skill, waits=[None, found], xmls=[self.NETWORK_ERROR_XML])
        with patch("time.sleep"):
            assert skill._submit_search("AI产品经理") is True
        skill.tap.assert_called_once_with(540, 1040)

    def test_suggestion_overlay_taps_search_button_then_succeeds(self, skill):
        found = UIElement(resource_id=f"{PKG}:id/tv_position_name", bounds=(0, 0, 10, 10))
        self._prep(skill, waits=[None, found], xmls=[self.OVERLAY_XML])
        with patch("time.sleep"):
            assert skill._submit_search("FDE") is True
        skill.tap.assert_called_once_with(948, 312)

    def test_gives_up_after_three_failed_recovery_taps(self, skill):
        self._prep(
            skill,
            waits=[None, None, None],
            xmls=[self.OVERLAY_XML, self.OVERLAY_XML, self.OVERLAY_XML],
        )
        with patch("time.sleep"):
            assert skill._submit_search("FDE") is False
        assert skill.tap.call_count == 3

    def test_no_results_and_no_recovery_button_returns_false(self, skill):
        """Must NOT return True — a silent success masks the failure downstream."""
        self._prep(skill, waits=[None], xmls=[self.BLANK_XML])
        with patch("time.sleep"):
            assert skill._submit_search("FDE") is False
        skill.tap.assert_not_called()

    def test_type_text_failure_aborts_before_polling(self, skill):
        self._prep(skill, waits=[], xmls=[])
        skill.type_text = MagicMock(return_value=False)
        with patch("time.sleep"):
            assert skill._submit_search("FDE") is False
        skill.wait_for_element.assert_not_called()

    def test_recovery_button_prefers_reload_over_search_submit(self, skill):
        both = self.NETWORK_ERROR_XML.replace(
            "</hierarchy>",
            f'  <node bounds="[890,281][1006,344]" resource-id="{PKG}:id/tv_search"'
            ' text="搜索"/>\n</hierarchy>',
        )
        skill.get_ui_hierarchy = MagicMock(return_value=both)
        btn = skill._find_recovery_button()
        assert btn is not None and btn.text == "重新加载"

    def test_recovery_button_none_on_empty_dump(self, skill):
        skill.get_ui_hierarchy = MagicMock(return_value="")
        assert skill._find_recovery_button() is None


class TestRecoverDetailPage:
    """
    详情页占位页仍会渲染 tv_job_name 与顶部 chips，wait_for_element 判不出异常，
    故成功判据是 dump 中不再含「网络异常」。
    """

    PLACEHOLDER_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[53,300][900,380]" resource-id="{PKG}:id/tv_job_name" text="平台产品经理"/>
    <node bounds="[53,400][300,450]" text="面议"/>
    <node bounds="[100,1100][980,1160]" text="网络异常，请检查网络后重试"/>
    <node bounds="[400,1200][680,1280]" text="重试"/>
  </node>
</hierarchy>"""

    OK_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[53,300][900,380]" resource-id="{PKG}:id/tv_job_name" text="平台产品经理"/>
    <node bounds="[53,500][1027,1500]" resource-id="{PKG}:id/tv_description" text="岗位职责：..."/>
  </node>
</hierarchy>"""

    NO_BUTTON_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]">
    <node bounds="[100,1100][980,1160]" text="网络异常，请检查网络后重试"/>
  </node>
</hierarchy>"""

    def test_healthy_page_returns_true_without_tapping(self, skill):
        skill.get_ui_hierarchy = MagicMock(return_value=self.OK_XML)
        skill.tap = MagicMock(return_value=True)
        assert skill.recover_detail_page() is True
        skill.tap.assert_not_called()

    def test_placeholder_taps_retry_then_succeeds(self, skill):
        # 每轮消耗 2 次 dump：recover 自身一次 + _find_recovery_button 一次。
        skill.get_ui_hierarchy = MagicMock(
            side_effect=[self.PLACEHOLDER_XML, self.PLACEHOLDER_XML, self.OK_XML]
        )
        skill.tap = MagicMock(return_value=True)
        with patch("time.sleep"):
            assert skill.recover_detail_page() is True
        skill.tap.assert_called_once_with(540, 1240)

    def test_gives_up_after_max_rounds(self, skill):
        skill.get_ui_hierarchy = MagicMock(return_value=self.PLACEHOLDER_XML)
        skill.tap = MagicMock(return_value=True)
        with patch("time.sleep"):
            assert skill.recover_detail_page() is False
        assert skill.tap.call_count == 3

    def test_no_retry_button_returns_false(self, skill):
        skill.get_ui_hierarchy = MagicMock(return_value=self.NO_BUTTON_XML)
        skill.tap = MagicMock(return_value=True)
        with patch("time.sleep"):
            assert skill.recover_detail_page() is False
        skill.tap.assert_not_called()

    def test_empty_dump_returns_false(self, skill):
        skill.get_ui_hierarchy = MagicMock(return_value="")
        assert skill.recover_detail_page() is False
