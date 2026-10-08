"""
Memory System Storage Layer

Provides persistent storage using OpenViking as the backend.
Handles memory records, semantic indexing, and cross-task data persistence.
"""

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .schemas import (
    EnhancedAction,
    MemoryRecord,
    MemoryConfig,
    MemoryQuery,
    MemoryRetrievalResult
)

logger = logging.getLogger(__name__)


class OpenVikingClient:
    """Mock OpenViking client for context database management.

    This is a placeholder implementation that mimics OpenViking's API.
    Replace with actual OpenViking client when available.
    """

    def __init__(self, connection_string: str, embedding_dimension: int = 384):
        """Initialize OpenViking client.

        Args:
            connection_string: Database connection string
            embedding_dimension: Dimension of embedding vectors
        """
        self.connection_string = connection_string
        self.embedding_dimension = embedding_dimension
        self.db_path = self._parse_connection_string()
        self._init_database()

    def _parse_connection_string(self) -> str:
        """Parse connection string to get database path."""
        if self.connection_string.startswith("sqlite:///"):
            return self.connection_string[10:]  # Remove sqlite:/// prefix
        else:
            # For PostgreSQL or other backends, would parse accordingly
            return ":memory:"  # Fallback to in-memory

    def _init_database(self):
        """Initialize database schema."""
        try:
            # Ensure directory exists
            if self.db_path != ":memory:":
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Create memory records table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS memory_records (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        goal TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        total_steps INTEGER DEFAULT 0,
                        successful_steps INTEGER DEFAULT 0,
                        total_execution_time REAL DEFAULT 0.0,
                        strategy_distribution TEXT DEFAULT '{}',
                        tags TEXT DEFAULT '[]',
                        difficulty_score REAL DEFAULT 0.0,
                        repeatability_score REAL DEFAULT 0.0,
                        metadata TEXT DEFAULT '{}',
                        semantic_embedding TEXT DEFAULT '[]',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Create actions table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS actions (
                        id TEXT PRIMARY KEY,
                        memory_id TEXT NOT NULL,
                        step INTEGER NOT NULL,
                        action_data TEXT NOT NULL,
                        success BOOLEAN NOT NULL,
                        execution_time REAL NOT NULL,
                        strategy_level TEXT NOT NULL,
                        semantic_features TEXT DEFAULT '{}',
                        screenshot_embedding TEXT DEFAULT '[]',
                        goal_context TEXT DEFAULT '',
                        timestamp TEXT NOT NULL,
                        device_state TEXT DEFAULT '{}',
                        error_message TEXT,
                        retry_count INTEGER DEFAULT 0,
                        fallback_used BOOLEAN DEFAULT FALSE,
                        FOREIGN KEY (memory_id) REFERENCES memory_records(id)
                    )
                """)

                # Create embeddings table for vector search
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS embeddings (
                        id TEXT PRIMARY KEY,
                        memory_id TEXT NOT NULL,
                        embedding_type TEXT NOT NULL,  -- 'goal', 'action', 'screenshot'
                        embedding_data TEXT NOT NULL,   -- JSON array of floats
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (memory_id) REFERENCES memory_records(id)
                    )
                """)

                # Create indices for performance
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_session ON memory_records(session_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_goal ON memory_records(goal)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_outcome ON memory_records(outcome)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_created ON memory_records(created_at)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_actions_memory ON actions(memory_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_actions_step ON actions(step)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_memory ON embeddings(memory_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_type ON embeddings(embedding_type)")

                conn.commit()
                logger.info(f"Initialized OpenViking database at {self.db_path}")

        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    async def store_memory(self, memory: MemoryRecord) -> bool:
        """Store a memory record.

        Args:
            memory: Memory record to store

        Returns:
            True if stored successfully
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Store memory record
                cursor.execute("""
                    INSERT OR REPLACE INTO memory_records
                    (id, session_id, goal, outcome, total_steps, successful_steps,
                     total_execution_time, strategy_distribution, tags, difficulty_score,
                     repeatability_score, metadata, semantic_embedding, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    memory.id,
                    memory.session_id,
                    memory.goal,
                    memory.outcome,
                    memory.total_steps,
                    memory.successful_steps,
                    memory.total_execution_time,
                    json.dumps(memory.strategy_distribution),
                    json.dumps(memory.tags),
                    memory.difficulty_score,
                    memory.repeatability_score,
                    json.dumps(memory.metadata),
                    json.dumps(memory.semantic_embedding),
                    memory.created_at.isoformat(),
                    memory.updated_at.isoformat()
                ))

                # Store associated actions
                for action in memory.action_sequence:
                    await self._store_action(cursor, memory.id, action)

                # Store embeddings for vector search
                if memory.semantic_embedding:
                    cursor.execute("""
                        INSERT OR REPLACE INTO embeddings
                        (id, memory_id, embedding_type, embedding_data)
                        VALUES (?, ?, ?, ?)
                    """, (
                        f"{memory.id}_goal",
                        memory.id,
                        "goal",
                        json.dumps(memory.semantic_embedding)
                    ))

                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Failed to store memory {memory.id}: {e}")
            return False

    async def _store_action(self, cursor: sqlite3.Cursor, memory_id: str, action: EnhancedAction):
        """Store an action record."""
        action_id = f"{memory_id}_step_{action.step}"

        cursor.execute("""
            INSERT OR REPLACE INTO actions
            (id, memory_id, step, action_data, success, execution_time, strategy_level,
             semantic_features, screenshot_embedding, goal_context, timestamp,
             device_state, error_message, retry_count, fallback_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            action_id,
            memory_id,
            action.step,
            json.dumps(action.action),
            action.success,
            action.execution_time,
            action.strategy_level,
            json.dumps(action.semantic_features.__dict__),
            json.dumps(action.screenshot_embedding),
            action.goal_context,
            action.timestamp,
            json.dumps(action.device_state.__dict__),
            action.error_message,
            action.retry_count,
            action.fallback_used
        ))

        # Store screenshot embedding for vector search
        if action.screenshot_embedding:
            cursor.execute("""
                INSERT OR REPLACE INTO embeddings
                (id, memory_id, embedding_type, embedding_data)
                VALUES (?, ?, ?, ?)
            """, (
                f"{action_id}_screenshot",
                memory_id,
                "screenshot",
                json.dumps(action.screenshot_embedding)
            ))

    async def retrieve_memories(self, query: MemoryQuery) -> MemoryRetrievalResult:
        """Retrieve memories based on query.

        Args:
            query: Memory query parameters

        Returns:
            MemoryRetrievalResult with matching memories
        """
        start_time = time.time()

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Build SQL query based on filters
                where_conditions = []
                params = []

                # Goal similarity (simple text matching for now)
                if query.goal:
                    where_conditions.append("goal LIKE ?")
                    params.append(f"%{query.goal}%")

                # Success rate filter
                if query.min_success_rate > 0:
                    where_conditions.append("(CASE WHEN total_steps > 0 THEN successful_steps * 1.0 / total_steps ELSE 0 END) >= ?")
                    params.append(query.min_success_rate)

                # Age filter
                if query.max_age_days:
                    cutoff_date = datetime.now() - timedelta(days=query.max_age_days)
                    where_conditions.append("created_at >= ?")
                    params.append(cutoff_date.isoformat())

                # Tags filter
                if query.required_tags:
                    for tag in query.required_tags:
                        where_conditions.append("tags LIKE ?")
                        params.append(f'%"{tag}"%')

                # Build final query
                where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
                sql_query = f"""
                    SELECT * FROM memory_records
                    WHERE {where_clause}
                    ORDER BY created_at DESC
                    LIMIT ?
                """
                params.append(query.max_results)

                # Execute query
                cursor.execute(sql_query, params)
                rows = cursor.fetchall()

                # Convert to MemoryRecord objects
                memories = []
                similarity_scores = []

                for row in rows:
                    memory = self._row_to_memory_record(row)
                    if memory:
                        memories.append(memory)
                        # Mock similarity score based on goal matching
                        similarity = self._calculate_similarity(memory, query)
                        similarity_scores.append(similarity)

                        # Load actions for this memory
                        await self._load_actions_for_memory(cursor, memory)

                # Sort by similarity
                sorted_pairs = sorted(zip(memories, similarity_scores),
                                    key=lambda x: x[1], reverse=True)
                memories = [pair[0] for pair in sorted_pairs]
                similarity_scores = [pair[1] for pair in sorted_pairs]

                query_time = (time.time() - start_time) * 1000  # Convert to milliseconds

                return MemoryRetrievalResult(
                    memories=memories,
                    similarity_scores=similarity_scores,
                    retrieval_strategy="sql_with_text_matching",
                    total_found=len(memories),
                    query_time_ms=query_time
                )

        except Exception as e:
            logger.error(f"Failed to retrieve memories: {e}")
            return MemoryRetrievalResult(
                query_time_ms=(time.time() - start_time) * 1000
            )

    def _row_to_memory_record(self, row: Tuple) -> Optional[MemoryRecord]:
        """Convert database row to MemoryRecord."""
        try:
            return MemoryRecord(
                id=row[0],
                session_id=row[1],
                goal=row[2],
                outcome=row[3],
                total_steps=row[4],
                successful_steps=row[5],
                total_execution_time=row[6],
                strategy_distribution=json.loads(row[7]),
                tags=json.loads(row[8]),
                difficulty_score=row[9],
                repeatability_score=row[10],
                metadata=json.loads(row[11]),
                semantic_embedding=json.loads(row[12]),
                created_at=datetime.fromisoformat(row[13]),
                updated_at=datetime.fromisoformat(row[14])
            )
        except Exception as e:
            logger.warning(f"Failed to convert row to MemoryRecord: {e}")
            return None

    async def _load_actions_for_memory(self, cursor: sqlite3.Cursor, memory: MemoryRecord):
        """Load actions for a memory record."""
        cursor.execute(
            "SELECT * FROM actions WHERE memory_id = ? ORDER BY step",
            (memory.id,)
        )
        action_rows = cursor.fetchall()

        memory.action_sequence = []
        for row in action_rows:
            try:
                # Reconstruct EnhancedAction (simplified)
                action = EnhancedAction(
                    step=row[2],
                    action=json.loads(row[3]),
                    success=bool(row[4]),
                    execution_time=row[5],
                    strategy_level=row[6],
                    goal_context=row[9],
                    timestamp=row[10],
                    error_message=row[12],
                    retry_count=row[13],
                    fallback_used=bool(row[14])
                )
                memory.action_sequence.append(action)
            except Exception as e:
                logger.warning(f"Failed to load action: {e}")

    def _calculate_similarity(self, memory: MemoryRecord, query: MemoryQuery) -> float:
        """Calculate similarity score between memory and query."""
        # Simple text-based similarity for now
        if not query.goal:
            return 0.5

        goal_words = set(query.goal.lower().split())
        memory_words = set(memory.goal.lower().split())

        # Jaccard similarity
        intersection = goal_words.intersection(memory_words)
        union = goal_words.union(memory_words)

        if not union:
            return 0.0

        jaccard_score = len(intersection) / len(union)

        # Adjust for success rate
        success_rate = memory.get_success_rate()
        success_weight = query.success_weight
        temporal_weight = query.temporal_weight

        # Temporal decay (more recent memories get higher scores)
        days_old = (datetime.now() - memory.created_at).days
        temporal_score = max(0, 1 - (days_old / 30))  # Decay over 30 days

        # Combined score
        final_score = (jaccard_score +
                      success_rate * success_weight +
                      temporal_score * temporal_weight) / 3

        return min(1.0, max(0.0, final_score))

    async def cleanup_old_memories(self, days: int = 30) -> int:
        """Clean up old memories.

        Args:
            days: Remove memories older than this many days

        Returns:
            Number of memories removed
        """
        try:
            cutoff_date = datetime.now() - timedelta(days=days)

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Count memories to be removed
                cursor.execute(
                    "SELECT COUNT(*) FROM memory_records WHERE created_at < ?",
                    (cutoff_date.isoformat(),)
                )
                count = cursor.fetchone()[0]

                if count > 0:
                    # Remove old memories and associated data
                    cursor.execute(
                        "DELETE FROM embeddings WHERE memory_id IN "
                        "(SELECT id FROM memory_records WHERE created_at < ?)",
                        (cutoff_date.isoformat(),)
                    )

                    cursor.execute(
                        "DELETE FROM actions WHERE memory_id IN "
                        "(SELECT id FROM memory_records WHERE created_at < ?)",
                        (cutoff_date.isoformat(),)
                    )

                    cursor.execute(
                        "DELETE FROM memory_records WHERE created_at < ?",
                        (cutoff_date.isoformat(),)
                    )

                    conn.commit()
                    logger.info(f"Cleaned up {count} old memories")

                return count

        except Exception as e:
            logger.error(f"Failed to cleanup old memories: {e}")
            return 0


class StorageLayer:
    """Storage layer for memory system."""

    def __init__(self, config: MemoryConfig):
        """Initialize storage layer.

        Args:
            config: Memory configuration
        """
        self.config = config
        self.enabled = config.enabled
        self.client: Optional[OpenVikingClient] = None

        if self.enabled:
            self._init_client()

    def _init_client(self):
        """Initialize storage client."""
        try:
            if self.config.storage_backend == "sqlite":
                self.client = OpenVikingClient(
                    self.config.connection_string,
                    self.config.embedding_dimension
                )
                logger.info("Initialized SQLite storage backend")
            else:
                logger.warning(f"Unsupported storage backend: {self.config.storage_backend}")
                self.enabled = False

        except Exception as e:
            logger.error(f"Failed to initialize storage client: {e}")
            self.enabled = False

    async def store_memory(self, memory: MemoryRecord) -> bool:
        """Store a memory record.

        Args:
            memory: Memory record to store

        Returns:
            True if stored successfully
        """
        if not self.enabled or not self.client:
            return False

        try:
            if self.config.async_storage:
                # Store asynchronously (non-blocking)
                asyncio.create_task(self.client.store_memory(memory))
                return True
            else:
                # Store synchronously
                return await self.client.store_memory(memory)

        except Exception as e:
            logger.error(f"Failed to store memory: {e}")
            return False

    async def retrieve_memories(self, query: MemoryQuery) -> MemoryRetrievalResult:
        """Retrieve memories based on query.

        Args:
            query: Memory query parameters

        Returns:
            MemoryRetrievalResult with matching memories
        """
        if not self.enabled or not self.client:
            return MemoryRetrievalResult()

        try:
            return await self.client.retrieve_memories(query)

        except Exception as e:
            logger.error(f"Failed to retrieve memories: {e}")
            return MemoryRetrievalResult()

    async def cleanup(self) -> int:
        """Cleanup old memories.

        Returns:
            Number of memories cleaned up
        """
        if not self.enabled or not self.client:
            return 0

        try:
            return await self.client.cleanup_old_memories(self.config.cleanup_days)

        except Exception as e:
            logger.error(f"Failed to cleanup memories: {e}")
            return 0

    def is_enabled(self) -> bool:
        """Check if storage layer is enabled."""
        return self.enabled and self.client is not None