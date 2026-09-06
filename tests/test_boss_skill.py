"""Tests for skills/boss/boss_automation_skill.py"""
import pytest
from unittest.mock import MagicMock

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))

from skills.android.ui_types import parse_bounds
from skills.boss.boss_automation_skill import BOSSAutomationSkill, BOSS_PACKAGE


SAMPLE_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,1920]" clickable="false">
    <node bounds="[50,200][500,260]" clickable="true"
          resource-id="{BOSS_PACKAGE}:id/tv_position_name" text="Python工程师"/>
    <node bounds="[50,270][500,330]" clickable="true"
          resource-id="{BOSS_PACKAGE}:id/tv_position_name" text="后端工程师"/>
    <node bounds="[600,200][900,260]" clickable="true"
          resource-id="{BOSS_PACKAGE}:id/btn_chat" text="立即沟通"/>
  </node>
</hierarchy>"""

DETAIL_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,1920]" clickable="false">
    <node bounds="[50,100][900,160]" clickable="true"
          resource-id="{BOSS_PACKAGE}:id/tv_job_name" text="Python工程师"/>
    <node bounds="[50,800][400,860]" clickable="false"
          resource-id="{BOSS_PACKAGE}:id/editText_with_scrollbar" text="" clickable="true"/>
  </node>
</hierarchy>"""


@pytest.fixture
def mock_adb():
    adb = MagicMock()
    adb.shell.return_value = (True, "")
    adb.tap.return_value = True
    adb.type_text.return_value = True
    adb.screenshot.return_value = MagicMock()
    return adb


@pytest.fixture
def skill(mock_adb):
    return BOSSAutomationSkill(mock_adb, device_id="test_device")


class TestElements:
    def test_all_required_keys_present(self):
        required = {"search_bar", "job_name", "chat_btn", "chat_input", "filter_btn"}
        assert required.issubset(BOSSAutomationSkill.ELEMENTS.keys())

    def test_all_resource_ids_contain_package(self):
        for key, rid in BOSSAutomationSkill.ELEMENTS.items():
            assert BOSS_PACKAGE in rid, f"ELEMENTS['{key}'] missing package prefix"


class TestParseBounds:
    def test_valid_bounds(self):
        result = parse_bounds("[10,20][200,80]")
        assert result == (10, 20, 200, 80)

    def test_invalid_bounds(self):
        assert parse_bounds("") is None
        assert parse_bounds("bad") is None


class TestFindElement:
    def test_find_by_resource_id(self, skill, mock_adb):
        mock_adb.shell.return_value = (True, SAMPLE_XML)
        elem = skill.find_element(resource_id=f"{BOSS_PACKAGE}:id/tv_position_name")
        assert elem is not None
        assert elem.text == "Python工程师"

    def test_find_nonexistent_resource_id(self, skill, mock_adb):
        mock_adb.shell.return_value = (True, SAMPLE_XML)
        elem = skill.find_element(resource_id=f"{BOSS_PACKAGE}:id/nonexistent")
        assert elem is None

    def test_find_by_text(self, skill, mock_adb):
        mock_adb.shell.return_value = (True, SAMPLE_XML)
        elem = skill.find_element(text="立即沟通")
        assert elem is not None

    def test_empty_xml_returns_none(self, skill, mock_adb):
        mock_adb.shell.return_value = (True, "")
        assert skill.find_element(resource_id="anything") is None

    def test_malformed_xml_returns_none(self, skill, mock_adb):
        mock_adb.shell.return_value = (True, "not xml <<< garbage")
        assert skill.find_element(resource_id="anything") is None


class TestGetJobList:
    def test_returns_job_info_list(self, skill, mock_adb):
        mock_adb.shell.return_value = (True, SAMPLE_XML)
        jobs = skill.get_job_list()
        assert len(jobs) == 2
        assert jobs[0].title == "Python工程师"

    def test_empty_xml_returns_empty_list(self, skill, mock_adb):
        mock_adb.shell.return_value = (True, "")
        assert skill.get_job_list() == []


class TestPressBack:
    def test_press_back_sends_keyevent_4(self, skill, mock_adb):
        skill.press_back()
        mock_adb.shell.assert_called_with("shell input keyevent 4", "test_device")

    def test_press_back_returns_adb_success(self, skill, mock_adb):
        mock_adb.shell.return_value = (True, "")
        assert skill.press_back() is True

    def test_press_back_returns_false_on_failure(self, skill, mock_adb):
        mock_adb.shell.return_value = (False, "error")
        assert skill.press_back() is False


class TestIsOnJobList:
    def test_true_when_job_list_visible(self, skill, mock_adb):
        mock_adb.shell.return_value = (True, SAMPLE_XML)
        assert skill.is_on_job_list() is True

    def test_false_when_on_detail_page(self, skill, mock_adb):
        # Detail XML has tv_job_name not tv_position_name
        mock_adb.shell.return_value = (True, DETAIL_XML)
        assert skill.is_on_job_list() is False

    def test_false_when_xml_empty(self, skill, mock_adb):
        mock_adb.shell.return_value = (True, "")
        assert skill.is_on_job_list() is False


class TestTapElement:
    def test_tap_element_sends_input_tap(self, skill, mock_adb):
        mock_adb.shell.return_value = (True, SAMPLE_XML)
        result = skill.tap_element("job_name")
        assert result is True
        assert any(
            call.args[0].startswith("shell input tap ")
            for call in mock_adb.shell.call_args_list
        )

    def test_tap_unknown_key_returns_false(self, skill):
        assert skill.tap_element("nonexistent_key") is False
