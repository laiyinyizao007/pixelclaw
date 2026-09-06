"""Tests for scenarios/boss/scripts/scrape_job_details.py helpers."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parents[1]
_SCRIPT = ROOT / "scenarios" / "boss" / "scripts" / "scrape_job_details.py"

_spec = importlib.util.spec_from_file_location("scrape_job_details", _SCRIPT)
scrape_mod = importlib.util.module_from_spec(_spec)
sys.modules["scrape_job_details"] = scrape_mod

# 脚本在 import 时会把 sys.stdout 包成 UTF-8 TextIOWrapper（Windows 中文输出所需）。
# 该包装器被回收时会关闭底层 buffer，进而关掉 pytest 的捕获流，故导入时屏蔽该分支。
with patch.object(sys, "platform", "linux"):
    _spec.loader.exec_module(scrape_mod)


def _skill(dumps, details=None):
    """Fake skill whose get_ui_hierarchy() yields `dumps` in order."""
    s = MagicMock()
    s.get_ui_hierarchy.side_effect = list(dumps)
    s.get_job_detail.side_effect = lambda xml: (details or {}).get(xml, {})
    return s


class TestScrollForCompanyInfo:
    def test_stops_once_company_captured(self):
        skill = _skill(
            ["a", "b"],
            {"a": {"description": "d"}, "b": {"company": "C", "company_info": "I"}},
        )
        detail = {}
        with patch.object(scrape_mod.time, "sleep"):
            scrape_mod._scroll_for_company_info(skill, detail)
        assert detail["company"] == "C"
        assert skill.scroll_down.call_count == 2

    def test_survives_consecutive_stale_dumps(self):
        """连续 3 轮 XML 完全一致后第 4 轮才出现公司信息——不能提前退出。"""
        skill = _skill(
            ["a", "a", "a", "b"],
            {"a": {"description": "d"}, "b": {"company": "C", "company_info": "I"}},
        )
        detail = {}
        with patch.object(scrape_mod.time, "sleep"):
            scrape_mod._scroll_for_company_info(skill, detail)
        assert detail["company"] == "C"
        assert skill.scroll_down.call_count == 4

    def test_gives_up_after_max_stale_dumps(self):
        n = scrape_mod._MAX_STALE_DUMPS
        skill = _skill(["a"] * (n + 5), {"a": {"description": "d"}})
        detail = {}
        with patch.object(scrape_mod.time, "sleep"):
            scrape_mod._scroll_for_company_info(skill, detail)
        assert "company" not in detail
        assert skill.scroll_down.call_count == n + 1

    def test_stops_on_empty_dump(self):
        skill = _skill(["a", ""], {"a": {"description": "d"}})
        detail = {}
        with patch.object(scrape_mod.time, "sleep"):
            scrape_mod._scroll_for_company_info(skill, detail)
        assert skill.scroll_down.call_count == 2

    def test_respects_max_scrolls_ceiling(self):
        n = scrape_mod._MAX_DETAIL_SCROLLS
        dumps = [str(i) for i in range(n + 5)]
        skill = _skill(dumps, {d: {"description": d} for d in dumps})
        detail = {}
        with patch.object(scrape_mod.time, "sleep"):
            scrape_mod._scroll_for_company_info(skill, detail)
        assert skill.scroll_down.call_count == n

    def test_stale_counter_resets_after_change(self):
        """中途页面变化应重置计数，不应因累计不变次数而提前放弃。"""
        skill = _skill(
            ["a", "a", "b", "b", "b", "c"],
            {
                "a": {"description": "d"},
                "b": {"description": "dd"},
                "c": {"company": "C", "company_info": "I"},
            },
        )
        detail = {}
        with patch.object(scrape_mod.time, "sleep"):
            scrape_mod._scroll_for_company_info(skill, detail)
        assert detail["company"] == "C"
        assert skill.scroll_down.call_count == 6


class TestMergeDetail:
    def test_keeps_longer_description(self):
        dst = {"description": "short"}
        scrape_mod._merge_detail(dst, {"description": "much longer text"})
        assert dst["description"] == "much longer text"

        scrape_mod._merge_detail(dst, {"description": "tiny"})
        assert dst["description"] == "much longer text"

    def test_appends_unique_raw_texts(self):
        dst = {"raw_texts": ["a", "b"]}
        scrape_mod._merge_detail(dst, {"raw_texts": ["b", "c"]})
        assert dst["raw_texts"] == ["a", "b", "c"]

    def test_does_not_overwrite_existing_scalar(self):
        dst = {"company": "First"}
        scrape_mod._merge_detail(dst, {"company": "Second", "location": "SH"})
        assert dst["company"] == "First"
        assert dst["location"] == "SH"
