"""
Memory System Configuration Management

Handles loading and validation of memory system configuration.
Supports environment variables, file-based config, and defaults.
"""

import os
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from .schemas import MemoryConfig

logger = logging.getLogger(__name__)


class MemoryConfigLoader:
    """Configuration loader for memory system."""

    def __init__(self, config_path: Optional[str] = None):
        """Initialize config loader.

        Args:
            config_path: Optional path to config file
        """
        self.config_path = config_path
        self._cache: Optional[MemoryConfig] = None

    def load(self, config_dict: Optional[Dict[str, Any]] = None) -> MemoryConfig:
        """Load memory configuration with precedence:
        1. Passed config_dict (highest priority)
        2. Environment variables
        3. File-based configuration
        4. Default values (lowest priority)

        Args:
            config_dict: Optional configuration dictionary

        Returns:
            MemoryConfig instance
        """
        if self._cache is not None:
            return self._cache

        # Start with defaults
        config = self._get_default_config()

        # Load from file if specified
        if self.config_path and Path(self.config_path).exists():
            try:
                file_config = self._load_from_file()
                config.update(file_config)
            except Exception as e:
                logger.warning(f"Failed to load config from file {self.config_path}: {e}")

        # Override with environment variables
        env_config = self._load_from_env()
        config.update(env_config)

        # Override with passed config
        if config_dict:
            config.update(config_dict)

        # Validate and create config object
        memory_config = self._validate_and_create_config(config)
        self._cache = memory_config

        return memory_config

    def _get_default_config(self) -> Dict[str, Any]:
        """Get default configuration."""
        return {
            "enabled": True,
            "storage_backend": "sqlite",
            "connection_string": "sqlite:///./data/pixelclaw_memory.db",
            "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
            "embedding_dimension": 384,
            "qmd_index_path": "./data/qmd_index",
            "qmd_search_k": 10,
            "include_screenshots": True,
            "semantic_extraction": True,
            "async_storage": True,
            "max_context_actions": 10,
            "similarity_threshold": 0.7,
            "temporal_weight": 0.3,
            "max_embedding_batch_size": 32,
            "cache_size": 1000,
            "cleanup_days": 30,
        }

    def _load_from_file(self) -> Dict[str, Any]:
        """Load configuration from file (YAML or JSON)."""
        import yaml
        import json

        config = {}
        path = Path(self.config_path)

        if path.suffix.lower() in ['.yaml', '.yml']:
            with open(path, 'r') as f:
                config = yaml.safe_load(f) or {}
        elif path.suffix.lower() == '.json':
            with open(path, 'r') as f:
                config = json.load(f)
        else:
            raise ValueError(f"Unsupported config file format: {path.suffix}")

        # Extract memory section if it exists
        return config.get('memory', config)

    def _load_from_env(self) -> Dict[str, Any]:
        """Load configuration from environment variables."""
        config = {}

        # Environment variable mappings
        env_mappings = {
            "PIXELCLAW_MEMORY_ENABLED": ("enabled", self._parse_bool),
            "PIXELCLAW_STORAGE_BACKEND": ("storage_backend", str),
            "OPENVIKING_DB_URL": ("connection_string", str),
            "PIXELCLAW_EMBEDDING_MODEL": ("embedding_model", str),
            "PIXELCLAW_EMBEDDING_DIM": ("embedding_dimension", int),
            "PIXELCLAW_QMD_INDEX_PATH": ("qmd_index_path", str),
            "PIXELCLAW_QMD_SEARCH_K": ("qmd_search_k", int),
            "PIXELCLAW_INCLUDE_SCREENSHOTS": ("include_screenshots", self._parse_bool),
            "PIXELCLAW_SEMANTIC_EXTRACTION": ("semantic_extraction", self._parse_bool),
            "PIXELCLAW_ASYNC_STORAGE": ("async_storage", self._parse_bool),
            "PIXELCLAW_MAX_CONTEXT_ACTIONS": ("max_context_actions", int),
            "PIXELCLAW_SIMILARITY_THRESHOLD": ("similarity_threshold", float),
            "PIXELCLAW_TEMPORAL_WEIGHT": ("temporal_weight", float),
            "PIXELCLAW_CACHE_SIZE": ("cache_size", int),
            "PIXELCLAW_CLEANUP_DAYS": ("cleanup_days", int),
        }

        for env_var, (config_key, type_converter) in env_mappings.items():
            value = os.getenv(env_var)
            if value is not None:
                try:
                    config[config_key] = type_converter(value)
                except (ValueError, TypeError) as e:
                    logger.warning(f"Invalid value for {env_var}: {value}, error: {e}")

        return config

    def _parse_bool(self, value: str) -> bool:
        """Parse string to boolean."""
        if isinstance(value, bool):
            return value
        return value.lower() in ('true', '1', 'yes', 'on')

    def _validate_and_create_config(self, config_dict: Dict[str, Any]) -> MemoryConfig:
        """Validate configuration and create MemoryConfig instance."""
        try:
            # Validate paths exist or create them
            if config_dict.get("enabled", True):
                self._ensure_directories(config_dict)

            # Create config object
            memory_config = MemoryConfig.from_dict(config_dict)

            logger.info(f"Memory system configured: backend={memory_config.storage_backend}, "
                       f"enabled={memory_config.enabled}")

            return memory_config

        except Exception as e:
            logger.error(f"Configuration validation failed: {e}")
            # Return disabled config as fallback
            fallback_config = MemoryConfig()
            fallback_config.enabled = False
            return fallback_config

    def _ensure_directories(self, config: Dict[str, Any]):
        """Ensure required directories exist."""
        # Create data directory for SQLite
        if config.get("storage_backend") == "sqlite":
            connection_string = config.get("connection_string", "")
            if connection_string.startswith("sqlite:///"):
                db_path = Path(connection_string[10:])  # Remove sqlite:/// prefix
                db_path.parent.mkdir(parents=True, exist_ok=True)

        # Create QMD index directory
        qmd_path = Path(config.get("qmd_index_path", "./data/qmd_index"))
        qmd_path.mkdir(parents=True, exist_ok=True)

    def clear_cache(self):
        """Clear cached configuration."""
        self._cache = None

    def is_enabled(self, config_dict: Optional[Dict[str, Any]] = None) -> bool:
        """Quick check if memory system is enabled."""
        if config_dict and "enabled" in config_dict:
            return config_dict["enabled"]

        # Check environment variable first
        env_enabled = os.getenv("PIXELCLAW_MEMORY_ENABLED")
        if env_enabled is not None:
            return self._parse_bool(env_enabled)

        # Default to enabled
        return True


def load_memory_config(config_dict: Optional[Dict[str, Any]] = None,
                      config_path: Optional[str] = None) -> MemoryConfig:
    """Convenience function to load memory configuration.

    Args:
        config_dict: Optional configuration dictionary
        config_path: Optional path to configuration file

    Returns:
        MemoryConfig instance
    """
    loader = MemoryConfigLoader(config_path)
    return loader.load(config_dict)


def is_memory_enabled(config_dict: Optional[Dict[str, Any]] = None) -> bool:
    """Quick check if memory system is enabled.

    Args:
        config_dict: Optional configuration dictionary

    Returns:
        True if memory system is enabled
    """
    loader = MemoryConfigLoader()
    return loader.is_enabled(config_dict)