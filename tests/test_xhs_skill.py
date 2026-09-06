"""Unit tests for skills.xhs.xhs_automation_skill."""

from unittest.mock import MagicMock, patch

import pytest

from skills.xhs import XHSAutomationSkill, UIElement


# Minimal XML matching the format uiautomator produces
_SAMPLE_XML = (
    '<hierarchy>'
    '<node resource-id="com.xingin.xhs:id/noteLikeLayout"'
    ' text="" content-desc="like"'
    ' bounds="[100,200][300,400]"/>'
    '</hierarchy>'
)


@pytest.fixture()
def skill(tmp_path):
    """Return a skill instance backed by a mocked ADB runner."""
    s = XHSAutomationSkill(device_id="test-device", output_dir=str(tmp_path))
    s.adb = MagicMock()
    s.adb.shell.return_value = (True, "")
    return s


def test_skill_logger_set(skill):
    """XHSAutomationSkill.__init__ sets a _logger attribute."""
    assert hasattr(skill, "_logger")
    assert skill._logger.name == "XHSAutomationSkill"


def test_find_element_parses_bounds(skill):
    """find_element correctly extracts bounds and computes center from XML."""
    with patch.object(skill, "get_ui_hierarchy", return_value=_SAMPLE_XML):
        elem = skill.find_element(resource_id="com.xingin.xhs:id/noteLikeLayout")

    assert elem is not None
    assert isinstance(elem, UIElement)
    assert elem.bounds == (100, 200, 300, 400)
    assert elem.center == (200, 300)


def test_tap_element_found(skill):
    """tap_element taps the element center when the element is found."""
    mock_elem = UIElement(
        resource_id="com.xingin.xhs:id/noteLikeLayout",
        text="",
        content_desc="like",
        bounds=(100, 200, 300, 400),
    )
    with patch.object(skill, "find_element", return_value=mock_elem):
        with patch.object(skill, "tap") as mock_tap:
            skill.tap_element("like")

    mock_tap.assert_called_once_with(200, 300)


def test_tap_element_not_found(skill):
    """tap_element returns False when the element is absent from the screen."""
    with patch.object(skill, "find_element", return_value=None):
        assert skill.tap_element("like") is False


def test_tap_element_unknown_key(skill):
    """tap_element returns False for a key absent from ELEMENTS."""
    assert skill.tap_element("no_such_key") is False


def test_favorite_returns_false_when_no_element(skill):
    """favorite_current_post returns False and logs a WARNING when button is missing."""
    with patch.object(skill, "find_element", return_value=None):
        result = skill.favorite_current_post()

    assert result is False


def test_like_returns_false_when_no_element(skill):
    """like_current_post returns False and logs a WARNING when button is missing."""
    with patch.object(skill, "find_element", return_value=None):
        result = skill.like_current_post()

    assert result is False
