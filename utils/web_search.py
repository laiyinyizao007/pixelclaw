"""DuckDuckGo web search helper for error-recovery context.

Never raises — all failures return an empty list so callers are never blocked.
"""

import logging
from typing import List

_logger = logging.getLogger(__name__)

try:
    from duckduckgo_search import DDGS as _DDGS
except ImportError:
    _DDGS = None  # type: ignore[assignment]


def search(query: str, max_results: int = 3) -> List[str]:
    """
    Search DuckDuckGo for query and return up to max_results text snippets.

    Each snippet is formatted as "title: body excerpt" for direct logging.
    Returns [] if the duckduckgo-search package is unavailable or the
    network request fails.
    """
    _logger.info("[WebSearch] 查询: %s", query)

    if _DDGS is None:
        _logger.warning("[WebSearch] duckduckgo-search 未安装，跳过搜索")
        return []

    try:
        with _DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))

        snippets = [
            f"{r.get('title', '')}: {r.get('body', '')}"
            for r in results
            if r.get("body")
        ]
        _logger.info("[WebSearch] 找到 %d 条结果", len(snippets))
        return snippets

    except Exception as exc:
        _logger.warning("[WebSearch] 搜索失败 (%s: %s)", type(exc).__name__, exc)
        return []
