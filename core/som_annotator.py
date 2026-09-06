"""
SoM (Set-of-Mark) Annotator

Overlays numbered bounding boxes on screenshots using UIAutomator XML bounds.
VLMs can then reference UI elements by index instead of raw coordinates.
"""

import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Set, Tuple

from PIL import Image, ImageDraw, ImageFont


def _load_font(size: int = 18) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a TrueType font with graceful fallback to PIL default."""
    candidates = [
        "arial.ttf",
        "Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    # PIL default bitmap font — scale workaround: draw into a temp image twice as large
    return ImageFont.load_default()


class SoMAnnotator:
    """Annotates screenshots with numbered bounding boxes from UIAutomator XML."""

    def __init__(
        self,
        box_color: str = "red",
        text_color: str = "white",
        line_width: int = 2,
        font_size: int = 18,
    ):
        self.box_color = box_color
        self.text_color = text_color
        self.line_width = line_width
        self._font = _load_font(font_size)

    def annotate(
        self,
        screenshot: Image.Image,
        xml_dump: str,
    ) -> Tuple[Image.Image, Dict[int, Tuple[int, int]]]:
        """
        Annotate screenshot with numbered bounding boxes.

        Args:
            screenshot: Original PIL screenshot.
            xml_dump: UIAutomator XML dump string (from adb shell cat).

        Returns:
            (annotated_image, som_mapping) where som_mapping maps
            {som_id: (center_x, center_y)}.
        """
        elements = self._parse_xml(xml_dump)
        annotated = screenshot.copy()
        draw = ImageDraw.Draw(annotated)
        som_mapping: Dict[int, Tuple[int, int]] = {}

        img_w, img_h = annotated.size

        for idx, (bounds, _) in enumerate(elements, start=1):
            x1, y1, x2, y2 = bounds
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            som_mapping[idx] = (cx, cy)

            draw.rectangle(
                [x1, y1, x2, y2],
                outline=self.box_color,
                width=self.line_width,
            )

            label = str(idx)
            # Measure label box size
            try:
                bbox = draw.textbbox((0, 0), label, font=self._font)
                lw = bbox[2] - bbox[0] + 6
                lh = bbox[3] - bbox[1] + 4
            except AttributeError:
                # Older Pillow without textbbox
                lw = len(label) * 10 + 6
                lh = 20

            # Clamp label position to stay within image bounds
            lx = min(max(x1, 0), img_w - lw)
            ly = min(max(y1 - lh, 0), img_h - lh)

            draw.rectangle([lx, ly, lx + lw, ly + lh], fill=self.box_color)
            draw.text((lx + 3, ly + 2), label, fill=self.text_color, font=self._font)

        return annotated, som_mapping

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_xml(
        self, xml_dump: str
    ) -> List[Tuple[Tuple[int, int, int, int], dict]]:
        """Extract clickable element bounds from UIAutomator XML, deduplicating overlapping boxes."""
        elements: List[Tuple[Tuple[int, int, int, int], dict]] = []
        seen_bounds: Set[Tuple[int, int, int, int]] = set()
        try:
            root = ET.fromstring(xml_dump)
            for node in root.iter("node"):
                if node.attrib.get("clickable") != "true":
                    continue
                bounds = self._parse_bounds(node.attrib.get("bounds", ""))
                if bounds and bounds not in seen_bounds:
                    seen_bounds.add(bounds)
                    elements.append((bounds, node.attrib))
        except ET.ParseError:
            pass
        return elements

    @staticmethod
    def _parse_bounds(
        bounds_str: str,
    ) -> Optional[Tuple[int, int, int, int]]:
        """Parse '[x1,y1][x2,y2]' into (x1, y1, x2, y2)."""
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str)
        if not m:
            return None
        return tuple(int(g) for g in m.groups())  # type: ignore[return-value]
