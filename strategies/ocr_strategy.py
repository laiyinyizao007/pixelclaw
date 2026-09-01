"""
OCR + Rule Matching Strategy

Uses OCR to extract text from screenshots and applies rule-based matching.
Level 3 in the fallback hierarchy - always available, no ML model needed.

Supports multiple OCR engines:
- PaddleOCR (recommended, more accurate)
- EasyOCR (alternative)
- Tesseract (fallback)
"""

import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

from .base import Action, ActionType, BaseStrategy, StrategyLevel, StrategyResult


@dataclass
class DetectedElement:
    """Represents a detected UI element."""
    text: str
    bbox: Tuple[int, int, int, int]  # x, y, width, height
    confidence: float
    element_type: str = "text"  # text, button, input, etc.

    @property
    def center(self) -> Tuple[int, int]:
        """Get the center point of the element."""
        x, y, w, h = self.bbox
        return (x + w // 2, y + h // 2)


class OCRStrategy(BaseStrategy):
    """
    Strategy using OCR and rule-based matching.

    This is the most reliable fallback that works without any external
    dependencies beyond OpenCV and basic OCR libraries.
    """

    # Common UI element patterns
    BUTTON_PATTERNS = [
        r"^(?i)(ok|yes|no|cancel|submit|login|sign\s*in|continue|next|back|done|save|delete|confirm)$",
        r"^(?i)(accept|decline|allow|deny|enable|disable|turn\s*on|turn\s*off)$",
        r"^(?i)(install|uninstall|open|close|start|stop|play|pause)$",
    ]

    INPUT_PATTERNS = [
        r"^(?i)(email|username|password|search|phone|name|address)$",
        r"^(?i)(enter\s+.*|type\s+.*|input\s+.*)$",
    ]

    NAVIGATION_PATTERNS = [
        r"^(?i)(home|menu|settings|back|search|profile|account)$",
        r"^(?i)(notifications|messages|favorites|bookmarks)$",
    ]

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.engine = self.config.get("engine", "paddleocr")
        self.lang = self.config.get("lang", "en")
        self.confidence_threshold = self.config.get("confidence_threshold", 0.6)

        self._ocr_engine = None
        self._initialized = False

    def get_level(self) -> StrategyLevel:
        return StrategyLevel.OCR

    def is_available(self) -> bool:
        """Check if OCR dependencies are available."""
        try:
            cv2.__version__
            return True
        except ImportError:
            return False

    def _init_ocr(self) -> bool:
        """Initialize the OCR engine."""
        if self._initialized:
            return True

        try:
            if self.engine == "paddleocr":
                try:
                    from paddleocr import PaddleOCR
                    self._ocr_engine = PaddleOCR(
                        use_angle_cls=True,
                        lang=self.lang,
                        show_log=False,
                    )
                except ImportError:
                    print("PaddleOCR not available, falling back to basic OCR")
                    self._ocr_engine = None

            self._initialized = True
            return True

        except Exception as e:
            print(f"Failed to initialize OCR: {e}")
            return False

    def _detect_elements(self, image: Image.Image) -> List[DetectedElement]:
        """Detect text elements using OCR."""
        elements = []

        # Convert PIL to OpenCV format
        cv_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

        if self._ocr_engine and self.engine == "paddleocr":
            # Use PaddleOCR
            result = self._ocr_engine.ocr(cv_image, cls=True)
            if result and result[0]:
                for line in result[0]:
                    if line:
                        bbox, (text, conf) = line
                        if conf >= self.confidence_threshold:
                            # Convert bbox to x, y, w, h format
                            x_coords = [p[0] for p in bbox]
                            y_coords = [p[1] for p in bbox]
                            x = int(min(x_coords))
                            y = int(min(y_coords))
                            w = int(max(x_coords) - x)
                            h = int(max(y_coords) - y)

                            element = DetectedElement(
                                text=text.strip(),
                                bbox=(x, y, w, h),
                                confidence=conf,
                                element_type=self._classify_element(text)
                            )
                            elements.append(element)

        else:
            # Fallback: Basic template matching for known UI patterns
            elements = self._detect_by_template(cv_image)

        return elements

    def _classify_element(self, text: str) -> str:
        """Classify the type of UI element based on text."""
        text = text.strip().lower()

        for pattern in self.BUTTON_PATTERNS:
            if re.match(pattern, text):
                return "button"

        for pattern in self.INPUT_PATTERNS:
            if re.match(pattern, text):
                return "input"

        for pattern in self.NAVIGATION_PATTERNS:
            if re.match(pattern, text):
                return "navigation"

        return "text"

    def _detect_by_template(self, cv_image: np.ndarray) -> List[DetectedElement]:
        """Fallback detection using template matching and contours."""
        elements = []

        # Convert to grayscale
        gray = cv2.cvtColor(cv_image, cv2.COLOR_BGR2GRAY)

        # Detect potential buttons/containers using contour detection
        _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            # Filter by size (likely UI elements)
            if 50 < w < 800 and 30 < h < 200:
                elements.append(DetectedElement(
                    text="",
                    bbox=(x, y, w, h),
                    confidence=0.5,
                    element_type="unknown"
                ))

        return elements

    def _match_goal_to_action(
        self,
        goal: str,
        elements: List[DetectedElement]
    ) -> Optional[Action]:
        """
        Match the user's goal to an action based on detected elements.

        This uses keyword matching and heuristics to determine the best action.
        """
        goal_lower = goal.lower()

        # Extract action intent from goal
        action_intents = {
            ActionType.TAP: ["click", "tap", "press", "select", "choose", "open"],
            ActionType.SWIPE: ["swipe", "scroll", "drag", "slide"],
            ActionType.TYPE: ["type", "enter", "input", "fill", "write"],
            ActionType.BACK: ["back", "return", "go back", "previous"],
            ActionType.HOME: ["home", "main screen", "go home"],
        }

        # Determine action type
        detected_action = ActionType.TAP  # Default
        for action, keywords in action_intents.items():
            if any(kw in goal_lower for kw in keywords):
                detected_action = action
                break

        # Find target element
        if detected_action == ActionType.TAP:
            # Extract target from goal (e.g., "click login button" -> "login")
            words = goal_lower.split()
            target_keywords = [w for w in words if w not in action_intents[ActionType.TAP]]

            best_match = None
            best_score = 0

            for elem in elements:
                elem_text = elem.text.lower()
                score = 0

                # Exact match
                if any(kw == elem_text for kw in target_keywords):
                    score = 1.0
                # Partial match
                elif any(kw in elem_text for kw in target_keywords):
                    score = 0.8
                # Button type bonus
                if elem.element_type == "button":
                    score += 0.1

                if score > best_score:
                    best_score = score
                    best_match = elem

            if best_match and best_score >= 0.5:
                x, y = best_match.center
                return Action(
                    action_type=ActionType.TAP,
                    params={"x": x, "y": y},
                    reasoning=f"Matched '{best_match.text}' element to goal",
                    confidence=best_score,
                )

        elif detected_action == ActionType.BACK:
            return Action(
                action_type=ActionType.BACK,
                params={},
                reasoning="Navigate back based on goal",
                confidence=0.7,
            )

        elif detected_action == ActionType.HOME:
            return Action(
                action_type=ActionType.HOME,
                params={},
                reasoning="Go to home screen based on goal",
                confidence=0.7,
            )

        elif detected_action == ActionType.SWIPE:
            # Default swipe down
            return Action(
                action_type=ActionType.SWIPE,
                params={"x1": 540, "y1": 1200, "x2": 540, "y2": 600},
                reasoning="Scroll based on goal",
                confidence=0.5,
            )

        return None

    async def analyze(
        self,
        screenshot: Image.Image,
        goal: str,
        context: Optional[Dict[str, Any]] = None
    ) -> StrategyResult:
        """Analyze screenshot using OCR and rule matching."""
        if not self.is_available():
            return self._create_error_result(
                "OpenCV not available",
                StrategyLevel.OCR
            )

        if not self._init_ocr():
            return self._create_error_result(
                "Failed to initialize OCR engine",
                StrategyLevel.OCR
            )

        start_time = time.time()

        try:
            # Detect elements
            elements = self._detect_elements(screenshot)

            if not elements:
                return self._create_error_result(
                    "No text elements detected",
                    StrategyLevel.OCR
                )

            # Match goal to action
            action = self._match_goal_to_action(goal, elements)

            if action:
                self.record_success()
                return StrategyResult(
                    success=True,
                    level=StrategyLevel.OCR,
                    action=action,
                    raw_response=f"Detected {len(elements)} elements",
                    processing_time=time.time() - start_time,
                    metadata={
                        "engine": self.engine,
                        "elements_detected": len(elements),
                        "element_list": [e.text for e in elements[:10]],
                    }
                )
            else:
                return self._create_error_result(
                    "Could not match goal to any element",
                    StrategyLevel.OCR
                )

        except Exception as e:
            return self._create_error_result(
                f"OCR analysis error: {str(e)}",
                StrategyLevel.OCR
            )