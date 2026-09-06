"""Tests for core/som_annotator.py"""
import pytest
from PIL import Image

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(_ROOT))

def _import_direct(rel_path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

_sa_mod = _import_direct("core/som_annotator.py", "core.som_annotator")
SoMAnnotator = _sa_mod.SoMAnnotator


SIMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,1920]" clickable="false" resource-id="root">
    <node bounds="[10,20][200,80]" clickable="true" resource-id="btn1" text="OK"/>
    <node bounds="[210,20][400,80]" clickable="true" resource-id="btn2" text="Cancel"/>
    <node bounds="[0,100][1080,200]" clickable="false" resource-id="label" text="Header"/>
  </node>
</hierarchy>"""

DUPLICATE_BOUNDS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,1920]" clickable="false">
    <node bounds="[10,20][200,80]" clickable="true" text="A"/>
    <node bounds="[10,20][200,80]" clickable="true" text="A_dup"/>
  </node>
</hierarchy>"""

INVALID_XML = "this is not xml <<<"

EMPTY_CLICKABLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,1920]" clickable="false" text="none"/>
</hierarchy>"""


@pytest.fixture
def annotator():
    return SoMAnnotator()


@pytest.fixture
def small_screenshot():
    return Image.new("RGB", (1080, 1920), color=(200, 200, 200))


class TestParseBounds:
    def test_valid_bounds(self):
        result = SoMAnnotator._parse_bounds("[10,20][200,80]")
        assert result == (10, 20, 200, 80)

    def test_zero_origin(self):
        result = SoMAnnotator._parse_bounds("[0,0][1080,1920]")
        assert result == (0, 0, 1080, 1920)

    def test_empty_string(self):
        assert SoMAnnotator._parse_bounds("") is None

    def test_malformed_string(self):
        assert SoMAnnotator._parse_bounds("10,20,200,80") is None

    def test_partial_string(self):
        assert SoMAnnotator._parse_bounds("[10,20]") is None


class TestParseXml:
    def test_clickable_elements_extracted(self, annotator):
        elements = annotator._parse_xml(SIMPLE_XML)
        assert len(elements) == 2

    def test_non_clickable_excluded(self, annotator):
        elements = annotator._parse_xml(SIMPLE_XML)
        bounds_list = [e[0] for e in elements]
        assert (0, 100, 1080, 200) not in bounds_list

    def test_duplicate_bounds_deduplicated(self, annotator):
        elements = annotator._parse_xml(DUPLICATE_BOUNDS_XML)
        assert len(elements) == 1

    def test_invalid_xml_returns_empty(self, annotator):
        elements = annotator._parse_xml(INVALID_XML)
        assert elements == []

    def test_no_clickable_returns_empty(self, annotator):
        elements = annotator._parse_xml(EMPTY_CLICKABLE_XML)
        assert elements == []


class TestAnnotate:
    def test_returns_image_and_mapping(self, annotator, small_screenshot):
        img, mapping = annotator.annotate(small_screenshot, SIMPLE_XML)
        assert isinstance(img, Image.Image)
        assert isinstance(mapping, dict)

    def test_mapping_keys_are_consecutive(self, annotator, small_screenshot):
        _, mapping = annotator.annotate(small_screenshot, SIMPLE_XML)
        assert sorted(mapping.keys()) == list(range(1, len(mapping) + 1))

    def test_center_coordinates_within_bounds(self, annotator, small_screenshot):
        _, mapping = annotator.annotate(small_screenshot, SIMPLE_XML)
        img_w, img_h = small_screenshot.size
        for cx, cy in mapping.values():
            assert 0 <= cx <= img_w
            assert 0 <= cy <= img_h

    def test_annotated_image_same_size(self, annotator, small_screenshot):
        annotated, _ = annotator.annotate(small_screenshot, SIMPLE_XML)
        assert annotated.size == small_screenshot.size

    def test_empty_xml_returns_empty_mapping(self, annotator, small_screenshot):
        _, mapping = annotator.annotate(small_screenshot, EMPTY_CLICKABLE_XML)
        assert mapping == {}

    def test_invalid_xml_returns_empty_mapping(self, annotator, small_screenshot):
        _, mapping = annotator.annotate(small_screenshot, INVALID_XML)
        assert mapping == {}

    def test_label_box_clamped_to_image_edge(self, annotator):
        # Element at top of screen — label would extend above y=0 without clamping
        xml = """<?xml version="1.0"?>
<hierarchy>
  <node bounds="[0,0][100,30]" clickable="true" text="topleft"/>
</hierarchy>"""
        img = Image.new("RGB", (1080, 1920))
        # Should not raise; label position must stay within image
        annotated, mapping = annotator.annotate(img, xml)
        assert len(mapping) == 1
