"""
Memory System Data Structures

Defines the core data structures for the three-layer memory architecture:
- EnhancedAction: Enhanced action records with semantic features
- MemoryRecord: Persistent memory records for cross-task learning
- MemoryQuery: Query interface for retrieval
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Union


@dataclass
class SemanticFeatures:
    """Semantic features extracted from actions and screenshots."""
    action_intent: str = ""  # navigation, input, selection, etc.
    ui_elements: List[str] = field(default_factory=list)  # button:login, input:username
    goal_progress: float = 0.0  # estimated progress towards goal (0-1)
    context_tags: List[str] = field(default_factory=list)  # authentication, form_filling, etc.
    screen_state: str = ""  # home, app, loading, error, etc.
    interaction_type: str = ""  # tap, swipe, input, wait
    confidence_score: float = 0.0  # action confidence from VLM


@dataclass
class DeviceState:
    """Device state information at the time of action."""
    screen_size: tuple[int, int] = (1080, 2400)
    orientation: str = "portrait"
    app_package: str = ""
    app_activity: str = ""
    battery_level: Optional[int] = None
    network_connected: bool = True


@dataclass
class EnhancedAction:
    """Enhanced action record with semantic features and context."""
    # Original fields from PixelClaw
    step: int
    action: Dict[str, Any]
    success: bool
    execution_time: float
    strategy_level: str

    # New semantic fields
    semantic_features: SemanticFeatures = field(default_factory=SemanticFeatures)
    screenshot_embedding: List[float] = field(default_factory=list)
    goal_context: str = ""
    timestamp: str = ""
    device_state: DeviceState = field(default_factory=DeviceState)

    # Additional metadata
    error_message: Optional[str] = None
    retry_count: int = 0
    fallback_used: bool = False

    def __post_init__(self):
        """Auto-generate timestamp if not provided."""
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


@dataclass
class MemoryRecord:
    """Persistent memory record for cross-task learning."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    goal: str = ""
    action_sequence: List[EnhancedAction] = field(default_factory=list)
    outcome: str = ""  # success, failure, partial
    semantic_embedding: List[float] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    # Success metrics
    total_steps: int = 0
    successful_steps: int = 0
    total_execution_time: float = 0.0
    strategy_distribution: Dict[str, int] = field(default_factory=dict)

    # Learning attributes
    tags: List[str] = field(default_factory=list)
    difficulty_score: float = 0.0  # 0=easy, 1=very difficult
    repeatability_score: float = 0.0  # 0=one-time, 1=highly repeatable

    def get_success_rate(self) -> float:
        """Calculate success rate for this memory."""
        if self.total_steps == 0:
            return 0.0
        return self.successful_steps / self.total_steps

    def add_action(self, action: EnhancedAction):
        """Add an action to the sequence."""
        self.action_sequence.append(action)
        self.total_steps += 1
        if action.success:
            self.successful_steps += 1
        self.total_execution_time += action.execution_time

        # Update strategy distribution
        strategy = action.strategy_level
        self.strategy_distribution[strategy] = self.strategy_distribution.get(strategy, 0) + 1

        self.updated_at = datetime.now()


@dataclass
class MemoryQuery:
    """Query interface for memory retrieval."""
    goal: str = ""
    current_screenshot_embedding: List[float] = field(default_factory=list)
    action_history: List[Dict[str, Any]] = field(default_factory=list)
    semantic_tags: List[str] = field(default_factory=list)

    # Retrieval parameters
    max_results: int = 10
    similarity_threshold: float = 0.7
    temporal_weight: float = 0.3  # weight for recent memories
    success_weight: float = 0.4   # weight for successful memories

    # Filters
    min_success_rate: float = 0.5
    max_age_days: Optional[int] = None
    required_tags: List[str] = field(default_factory=list)
    excluded_tags: List[str] = field(default_factory=list)


@dataclass
class MemoryRetrievalResult:
    """Result from memory retrieval."""
    memories: List[MemoryRecord] = field(default_factory=list)
    similarity_scores: List[float] = field(default_factory=list)
    retrieval_strategy: str = ""  # semantic, pattern_matching, temporal
    total_found: int = 0
    query_time_ms: float = 0.0

    # Enhanced context for prompt building
    relevant_actions: List[EnhancedAction] = field(default_factory=list)
    success_patterns: List[str] = field(default_factory=list)
    failure_warnings: List[str] = field(default_factory=list)
    suggested_next_actions: List[str] = field(default_factory=list)


@dataclass
class MemoryConfig:
    """Configuration for memory system."""
    enabled: bool = True

    # Storage configuration
    storage_backend: str = "sqlite"  # sqlite, postgresql, mock
    connection_string: str = "sqlite:///./data/pixelclaw_memory.db"

    # Embedding configuration
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dimension: int = 384

    # QMD configuration
    qmd_index_path: str = "./data/qmd_index"
    qmd_search_k: int = 10

    # Capture configuration
    include_screenshots: bool = True
    semantic_extraction: bool = True
    async_storage: bool = True

    # Retrieval configuration
    max_context_actions: int = 10
    similarity_threshold: float = 0.7
    temporal_weight: float = 0.3

    # Performance tuning
    max_embedding_batch_size: int = 32
    cache_size: int = 1000
    cleanup_days: int = 30  # cleanup old memories after N days

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "enabled": self.enabled,
            "storage_backend": self.storage_backend,
            "connection_string": self.connection_string,
            "embedding_model": self.embedding_model,
            "embedding_dimension": self.embedding_dimension,
            "qmd_index_path": self.qmd_index_path,
            "qmd_search_k": self.qmd_search_k,
            "include_screenshots": self.include_screenshots,
            "semantic_extraction": self.semantic_extraction,
            "async_storage": self.async_storage,
            "max_context_actions": self.max_context_actions,
            "similarity_threshold": self.similarity_threshold,
            "temporal_weight": self.temporal_weight,
            "max_embedding_batch_size": self.max_embedding_batch_size,
            "cache_size": self.cache_size,
            "cleanup_days": self.cleanup_days,
        }

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> "MemoryConfig":
        """Create from dictionary."""
        return cls(**{k: v for k, v in config_dict.items() if hasattr(cls, k)})