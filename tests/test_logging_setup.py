"""Unit tests for utils.logging_setup."""

import json
import logging

import pytest

from utils.logging_setup import setup_logger


def test_creates_log_file(tmp_path):
    """setup_logger creates a JSONL file in the specified log_dir."""
    logger = setup_logger("tc_creates_file", log_dir=str(tmp_path))
    logger.info("hello")
    files = list(tmp_path.glob("tc_creates_file_*.jsonl"))
    assert len(files) == 1


def test_jsonl_format(tmp_path):
    """Each log line is valid JSON with ts / level / name / msg fields."""
    logger = setup_logger("tc_jsonl_fmt", log_dir=str(tmp_path))
    logger.info("test message")
    files = list(tmp_path.glob("tc_jsonl_fmt_*.jsonl"))
    assert files, "JSONL log file not found"
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert lines, "Log file is empty"
    doc = json.loads(lines[-1])
    assert doc["level"] == "INFO"
    assert doc["msg"] == "test message"
    assert "ts" in doc
    assert "name" in doc


def test_exc_info_captured(tmp_path):
    """logger.error(exc_info=True) writes an 'exc' list field to the JSONL line."""
    logger = setup_logger("tc_exc_info", log_dir=str(tmp_path))
    try:
        raise ValueError("boom")
    except Exception:
        logger.error("caught", exc_info=True)
    files = list(tmp_path.glob("tc_exc_info_*.jsonl"))
    assert files
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    doc = json.loads(lines[-1])
    assert "exc" in doc, "exc field missing from JSONL record"
    assert any("ValueError" in e for e in doc["exc"])


def test_idempotent_handlers(tmp_path):
    """Calling setup_logger twice with the same name must not duplicate handlers."""
    logger = setup_logger("tc_idempotent", log_dir=str(tmp_path))
    n_handlers = len(logger.handlers)
    setup_logger("tc_idempotent", log_dir=str(tmp_path))
    assert len(logger.handlers) == n_handlers


def test_mkdir_creates_dir(tmp_path):
    """setup_logger auto-creates a nested log_dir that does not yet exist."""
    nested = tmp_path / "a" / "b" / "c"
    assert not nested.exists()
    setup_logger("tc_mkdir", log_dir=str(nested))
    assert nested.is_dir()
