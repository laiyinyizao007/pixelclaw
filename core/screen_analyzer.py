"""
Screen Analyzer Module

Analyzes device screenshots to extract information:
- Visual features extraction
- Text detection via OCR
- UI element detection
- Screen state classification
"""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image


@dataclass
class UIElement:
    """Represents a detected UI element."""
    element_type: str  # button, text, input, image, etc.
    bbox: Tuple[int, int, int, int]  # x, y, width, height
    text: Optional[str] = None
    confidence: float = 0.0
    attributes: Dict[str, Any] = field(default_factory=dict)

    @property
    def center(self) -> Tuple[int, int]:
        """Get the center point of the element."""
        x, y, w, h = self.bbox
        return (x + w // 2, y + h // 2)

    @property
    def area(self) -> int:
        """Get the area of the element."""
        return self.bbox[2] * self.bbox[3]


@dataclass
class ScreenAnalysis:
    """Result of screen analysis."""
    screen_size: Tuple[int, int]
    elements: List[UIElement]
    dominant_colors: List[Tuple[int, int, int]]
    brightness: float
    screen_state: str  # home, app, loading, error, etc.
    text_content: List[str]
    timestamp: float
    metadata: Dict[str, Any] = field(default_factory=dict)


class ScreenAnalyzer:
    """
    Analyzes device screenshots to understand the current screen state.

    Uses computer vision techniques to detect UI elements, text,
    and classify the overall screen state.
    """

    # Color ranges for common UI elements
    BUTTON_COLOR_RANGES = {
        "blue_button": ([100, 150, 50], [130, 255, 200]),
        "green_button": ([40, 100, 50], [80, 255, 200]),
        "red_button": ([0, 150, 50], [10, 255, 200]),
    }

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.ocr_engine = None
        self._init_ocr()

    def _init_ocr(self):
        """Initialize OCR engine if available."""
        try:
            from paddleocr import PaddleOCR
            self.ocr_engine = PaddleOCR(
                use_angle_cls=True,
                lang='en',
                show_log=False,
            )
        except ImportError:
            self.ocr_engine = None

    def analyze(self, screenshot: Image.Image) -> ScreenAnalysis:
        """
        Analyze a screenshot and extract information.

        Args:
            screenshot: PIL Image of the screen

        Returns:
            ScreenAnalysis with detected elements and metadata
        """
        import time
        timestamp = time.time()

        # Convert to OpenCV format
        cv_image = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

        # Get screen dimensions
        height, width = cv_image.shape[:2]

        # Detect elements
        elements = self._detect_elements(cv_image)

        # Extract colors
        dominant_colors = self._extract_dominant_colors(cv_image)

        # Calculate brightness
        brightness = self._calculate_brightness(cv_image)

        # Classify screen state
        screen_state = self._classify_screen_state(cv_image, elements)

        # Extract text
        text_content = self._extract_text(cv_image)

        return ScreenAnalysis(
            screen_size=(width, height),
            elements=elements,
            dominant_colors=dominant_colors,
            brightness=brightness,
            screen_state=screen_state,
            text_content=text_content,
            timestamp=timestamp,
        )

    def _detect_elements(self, cv_image: np.ndarray) -> List[UIElement]:
        """Detect UI elements in the image."""
        elements = []

        # Convert to grayscale
        gray = cv2.cvtColor(cv_image, cv2.COLOR_BGR2GRAY)

        # Detect rectangles (potential buttons/containers)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 50, 150)

        contours, _ = cv2.findContours(
            edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)

            # Filter by size (likely UI elements)
            if 80 < w < 600 and 40 < h < 200:
                # Classify element type
                element_type = self._classify_element_type(
                    cv_image, x, y, w, h
                )

                element = UIElement(
                    element_type=element_type,
                    bbox=(x, y, w, h),
                    confidence=0.6,
                )
                elements.append(element)

        # Merge overlapping elements
        elements = self._merge_overlapping_elements(elements)

        return elements

    def _classify_element_type(
        self,
        cv_image: np.ndarray,
        x: int,
        y: int,
        w: int,
        h: int
    ) -> str:
        """Classify the type of a UI element."""
        # Extract region
        roi = cv_image[y:y+h, x:x+w]

        if roi.size == 0:
            return "unknown"

        # Calculate aspect ratio
        aspect_ratio = w / h if h > 0 else 0

        # Check for rounded corners (typical of buttons)
        is_rounded = self._has_rounded_corners(roi)

        # Check for text inside
        has_text = self._region_has_text(roi)

        # Classify based on features
        if is_rounded and 2 < aspect_ratio < 6:
            return "button"
        elif has_text and aspect_ratio > 3:
            return "text_field"
        elif aspect_ratio > 5:
            return "text"
        else:
            return "container"

    def _has_rounded_corners(self, roi: np.ndarray) -> bool:
        """Check if a region has rounded corners."""
        # Simplified check: look for consistent border
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)

        # Check corners
        h, w = thresh.shape
        corner_size = min(h, w) // 10

        # Top-left corner
        tl = thresh[:corner_size, :corner_size]
        # Top-right corner
        tr = thresh[:corner_size, -corner_size:]
        # Bottom-left corner
        bl = thresh[-corner_size:, :corner_size]
        # Bottom-right corner
        br = thresh[-corner_size:, -corner_size:]

        # Count white pixels in corners
        corners = [tl, tr, bl, br]
        white_counts = [np.sum(c == 255) for c in corners]

        # Rounded corners typically have fewer white pixels in corners
        return all(count < corner_size * corner_size * 0.5 for count in white_counts)

    def _region_has_text(self, roi: np.ndarray) -> bool:
        """Check if a region contains text."""
        # Simple heuristic: text regions have high contrast
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

        # Check contrast
        contrast = np.std(gray)
        return contrast > 30

    def _merge_overlapping_elements(
        self,
        elements: List[UIElement],
        overlap_threshold: float = 0.5
    ) -> List[UIElement]:
        """Merge overlapping elements."""
        if not elements:
            return elements

        # Sort by area (largest first)
        elements = sorted(elements, key=lambda e: e.area, reverse=True)

        merged = []
        for elem in elements:
            should_merge = False
            for existing in merged:
                overlap = self._calculate_overlap(elem.bbox, existing.bbox)
                if overlap > overlap_threshold:
                    should_merge = True
                    break

            if not should_merge:
                merged.append(elem)

        return merged

    def _calculate_overlap(
        self,
        bbox1: Tuple[int, int, int, int],
        bbox2: Tuple[int, int, int, int]
    ) -> float:
        """Calculate overlap ratio between two bounding boxes."""
        x1, y1, w1, h1 = bbox1
        x2, y2, w2, h2 = bbox2

        # Calculate intersection
        xi = max(x1, x2)
        yi = max(y1, y2)
        wi = max(0, min(x1 + w1, x2 + w2) - xi)
        hi = max(0, min(y1 + h1, y2 + h2) - yi)

        intersection = wi * hi
        union = w1 * h1 + w2 * h2 - intersection

        return intersection / union if union > 0 else 0

    def _extract_dominant_colors(
        self,
        cv_image: np.ndarray,
        n_colors: int = 5
    ) -> List[Tuple[int, int, int]]:
        """Extract dominant colors from the image."""
        # Resize for faster processing
        small = cv2.resize(cv_image, (100, 100))
        data = small.reshape(-1, 3).astype(np.float32)

        # K-means clustering
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
        _, labels, centers = cv2.kmeans(
            data, n_colors, None, criteria, 10, cv2.KMEANS_RANDOM_CENTERS
        )

        # Convert to RGB
        colors = [tuple(map(int, center[::-1])) for center in centers]

        return colors

    def _calculate_brightness(self, cv_image: np.ndarray) -> float:
        """Calculate average brightness of the image."""
        gray = cv2.cvtColor(cv_image, cv2.COLOR_BGR2GRAY)
        return float(np.mean(gray))

    def _classify_screen_state(
        self,
        cv_image: np.ndarray,
        elements: List[UIElement]
    ) -> str:
        """Classify the current screen state."""
        # Check for common screen patterns
        gray = cv2.cvtColor(cv_image, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape

        # Check for loading indicators (spinners, progress bars)
        loading_indicators = self._detect_loading_indicators(cv_image)
        if loading_indicators:
            return "loading"

        # Check for error dialogs
        error_indicators = self._detect_error_indicators(cv_image)
        if error_indicators:
            return "error"

        # Check for keyboard
        if h > w:  # Portrait mode
            keyboard_region = gray[int(h * 0.6):, :]
            if self._looks_like_keyboard(keyboard_region):
                return "keyboard"

        # Count element types
        button_count = sum(1 for e in elements if e.element_type == "button")
        text_count = sum(1 for e in elements if e.element_type == "text")

        # Heuristic classification
        if button_count == 0 and text_count < 3:
            return "empty_or_splash"
        elif button_count <= 3 and text_count > 10:
            return "content"
        elif button_count > 3:
            return "app"
        else:
            return "unknown"

    def _detect_loading_indicators(self, cv_image: np.ndarray) -> bool:
        """Detect loading indicators in the image."""
        # Look for circular patterns (spinners)
        gray = cv2.cvtColor(cv_image, cv2.COLOR_BGR2GRAY)
        circles = cv2.HoughCircles(
            gray, cv2.HOUGH_GRADIENT, 1, 20,
            param1=50, param2=30, minRadius=10, maxRadius=50
        )

        return circles is not None and len(circles) > 0

    def _detect_error_indicators(self, cv_image: np.ndarray) -> bool:
        """Detect error indicators in the image."""
        # This is a placeholder for more sophisticated error detection
        # Could use OCR to look for error-related text
        return False

    def _looks_like_keyboard(self, region: np.ndarray) -> bool:
        """Check if a region looks like a keyboard."""
        # Keyboards have many small rectangular regions
        blurred = cv2.GaussianBlur(region, (5, 5), 0)
        edges = cv2.Canny(blurred, 50, 150)

        contours, _ = cv2.findContours(
            edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        # Count small rectangles
        key_like_count = 0
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            if 30 < w < 100 and 30 < h < 100:
                key_like_count += 1

        return key_like_count > 10

    def _extract_text(self, cv_image: np.ndarray) -> List[str]:
        """Extract text from the image using OCR."""
        if self.ocr_engine is None:
            return []

        try:
            result = self.ocr_engine.ocr(cv_image, cls=True)
            texts = []

            if result and result[0]:
                for line in result[0]:
                    if line:
                        text = line[1][0]
                        confidence = line[1][1]
                        if confidence > 0.6:
                            texts.append(text)

            return texts
        except Exception as e:
            print(f"OCR error: {e}")
            return []

    def find_element_by_text(
        self,
        screenshot: Image.Image,
        text: str,
        case_sensitive: bool = False
    ) -> Optional[Tuple[int, int]]:
        """
        Find an element containing specific text.

        Returns:
            Center coordinates (x, y) or None
        """
        analysis = self.analyze(screenshot)

        target = text if case_sensitive else text.lower()

        for element in analysis.elements:
            elem_text = element.text or ""
            if not case_sensitive:
                elem_text = elem_text.lower()

            if target in elem_text:
                return element.center

        return None

    def find_clickable_elements(
        self,
        screenshot: Image.Image
    ) -> List[UIElement]:
        """Find all clickable elements (buttons, links)."""
        analysis = self.analyze(screenshot)

        clickable_types = {"button", "text_field", "link"}
        return [e for e in analysis.elements if e.element_type in clickable_types]
