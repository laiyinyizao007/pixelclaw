"""
Memory System Capture Layer

Responsible for capturing and enhancing action records from PixelClaw execution.
Extracts semantic features, generates embeddings, and prepares data for storage.
"""

import asyncio
import hashlib
import logging
import time
from typing import Any, Dict, List, Optional, Tuple, Union
from PIL import Image
import numpy as np

from .schemas import (
    EnhancedAction,
    SemanticFeatures,
    DeviceState,
    MemoryConfig
)

logger = logging.getLogger(__name__)


class SemanticExtractor:
    """Extracts semantic features from actions and screenshots."""

    def __init__(self, config: MemoryConfig):
        """Initialize semantic extractor.

        Args:
            config: Memory configuration
        """
        self.config = config
        self.enabled = config.semantic_extraction

        # Action intent mapping
        self.action_intent_map = {
            "TAP": "selection",
            "SWIPE": "navigation",
            "LONG_PRESS": "context_menu",
            "TYPE": "input",
            "BACK": "navigation",
            "HOME": "navigation",
            "RECENT": "navigation",
            "WAIT": "synchronization",
            "COMPLETE": "completion"
        }

    def extract_action_features(self, action: Dict[str, Any], goal: str,
                              screenshot: Optional[Image.Image] = None,
                              step: int = 0) -> SemanticFeatures:
        """Extract semantic features from an action.

        Args:
            action: Action dictionary from PixelClaw
            goal: Current task goal
            screenshot: Optional screenshot at action time
            step: Step number in sequence

        Returns:
            SemanticFeatures object
        """
        if not self.enabled:
            return SemanticFeatures()

        try:
            features = SemanticFeatures()

            # Extract action intent
            action_type = action.get("action", "")
            features.action_intent = self.action_intent_map.get(action_type, "unknown")
            features.interaction_type = action_type.lower()

            # Extract confidence if available
            if "confidence" in action:
                features.confidence_score = float(action["confidence"])

            # Analyze parameters for UI element detection
            features.ui_elements = self._extract_ui_elements(action)

            # Estimate progress towards goal
            features.goal_progress = self._estimate_progress(step, action_type, goal)

            # Generate context tags
            features.context_tags = self._generate_context_tags(action, goal)

            # Extract screen state if screenshot available
            if screenshot:
                features.screen_state = self._analyze_screen_state(screenshot)

            return features

        except Exception as e:
            logger.warning(f"Failed to extract semantic features: {e}")
            return SemanticFeatures()

    def _extract_ui_elements(self, action: Dict[str, Any]) -> List[str]:
        """Extract UI element information from action parameters."""
        elements = []

        action_type = action.get("action", "")
        params = action.get("params", {})

        if action_type == "TAP" and "x" in params and "y" in params:
            x, y = params["x"], params["y"]
            # Rough screen region classification
            if y < 400:
                elements.append("element:top_bar")
            elif y > 2000:
                elements.append("element:bottom_nav")
            else:
                elements.append("element:main_content")

            # Coordinate-based element type guessing
            if x > 900:  # Right side
                elements.append("button:action")
            elif x < 200:  # Left side
                elements.append("button:back")
            else:
                elements.append("button:primary")

        elif action_type == "TYPE":
            text = params.get("text", "").lower()
            if "@" in text:
                elements.append("input:email")
            elif len(text) > 6 and not " " in text:
                elements.append("input:password")
            else:
                elements.append("input:text")

        elif action_type == "SWIPE":
            x1, y1 = params.get("x1", 0), params.get("y1", 0)
            x2, y2 = params.get("x2", 0), params.get("y2", 0)

            if y1 < y2:  # Swipe down
                elements.append("gesture:scroll_down")
            elif y1 > y2:  # Swipe up
                elements.append("gesture:scroll_up")
            elif x1 < x2:  # Swipe right
                elements.append("gesture:swipe_right")
            else:  # Swipe left
                elements.append("gesture:swipe_left")

        return elements

    def _estimate_progress(self, step: int, action_type: str, goal: str) -> float:
        """Estimate progress towards goal based on step and action type."""
        # Simple heuristic-based progress estimation
        base_progress = min(step / 20.0, 0.9)  # Max 90% based on step count

        # Adjust based on action type
        if action_type == "COMPLETE":
            return 1.0
        elif action_type in ["TYPE", "TAP"]:  # Productive actions
            return base_progress + 0.05
        elif action_type == "WAIT":  # Waiting suggests interim state
            return base_progress - 0.05

        return max(0.0, min(1.0, base_progress))

    def _generate_context_tags(self, action: Dict[str, Any], goal: str) -> List[str]:
        """Generate context tags based on action and goal."""
        tags = []

        goal_lower = goal.lower()
        action_type = action.get("action", "")
        params = action.get("params", {})

        # Goal-based tags
        if "login" in goal_lower or "sign in" in goal_lower:
            tags.append("authentication")
        if "search" in goal_lower:
            tags.append("search")
        if "post" in goal_lower or "publish" in goal_lower:
            tags.append("content_creation")
        if "favorite" in goal_lower or "like" in goal_lower:
            tags.append("engagement")

        # Action-based tags
        if action_type == "TYPE":
            tags.append("form_filling")
            text = params.get("text", "").lower()
            if "@" in text:
                tags.append("email_input")

        if action_type == "SWIPE":
            tags.append("navigation")
            tags.append("scrolling")

        if action_type == "TAP":
            tags.append("selection")

        return list(set(tags))  # Remove duplicates

    def _analyze_screen_state(self, screenshot: Image.Image) -> str:
        """Analyze screenshot to determine screen state."""
        try:
            # Convert to grayscale and analyze
            gray = screenshot.convert('L')
            pixels = np.array(gray)

            # Basic screen state detection based on image properties
            avg_brightness = np.mean(pixels)

            if avg_brightness < 50:  # Very dark
                return "loading"
            elif avg_brightness > 200:  # Very bright
                return "blank"
            else:
                # Could implement more sophisticated detection here
                # For now, default to "app"
                return "app"

        except Exception as e:
            logger.warning(f"Failed to analyze screen state: {e}")
            return "unknown"


class EmbeddingGenerator:
    """Generates embeddings for screenshots and text."""

    def __init__(self, config: MemoryConfig):
        """Initialize embedding generator.

        Args:
            config: Memory configuration
        """
        self.config = config
        self.model = None
        self._init_model()

    def _init_model(self):
        """Initialize embedding model."""
        try:
            # Try to import sentence-transformers
            from sentence_transformers import SentenceTransformer

            model_name = self.config.embedding_model
            self.model = SentenceTransformer(model_name)
            logger.info(f"Loaded embedding model: {model_name}")

        except ImportError:
            logger.warning("sentence-transformers not available, using mock embeddings")
            self.model = None
        except Exception as e:
            logger.warning(f"Failed to load embedding model: {e}, using mock embeddings")
            self.model = None

    def generate_screenshot_embedding(self, screenshot: Image.Image) -> List[float]:
        """Generate embedding for screenshot.

        Args:
            screenshot: PIL Image

        Returns:
            Embedding vector
        """
        if not screenshot:
            return []

        try:
            if self.model is None:
                # Mock embedding based on image hash
                return self._generate_mock_image_embedding(screenshot)

            # For real implementation, you would:
            # 1. Resize/normalize image
            # 2. Extract features with vision model
            # 3. Return embedding vector

            # For now, use mock implementation
            return self._generate_mock_image_embedding(screenshot)

        except Exception as e:
            logger.warning(f"Failed to generate screenshot embedding: {e}")
            return []

    def generate_text_embedding(self, text: str) -> List[float]:
        """Generate embedding for text.

        Args:
            text: Input text

        Returns:
            Embedding vector
        """
        if not text:
            return []

        try:
            if self.model is not None:
                embedding = self.model.encode([text])[0]
                return embedding.tolist()
            else:
                # Mock embedding based on text hash
                return self._generate_mock_text_embedding(text)

        except Exception as e:
            logger.warning(f"Failed to generate text embedding: {e}")
            return []

    def _generate_mock_image_embedding(self, image: Image.Image) -> List[float]:
        """Generate mock embedding for image."""
        # Convert image to bytes and hash
        img_bytes = image.tobytes()
        hash_obj = hashlib.md5(img_bytes)

        # Convert hash to float vector
        hash_bytes = hash_obj.digest()
        embedding = [float(b) / 255.0 for b in hash_bytes]

        # Pad to target dimension
        target_dim = self.config.embedding_dimension
        while len(embedding) < target_dim:
            embedding.extend(embedding[:min(len(embedding), target_dim - len(embedding))])

        return embedding[:target_dim]

    def _generate_mock_text_embedding(self, text: str) -> List[float]:
        """Generate mock embedding for text."""
        # Hash text
        hash_obj = hashlib.md5(text.encode())
        hash_bytes = hash_obj.digest()

        # Convert to float vector
        embedding = [float(b) / 255.0 for b in hash_bytes]

        # Pad to target dimension
        target_dim = self.config.embedding_dimension
        while len(embedding) < target_dim:
            embedding.extend(embedding[:min(len(embedding), target_dim - len(embedding))])

        return embedding[:target_dim]


class CaptureLayer:
    """Main capture layer for memory system."""

    def __init__(self, config: MemoryConfig):
        """Initialize capture layer.

        Args:
            config: Memory configuration
        """
        self.config = config
        self.semantic_extractor = SemanticExtractor(config)
        self.embedding_generator = EmbeddingGenerator(config)
        self.enabled = config.enabled

    async def capture_action(self, action: Dict[str, Any], success: bool,
                           execution_time: float, strategy_level: str,
                           goal: str, step: int, screenshot: Optional[Image.Image] = None,
                           error_message: Optional[str] = None,
                           retry_count: int = 0) -> EnhancedAction:
        """Capture and enhance an action record.

        Args:
            action: Action dictionary from PixelClaw
            success: Whether action succeeded
            execution_time: Time taken to execute action
            strategy_level: Strategy level used (step1v, minicpm, ocr)
            goal: Current task goal
            step: Step number in sequence
            screenshot: Optional screenshot at action time
            error_message: Optional error message if action failed
            retry_count: Number of retries for this action

        Returns:
            EnhancedAction object
        """
        if not self.enabled:
            # Return minimal enhanced action if disabled
            return EnhancedAction(
                step=step,
                action=action,
                success=success,
                execution_time=execution_time,
                strategy_level=strategy_level
            )

        try:
            start_time = time.time()

            # Extract semantic features
            semantic_features = self.semantic_extractor.extract_action_features(
                action, goal, screenshot, step
            )

            # Generate screenshot embedding
            screenshot_embedding = []
            if screenshot and self.config.include_screenshots:
                screenshot_embedding = self.embedding_generator.generate_screenshot_embedding(screenshot)

            # Create device state (placeholder - would be populated by device connector)
            device_state = DeviceState()
            if screenshot:
                device_state.screen_size = screenshot.size

            # Create enhanced action
            enhanced_action = EnhancedAction(
                step=step,
                action=action,
                success=success,
                execution_time=execution_time,
                strategy_level=strategy_level,
                semantic_features=semantic_features,
                screenshot_embedding=screenshot_embedding,
                goal_context=goal,
                device_state=device_state,
                error_message=error_message,
                retry_count=retry_count,
                fallback_used=(strategy_level != "step1v")  # Assuming step1v is primary
            )

            capture_time = time.time() - start_time
            logger.debug(f"Captured enhanced action in {capture_time:.3f}s")

            return enhanced_action

        except Exception as e:
            logger.error(f"Failed to capture action: {e}")
            # Return minimal enhanced action on error
            return EnhancedAction(
                step=step,
                action=action,
                success=success,
                execution_time=execution_time,
                strategy_level=strategy_level,
                goal_context=goal,
                error_message=str(e)
            )

    def is_enabled(self) -> bool:
        """Check if capture layer is enabled."""
        return self.enabled