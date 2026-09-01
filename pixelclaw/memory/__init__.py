"""
Memory System for PixelClaw

A three-layer memory architecture (capture -> store -> recall) designed to address
the limitations of the original memos-based system.

Key improvements:
- Persistent cross-task learning
- Semantic search and retrieval
- Structured memory organization
- Intelligent context enhancement

Usage:
    from pixelclaw.memory import MemoryManager

    memory = MemoryManager(config)

    # Get enhanced context for decision making
    context = await memory.get_enhanced_context(goal, action_history, screenshot)

    # Capture action for learning
    await memory.capture_action(action, result, context_info)
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from PIL import Image

from .capture import CaptureLayer
from .config import load_memory_config, is_memory_enabled
from .retrieval import RetrievalLayer
from .schemas import (
    EnhancedAction,
    MemoryConfig,
    MemoryRecord,
    MemoryQuery,
    MemoryRetrievalResult
)
from .storage import StorageLayer

logger = logging.getLogger(__name__)


class MemoryManager:
    """Main memory manager coordinating the three-layer architecture.

    Provides a unified interface for the VisionAgent to interact with the memory system.
    Handles the flow: capture -> store -> recall with proper error handling and fallback.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize memory manager.

        Args:
            config: Optional configuration dictionary
        """
        self.config = load_memory_config(config)
        self.enabled = self.config.enabled

        # Initialize layers
        self.capture = CaptureLayer(self.config) if self.enabled else None
        self.storage = StorageLayer(self.config) if self.enabled else None
        self.retrieval = RetrievalLayer(self.config) if self.enabled else None

        # Session management
        self.current_session_id = self._generate_session_id()
        self.current_memory: Optional[MemoryRecord] = None

        # Performance monitoring
        self.stats = {
            'captures': 0,
            'retrievals': 0,
            'storage_ops': 0,
            'avg_capture_time': 0.0,
            'avg_retrieval_time': 0.0,
            'errors': 0
        }

        if self.enabled:
            logger.info(f"Memory system initialized with session {self.current_session_id}")
        else:
            logger.info("Memory system disabled")

    async def get_enhanced_context(self, goal: str, action_history: List[Dict[str, Any]],
                                 current_screenshot: Optional[Image.Image] = None) -> Dict[str, Any]:
        """Get enhanced context for prompt building.

        This is the main interface for the VisionAgent to retrieve relevant memories
        and build an enhanced context for decision making.

        Args:
            goal: Current task goal
            action_history: Current action history from VisionAgent
            current_screenshot: Optional current screenshot

        Returns:
            Enhanced context dictionary with:
            - action_history: Enhanced action history
            - relevant_memories: Similar past experiences
            - success_patterns: Patterns from successful attempts
            - failure_warnings: Warnings from failed attempts
            - suggested_actions: AI-suggested next actions
        """
        start_time = time.time()

        try:
            if not self.enabled:
                # Return original action history if memory system disabled
                return {
                    "action_history": action_history[-5:],  # Last 5 actions (original behavior)
                    "relevant_memories": [],
                    "success_patterns": [],
                    "failure_warnings": [],
                    "suggested_actions": []
                }

            # Generate screenshot embedding if available
            screenshot_embedding = []
            if current_screenshot and self.capture:
                try:
                    screenshot_embedding = self.capture.embedding_generator.generate_screenshot_embedding(current_screenshot)
                except Exception as e:
                    logger.warning(f"Failed to generate screenshot embedding: {e}")

            # Retrieve memories from storage
            memories = await self._get_relevant_memories_from_storage(goal, action_history, screenshot_embedding)

            # Use retrieval layer for intelligent context enhancement
            if self.retrieval and memories:
                retrieval_result = await self.retrieval.retrieve_enhanced_context(
                    goal, action_history, screenshot_embedding, memories
                )

                # Build enhanced context
                enhanced_context = self._build_enhanced_context(action_history, retrieval_result)

                self.stats['retrievals'] += 1
                retrieval_time = time.time() - start_time
                self.stats['avg_retrieval_time'] = (
                    (self.stats['avg_retrieval_time'] * (self.stats['retrievals'] - 1) + retrieval_time) /
                    self.stats['retrievals']
                )

                logger.debug(f"Enhanced context retrieved in {retrieval_time:.3f}s with {len(retrieval_result.memories)} memories")

                return enhanced_context
            else:
                # Fallback to original behavior
                return {
                    "action_history": action_history[-5:],
                    "relevant_memories": [],
                    "success_patterns": [],
                    "failure_warnings": [],
                    "suggested_actions": []
                }

        except Exception as e:
            logger.error(f"Failed to get enhanced context: {e}")
            self.stats['errors'] += 1

            # Return safe fallback
            return {
                "action_history": action_history[-5:],  # Original behavior
                "relevant_memories": [],
                "success_patterns": [],
                "failure_warnings": [],
                "suggested_actions": []
            }

    async def capture_action(self, action: Dict[str, Any], result: Dict[str, Any],
                           context: Dict[str, Any]) -> bool:
        """Capture and store an action for learning.

        This is called after each action execution to build up the memory database.

        Args:
            action: Action dictionary from PixelClaw
            result: Execution result with success/failure info
            context: Additional context (goal, screenshot, etc.)

        Returns:
            True if captured successfully
        """
        start_time = time.time()

        try:
            if not self.enabled or not self.capture:
                return True  # Silently succeed if disabled

            # Extract information from parameters
            goal = context.get("goal", "")
            step = context.get("step", 0)
            screenshot = context.get("screenshot")
            strategy_level = context.get("strategy_level", "unknown")

            # Capture enhanced action
            enhanced_action = await self.capture.capture_action(
                action=action,
                success=result.get("success", False),
                execution_time=result.get("execution_time", 0.0),
                strategy_level=strategy_level,
                goal=goal,
                step=step,
                screenshot=screenshot,
                error_message=result.get("error_message"),
                retry_count=result.get("retry_count", 0)
            )

            # Add to current memory record
            if not self.current_memory or self.current_memory.goal != goal:
                # Start new memory record for this goal
                await self._start_new_memory_record(goal)

            if self.current_memory:
                self.current_memory.add_action(enhanced_action)

            self.stats['captures'] += 1
            capture_time = time.time() - start_time
            self.stats['avg_capture_time'] = (
                (self.stats['avg_capture_time'] * (self.stats['captures'] - 1) + capture_time) /
                self.stats['captures']
            )

            logger.debug(f"Captured action {step} in {capture_time:.3f}s")

            return True

        except Exception as e:
            logger.error(f"Failed to capture action: {e}")
            self.stats['errors'] += 1
            return False

    async def finalize_memory_record(self, outcome: str = "unknown") -> bool:
        """Finalize and store the current memory record.

        Called when a task completes (successfully or not).

        Args:
            outcome: Task outcome ("success", "failure", "partial")

        Returns:
            True if stored successfully
        """
        try:
            if not self.current_memory or not self.storage:
                return True

            # Set final outcome and metadata
            self.current_memory.outcome = outcome
            self.current_memory.updated_at = datetime.now()

            # Calculate difficulty and repeatability scores
            self.current_memory.difficulty_score = self._calculate_difficulty_score()
            self.current_memory.repeatability_score = self._calculate_repeatability_score()

            # Generate semantic embedding for the entire memory
            if self.capture:
                goal_embedding = self.capture.embedding_generator.generate_text_embedding(
                    f"{self.current_memory.goal} {outcome} {' '.join(self.current_memory.tags)}"
                )
                self.current_memory.semantic_embedding = goal_embedding

            # Store to persistent storage
            success = await self.storage.store_memory(self.current_memory)

            if success:
                self.stats['storage_ops'] += 1
                logger.info(f"Stored memory record {self.current_memory.id[:8]}... with {len(self.current_memory.action_sequence)} actions")
            else:
                logger.error(f"Failed to store memory record {self.current_memory.id}")

            # Reset current memory
            self.current_memory = None

            return success

        except Exception as e:
            logger.error(f"Failed to finalize memory record: {e}")
            self.stats['errors'] += 1
            return False

    async def _start_new_memory_record(self, goal: str):
        """Start a new memory record for a task."""
        try:
            # Finalize previous memory if exists
            if self.current_memory:
                await self.finalize_memory_record("incomplete")

            # Create new memory record
            self.current_memory = MemoryRecord(
                session_id=self.current_session_id,
                goal=goal
            )

            # Extract tags from goal
            self.current_memory.tags = self._extract_goal_tags(goal)

            logger.debug(f"Started new memory record for goal: {goal}")

        except Exception as e:
            logger.error(f"Failed to start new memory record: {e}")

    async def _get_relevant_memories_from_storage(self, goal: str, action_history: List[Dict[str, Any]],
                                                screenshot_embedding: List[float]) -> List[MemoryRecord]:
        """Retrieve relevant memories from storage."""
        try:
            if not self.storage:
                return []

            # Build query
            query = MemoryQuery(
                goal=goal,
                current_screenshot_embedding=screenshot_embedding,
                action_history=action_history,
                max_results=20,  # Get more for better filtering
                similarity_threshold=self.config.similarity_threshold,
                temporal_weight=self.config.temporal_weight,
                min_success_rate=0.3  # Include some failures for learning
            )

            # Retrieve from storage
            result = await self.storage.retrieve_memories(query)
            return result.memories

        except Exception as e:
            logger.error(f"Failed to retrieve memories from storage: {e}")
            return []

    def _build_enhanced_context(self, action_history: List[Dict[str, Any]],
                               retrieval_result: MemoryRetrievalResult) -> Dict[str, Any]:
        """Build enhanced context from retrieval results."""
        # Start with original action history
        enhanced_history = action_history.copy()

        # Add relevant actions from memory
        for action in retrieval_result.relevant_actions:
            enhanced_history.append({
                "step": f"memory_{action.step}",
                "action": action.action,
                "success": action.success,
                "execution_time": action.execution_time,
                "strategy_level": action.strategy_level,
                "source": "memory",
                "confidence": action.semantic_features.confidence_score if action.semantic_features else 0.0
            })

        # Limit to configured maximum
        max_actions = self.config.max_context_actions
        enhanced_history = enhanced_history[-max_actions:]

        return {
            "action_history": enhanced_history,
            "relevant_memories": [
                {
                    "id": memory.id[:8] + "...",
                    "goal": memory.goal,
                    "outcome": memory.outcome,
                    "success_rate": memory.get_success_rate(),
                    "steps": len(memory.action_sequence),
                    "created_days_ago": (datetime.now() - memory.created_at).days,
                    "tags": memory.tags
                }
                for memory in retrieval_result.memories
            ],
            "success_patterns": retrieval_result.success_patterns,
            "failure_warnings": retrieval_result.failure_warnings,
            "suggested_actions": retrieval_result.suggested_next_actions
        }

    def _extract_goal_tags(self, goal: str) -> List[str]:
        """Extract tags from goal description."""
        tags = []
        goal_lower = goal.lower()

        # Common app tags
        if "wechat" in goal_lower or "微信" in goal_lower:
            tags.append("wechat")
        if "xiaohongshu" in goal_lower or "小红书" in goal_lower:
            tags.append("xiaohongshu")
        if "chrome" in goal_lower:
            tags.append("chrome")

        # Action tags
        if "login" in goal_lower or "登录" in goal_lower:
            tags.append("login")
        if "search" in goal_lower or "搜索" in goal_lower:
            tags.append("search")
        if "post" in goal_lower or "发布" in goal_lower:
            tags.append("post")
        if "like" in goal_lower or "favorite" in goal_lower or "收藏" in goal_lower:
            tags.append("engagement")

        return tags

    def _calculate_difficulty_score(self) -> float:
        """Calculate difficulty score based on memory record."""
        if not self.current_memory:
            return 0.0

        # Factors that increase difficulty
        total_steps = self.current_memory.total_steps
        success_rate = self.current_memory.get_success_rate()
        avg_execution_time = (
            self.current_memory.total_execution_time / max(1, total_steps)
        )
        fallback_usage = sum(
            1 for action in self.current_memory.action_sequence
            if action.fallback_used
        ) / max(1, total_steps)

        # Normalize and combine factors
        step_factor = min(1.0, total_steps / 20.0)  # More steps = harder
        failure_factor = 1.0 - success_rate  # More failures = harder
        time_factor = min(1.0, avg_execution_time / 10.0)  # Slower = harder
        fallback_factor = fallback_usage  # More fallbacks = harder

        difficulty = (step_factor + failure_factor + time_factor + fallback_factor) / 4.0
        return min(1.0, max(0.0, difficulty))

    def _calculate_repeatability_score(self) -> float:
        """Calculate repeatability score based on memory record."""
        if not self.current_memory:
            return 0.0

        # Factors that increase repeatability
        success_rate = self.current_memory.get_success_rate()
        action_consistency = self._calculate_action_consistency()
        goal_specificity = len(self.current_memory.goal.split()) / 10.0  # Specific goals more repeatable

        repeatability = (success_rate + action_consistency + goal_specificity) / 3.0
        return min(1.0, max(0.0, repeatability))

    def _calculate_action_consistency(self) -> float:
        """Calculate consistency of actions in the sequence."""
        if not self.current_memory or len(self.current_memory.action_sequence) < 2:
            return 0.0

        # Check for consistent action types
        action_types = [action.action.get("action", "") for action in self.current_memory.action_sequence]
        unique_types = len(set(action_types))
        total_actions = len(action_types)

        # Fewer unique action types relative to total = more consistent
        consistency = 1.0 - (unique_types / total_actions)
        return consistency

    def _generate_session_id(self) -> str:
        """Generate a new session ID."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_part = str(uuid.uuid4())[:8]
        return f"session_{timestamp}_{random_part}"

    def get_stats(self) -> Dict[str, Any]:
        """Get performance statistics."""
        return self.stats.copy()

    def is_enabled(self) -> bool:
        """Check if memory system is enabled."""
        return self.enabled

    async def cleanup(self) -> int:
        """Cleanup old memories and return count."""
        if self.storage:
            return await self.storage.cleanup()
        return 0


# Convenience functions for easy imports
def create_memory_manager(config: Optional[Dict[str, Any]] = None) -> MemoryManager:
    """Create a memory manager instance."""
    return MemoryManager(config)


# Export main classes and functions
__all__ = [
    'MemoryManager',
    'MemoryConfig',
    'MemoryRecord',
    'EnhancedAction',
    'create_memory_manager',
    'load_memory_config',
    'is_memory_enabled'
]