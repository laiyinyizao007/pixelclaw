"""Unit tests for utils.web_search."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from utils.web_search import search


def _make_ddgs(results):
    """Return a mock DDGS context-manager that yields results from .text()."""
    mock_instance = MagicMock()
    mock_instance.__enter__ = MagicMock(return_value=mock_instance)
    mock_instance.__exit__ = MagicMock(return_value=False)
    mock_instance.text.return_value = results
    return MagicMock(return_value=mock_instance)


def test_search_returns_list():
    """search() returns a list[str] when DDGS succeeds."""
    ddgs_cls = _make_ddgs([
        {"title": "Guide", "body": "How to fix ADB"},
        {"title": "Forum", "body": "Try reconnecting cable"},
    ])
    with patch("utils.web_search._DDGS", ddgs_cls):
        result = search("adb error", max_results=2)
    assert isinstance(result, list)
    assert len(result) == 2
    assert "Guide" in result[0]
    assert "How to fix ADB" in result[0]


def test_search_network_failure():
    """search() returns [] when DDGS raises — never propagates."""
    failing_cls = MagicMock(side_effect=ConnectionError("network down"))
    with patch("utils.web_search._DDGS", failing_cls):
        result = search("test query")
    assert result == []


def test_search_empty_results():
    """search() returns [] when DDGS returns no hits."""
    ddgs_cls = _make_ddgs([])
    with patch("utils.web_search._DDGS", ddgs_cls):
        result = search("obscure query")
    assert result == []


def test_search_logs_query(caplog):
    """search() logs a WARNING when DDGS is unavailable (_DDGS=None)."""
    with patch("utils.web_search._DDGS", None):
        with caplog.at_level(logging.WARNING, logger="utils.web_search"):
            result = search("boss直聘 error")
    assert result == []
    assert any("未安装" in r.message for r in caplog.records)
