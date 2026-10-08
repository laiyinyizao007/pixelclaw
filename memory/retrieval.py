"""
Memory System Retrieval Layer

Implements intelligent context retrieval using QMD-style multi-modal search.
Combines BM25 text search, vector similarity, and LLM reranking for optimal results.
"""

import asyncio
import json
import logging
import math
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from .schemas import (
    EnhancedAction,
    MemoryConfig,
    MemoryQuery,
    MemoryRecord,
    MemoryRetrievalResult
)

logger = logging.getLogger(__name__)


class BM25Scorer:
    """BM25 text similarity scoring for memory retrieval."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        """Initialize BM25 scorer.

        Args:
            k1: Term frequency saturation parameter
            b: Field length normalization parameter
        """
        self.k1 = k1
        self.b = b
        self.corpus_stats = {}
        self.doc_frequencies = defaultdict(int)
        self.total_docs = 0
        self.avg_doc_length = 0.0

    def build_index(self, memories: List[MemoryRecord]):
        """Build BM25 index from memory records.

        Args:
            memories: List of memory records to index
        """
        documents = []
        doc_lengths = []

        for memory in memories:
            # Combine goal, tags, and outcome for text index
            text = f"{memory.goal} {' '.join(memory.tags)} {memory.outcome}"
            doc_terms = self._tokenize(text)
            documents.append(doc_terms)
            doc_lengths.append(len(doc_terms))

            # Update document frequencies
            unique_terms = set(doc_terms)
            for term in unique_terms:
                self.doc_frequencies[term] += 1

        self.total_docs = len(documents)
        self.avg_doc_length = sum(doc_lengths) / max(1, len(doc_lengths))

        # Store documents for scoring
        self.corpus_stats = {
            'documents': documents,
            'doc_lengths': doc_lengths
        }

    def score(self, query: str, doc_index: int) -> float:
        """Calculate BM25 score for query against document.

        Args:
            query: Query string
            doc_index: Index of document to score

        Returns:
            BM25 score
        """
        if 'documents' not in self.corpus_stats:
            return 0.0

        query_terms = self._tokenize(query)
        doc_terms = self.corpus_stats['documents'][doc_index]
        doc_length = self.corpus_stats['doc_lengths'][doc_index]

        score = 0.0
        term_counts = Counter(doc_terms)

        for term in query_terms:
            if term in term_counts:
                # Term frequency component
                tf = term_counts[term]
                tf_component = (tf * (self.k1 + 1)) / (
                    tf + self.k1 * (1 - self.b + self.b * (doc_length / self.avg_doc_length))
                )

                # Inverse document frequency component
                doc_freq = self.doc_frequencies.get(term, 0)
                if doc_freq > 0:
                    idf = math.log((self.total_docs - doc_freq + 0.5) / (doc_freq + 0.5))
                    score += idf * tf_component

        return score

    def _tokenize(self, text: str) -> List[str]:
        """Tokenize text into terms.

        Args:
            text: Text to tokenize

        Returns:
            List of terms
        """
        # Simple tokenization - could be improved with proper NLP
        text = text.lower()
        terms = re.findall(r'\b\w+\b', text)
        return terms


class VectorSimilarityScorer:
    """Vector similarity scoring for semantic search."""

    def __init__(self, dimension: int = 384):
        """Initialize vector scorer.

        Args:
            dimension: Embedding dimension
        """
        self.dimension = dimension

    def cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between vectors.

        Args:
            vec1: First vector
            vec2: Second vector

        Returns:
            Cosine similarity score (0-1)
        """
        if not vec1 or not vec2 or len(vec1) != len(vec2):
            return 0.0

        try:
            # Calculate dot product
            dot_product = sum(a * b for a, b in zip(vec1, vec2))

            # Calculate magnitudes
            magnitude1 = math.sqrt(sum(a * a for a in vec1))
            magnitude2 = math.sqrt(sum(a * a for a in vec2))

            if magnitude1 == 0.0 or magnitude2 == 0.0:
                return 0.0

            # Cosine similarity
            similarity = dot_product / (magnitude1 * magnitude2)
            return max(0.0, min(1.0, similarity))  # Clamp to [0, 1]

        except Exception as e:
            logger.warning(f"Failed to calculate cosine similarity: {e}")
            return 0.0


class LLMReranker:
    """LLM-based reranking for context relevance."""

    def __init__(self, config: MemoryConfig):
        """Initialize LLM reranker.

        Args:
            config: Memory configuration
        """
        self.config = config
        self.enabled = True  # Could be disabled if no LLM available

    async def rerank(self, query: str, memories: List[MemoryRecord],
                    scores: List[float]) -> Tuple[List[MemoryRecord], List[float]]:
        """Rerank memories using LLM for context relevance.

        Args:
            query: Original query
            memories: List of memory records
            scores: Current scores

        Returns:
            Tuple of reranked memories and scores
        """
        if not self.enabled or len(memories) <= 1:
            return memories, scores

        try:
            # Create pairs of (memory, score) for sorting
            memory_score_pairs = list(zip(memories, scores))

            # Simple heuristic-based reranking (placeholder for actual LLM)
            reranked_pairs = self._heuristic_rerank(query, memory_score_pairs)

            # Separate back into lists
            reranked_memories = [pair[0] for pair in reranked_pairs]
            reranked_scores = [pair[1] for pair in reranked_pairs]

            return reranked_memories, reranked_scores

        except Exception as e:
            logger.warning(f"Failed to rerank with LLM: {e}")
            return memories, scores

    def _heuristic_rerank(self, query: str,
                         memory_score_pairs: List[Tuple[MemoryRecord, float]]) -> List[Tuple[MemoryRecord, float]]:
        """Heuristic-based reranking as placeholder for LLM.

        Args:
            query: Query string
            memory_score_pairs: List of (memory, score) pairs

        Returns:
            Reranked list of (memory, score) pairs
        """
        query_lower = query.lower()
        boosted_pairs = []

        for memory, score in memory_score_pairs:
            boosted_score = score

            # Boost recent successful memories
            if memory.get_success_rate() > 0.8:
                boosted_score += 0.1

            # Boost memories with matching action patterns
            if "tap" in query_lower and any("TAP" in str(action.action) for action in memory.action_sequence):
                boosted_score += 0.05

            if "type" in query_lower and any("TYPE" in str(action.action) for action in memory.action_sequence):
                boosted_score += 0.05

            # Boost memories with similar complexity
            if len(memory.action_sequence) <= 5 and "simple" in query_lower:
                boosted_score += 0.05
            elif len(memory.action_sequence) > 10 and "complex" in query_lower:
                boosted_score += 0.05

            # Penalize very old memories
            days_old = (datetime.now() - memory.created_at).days
            if days_old > 7:
                boosted_score -= 0.02 * (days_old - 7)

            boosted_pairs.append((memory, max(0.0, min(1.0, boosted_score))))

        # Sort by boosted score
        return sorted(boosted_pairs, key=lambda x: x[1], reverse=True)


class QMDSearchEngine:
    """Mock QMD search engine with BM25 + Vector + LLM reranking.

    This is a placeholder implementation that mimics QMD's multi-modal search.
    Replace with actual QMD client when available.
    """

    def __init__(self, config: MemoryConfig):
        """Initialize QMD search engine.

        Args:
            config: Memory configuration
        """
        self.config = config
        self.index_path = config.qmd_index_path
        self.search_k = config.qmd_search_k

        # Initialize components
        self.bm25_scorer = BM25Scorer()
        self.vector_scorer = VectorSimilarityScorer(config.embedding_dimension)
        self.llm_reranker = LLMReranker(config)

        # Weights for combining scores
        self.bm25_weight = 0.3
        self.vector_weight = 0.4
        self.temporal_weight = 0.3

    async def search(self, query: MemoryQuery, memories: List[MemoryRecord]) -> MemoryRetrievalResult:
        """Search memories using multi-modal approach.

        Args:
            query: Memory query
            memories: Available memories to search

        Returns:
            MemoryRetrievalResult with ranked results
        """
        start_time = time.time()

        try:
            if not memories:
                return MemoryRetrievalResult(
                    query_time_ms=(time.time() - start_time) * 1000
                )

            # Build BM25 index
            self.bm25_scorer.build_index(memories)

            # Calculate multi-modal scores
            combined_scores = []
            for i, memory in enumerate(memories):
                score = await self._calculate_combined_score(query, memory, i)
                combined_scores.append(score)

            # Sort by combined scores
            scored_memories = list(zip(memories, combined_scores))
            scored_memories.sort(key=lambda x: x[1], reverse=True)

            # Take top-k results
            top_memories = scored_memories[:self.search_k]

            # Extract memories and scores
            result_memories = [pair[0] for pair in top_memories]
            result_scores = [pair[1] for pair in top_memories]

            # LLM reranking
            reranked_memories, reranked_scores = await self.llm_reranker.rerank(
                query.goal, result_memories, result_scores
            )

            # Generate enhanced context
            enhanced_context = self._generate_enhanced_context(
                query, reranked_memories, reranked_scores
            )

            query_time = (time.time() - start_time) * 1000

            return MemoryRetrievalResult(
                memories=reranked_memories,
                similarity_scores=reranked_scores,
                retrieval_strategy="bm25_vector_llm_reranking",
                total_found=len(reranked_memories),
                query_time_ms=query_time,
                **enhanced_context
            )

        except Exception as e:
            logger.error(f"Search failed: {e}")
            return MemoryRetrievalResult(
                query_time_ms=(time.time() - start_time) * 1000
            )

    async def _calculate_combined_score(self, query: MemoryQuery,
                                      memory: MemoryRecord, memory_index: int) -> float:
        """Calculate combined score using all modalities.

        Args:
            query: Memory query
            memory: Memory record
            memory_index: Index in memory list for BM25

        Returns:
            Combined score
        """
        # BM25 text score
        bm25_score = self.bm25_scorer.score(query.goal, memory_index)

        # Vector similarity score
        vector_score = 0.0
        if query.current_screenshot_embedding and memory.semantic_embedding:
            vector_score = self.vector_scorer.cosine_similarity(
                query.current_screenshot_embedding,
                memory.semantic_embedding
            )

        # Temporal score (recency boost)
        days_old = (datetime.now() - memory.created_at).days
        temporal_score = max(0, 1 - (days_old / 30))  # Decay over 30 days

        # Success rate boost
        success_score = memory.get_success_rate()

        # Combine scores with weights
        combined_score = (
            self.bm25_weight * bm25_score +
            self.vector_weight * vector_score +
            self.temporal_weight * temporal_score +
            query.success_weight * success_score
        ) / (self.bm25_weight + self.vector_weight +
             self.temporal_weight + query.success_weight)

        # Apply filters
        if combined_score < query.similarity_threshold:
            return 0.0

        if memory.get_success_rate() < query.min_success_rate:
            return 0.0

        return combined_score

    def _generate_enhanced_context(self, query: MemoryQuery,
                                 memories: List[MemoryRecord],
                                 scores: List[float]) -> Dict[str, Any]:
        """Generate enhanced context for prompt building.

        Args:
            query: Original query
            memories: Retrieved memories
            scores: Similarity scores

        Returns:
            Enhanced context dictionary
        """
        if not memories:
            return {
                'relevant_actions': [],
                'success_patterns': [],
                'failure_warnings': [],
                'suggested_next_actions': []
            }

        # Collect relevant actions from top memories
        relevant_actions = []
        success_patterns = []
        failure_warnings = []
        suggested_actions = set()

        for memory in memories[:5]:  # Top 5 memories
            # Add successful action sequences
            if memory.get_success_rate() > 0.8:
                for action in memory.action_sequence[:3]:  # First 3 steps
                    if action.success:
                        relevant_actions.append(action)

                        # Extract action patterns
                        action_type = action.action.get("action", "")
                        if action_type:
                            suggested_actions.add(f"Consider {action_type.lower()} action")

                # Add success patterns
                if memory.total_steps <= 5:
                    success_patterns.append(f"Similar task completed in {memory.total_steps} steps")
                if memory.total_execution_time < 30:
                    success_patterns.append("Fast completion possible")

            # Add failure warnings
            if memory.get_success_rate() < 0.5:
                common_failures = [action for action in memory.action_sequence if not action.success]
                if common_failures:
                    failure_type = common_failures[0].action.get("action", "unknown")
                    failure_warnings.append(f"Be careful with {failure_type} actions - high failure rate")

        return {
            'relevant_actions': relevant_actions[:query.max_context_actions],
            'success_patterns': success_patterns[:3],
            'failure_warnings': failure_warnings[:2],
            'suggested_next_actions': list(suggested_actions)[:3]
        }


class RetrievalLayer:
    """Retrieval layer for memory system."""

    def __init__(self, config: MemoryConfig):
        """Initialize retrieval layer.

        Args:
            config: Memory configuration
        """
        self.config = config
        self.enabled = config.enabled
        self.search_engine = QMDSearchEngine(config) if self.enabled else None

    async def retrieve_enhanced_context(self, goal: str, action_history: List[Dict[str, Any]],
                                      current_screenshot_embedding: List[float],
                                      memories: List[MemoryRecord]) -> MemoryRetrievalResult:
        """Retrieve enhanced context for prompt building.

        Args:
            goal: Current task goal
            action_history: Current action history
            current_screenshot_embedding: Embedding of current screenshot
            memories: Available memories from storage

        Returns:
            MemoryRetrievalResult with enhanced context
        """
        if not self.enabled or not self.search_engine:
            return MemoryRetrievalResult()

        try:
            # Build query
            query = MemoryQuery(
                goal=goal,
                current_screenshot_embedding=current_screenshot_embedding,
                action_history=action_history,
                max_results=self.config.max_context_actions,
                similarity_threshold=self.config.similarity_threshold,
                temporal_weight=self.config.temporal_weight
            )

            # Extract semantic tags from action history
            query.semantic_tags = self._extract_semantic_tags(action_history, goal)

            # Search using QMD-style multi-modal approach
            result = await self.search_engine.search(query, memories)

            logger.debug(f"Retrieved {len(result.memories)} relevant memories in {result.query_time_ms:.1f}ms")

            return result

        except Exception as e:
            logger.error(f"Failed to retrieve enhanced context: {e}")
            return MemoryRetrievalResult()

    def _extract_semantic_tags(self, action_history: List[Dict[str, Any]], goal: str) -> List[str]:
        """Extract semantic tags from action history and goal.

        Args:
            action_history: Current action history
            goal: Task goal

        Returns:
            List of semantic tags
        """
        tags = set()

        # Extract from goal
        goal_lower = goal.lower()
        if "login" in goal_lower:
            tags.add("authentication")
        if "search" in goal_lower:
            tags.add("search")
        if "post" in goal_lower:
            tags.add("content_creation")
        if "like" in goal_lower or "favorite" in goal_lower:
            tags.add("engagement")

        # Extract from recent actions
        for action_dict in action_history[-5:]:  # Last 5 actions
            action_type = action_dict.get("action", {}).get("action", "")
            if action_type == "TYPE":
                tags.add("form_filling")
            elif action_type == "SWIPE":
                tags.add("navigation")
            elif action_type == "TAP":
                tags.add("selection")

        return list(tags)

    def is_enabled(self) -> bool:
        """Check if retrieval layer is enabled."""
        return self.enabled and self.search_engine is not None