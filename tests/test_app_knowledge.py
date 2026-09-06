"""Tests for core/app_knowledge.py"""
import json
import os
import pytest
import tempfile

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(_ROOT))

# Import the module directly to bypass core/__init__.py eager imports
def _import_direct(rel_path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

_ak_mod = _import_direct("core/app_knowledge.py", "core.app_knowledge")
AppKnowledge = _ak_mod.AppKnowledge


@pytest.fixture
def tmp_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture
def sample_data():
    return {
        "app_name": "TestApp",
        "elements": [
            {
                "visual_description": "Submit button",
                "action": "tap",
                "documentation": {
                    "function": "submits the form",
                    "post_conditions": "confirmation dialog appears",
                },
            }
        ],
    }


class TestPathTraversal:
    def test_dotdot_path_sanitised(self, tmp_dir):
        kb = AppKnowledge("../../etc/passwd", knowledge_dir=tmp_dir)
        # Traversal components stripped — app_name contains no ".." and path is safe
        assert ".." not in kb.app_name
        assert ".." not in kb.path

    def test_slash_replaced(self, tmp_dir):
        kb = AppKnowledge("some/app/name", knowledge_dir=tmp_dir)
        # basename strips the slashes
        assert "/" not in kb.app_name

    def test_path_stays_within_knowledge_dir(self, tmp_dir):
        kb = AppKnowledge("../../evil", knowledge_dir=tmp_dir)
        assert os.path.commonpath([kb.path, tmp_dir]) == tmp_dir


class TestLoad:
    def test_load_valid_file(self, tmp_dir, sample_data):
        kb = AppKnowledge("testapp", knowledge_dir=tmp_dir)
        with open(kb.path, "w", encoding="utf-8") as f:
            json.dump(sample_data, f)
        data = kb.load()
        assert data["app_name"] == "TestApp"

    def test_load_caches_result(self, tmp_dir, sample_data):
        kb = AppKnowledge("testapp", knowledge_dir=tmp_dir)
        with open(kb.path, "w", encoding="utf-8") as f:
            json.dump(sample_data, f)
        data1 = kb.load()
        data2 = kb.load()
        assert data1 is data2  # same object returned from cache

    def test_load_missing_file_raises_with_context(self, tmp_dir):
        kb = AppKnowledge("nonexistent", knowledge_dir=tmp_dir)
        with pytest.raises(FileNotFoundError) as exc_info:
            kb.load()
        assert "nonexistent" in str(exc_info.value)
        assert kb.path in str(exc_info.value)

    def test_load_invalid_json_raises_with_context(self, tmp_dir):
        kb = AppKnowledge("badjson", knowledge_dir=tmp_dir)
        os.makedirs(tmp_dir, exist_ok=True)
        with open(kb.path, "w") as f:
            f.write("{not valid json}")
        with pytest.raises(json.JSONDecodeError) as exc_info:
            kb.load()
        assert "badjson" in str(exc_info.value)


class TestSave:
    def test_save_persists_data(self, tmp_dir, sample_data):
        kb = AppKnowledge("testapp", knowledge_dir=tmp_dir)
        kb.save(sample_data)
        with open(kb.path, "r", encoding="utf-8") as f:
            on_disk = json.load(f)
        assert on_disk["app_name"] == "TestApp"

    def test_save_deep_copies_data(self, tmp_dir, sample_data):
        kb = AppKnowledge("testapp", knowledge_dir=tmp_dir)
        kb.save(sample_data)
        # Mutate original — cached data must not change
        sample_data["app_name"] = "Mutated"
        assert kb._data["app_name"] == "TestApp"

    def test_save_updates_cache(self, tmp_dir, sample_data):
        kb = AppKnowledge("testapp", knowledge_dir=tmp_dir)
        kb.save(sample_data)
        assert kb._data is not None
        assert kb._data["app_name"] == "TestApp"


class TestGetPromptContext:
    def test_returns_empty_when_file_missing(self, tmp_dir):
        kb = AppKnowledge("missing", knowledge_dir=tmp_dir)
        result = kb.get_prompt_context()
        assert result == ""

    def test_returns_formatted_string(self, tmp_dir, sample_data):
        kb = AppKnowledge("testapp", knowledge_dir=tmp_dir)
        kb.save(sample_data)
        ctx = kb.get_prompt_context()
        assert "Submit button" in ctx
        assert "submits the form" in ctx

    def test_page_filter_excludes_non_matching(self, tmp_dir):
        data = {
            "app_name": "App",
            "elements": [
                {"visual_description": "Home btn", "action": "tap",
                 "page": "home", "documentation": {}},
                {"visual_description": "Settings btn", "action": "tap",
                 "page": "settings", "documentation": {}},
            ],
        }
        kb = AppKnowledge("filterapp", knowledge_dir=tmp_dir)
        kb.save(data)
        ctx = kb.get_prompt_context(page_name="home")
        assert "Home btn" in ctx
        assert "Settings btn" not in ctx
