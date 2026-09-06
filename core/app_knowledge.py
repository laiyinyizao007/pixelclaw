"""
AppAgent-style App Knowledge Base

Loads per-app JSON knowledge documents and converts them to a System Context
prompt string suitable for injection into VLM prompts.  Compatible with the
AppAgent JSON schema.
"""

import copy
import json
import os
from typing import Any, Dict, List, Optional


class AppKnowledge:
    """Manages AppAgent-format knowledge for a single app."""

    def __init__(
        self,
        app_name: str,
        knowledge_dir: str = "config/app_knowledge",
    ):
        # Sanitise app_name to prevent path traversal:
        # Strip separators, take basename, then remove any remaining ".." sequences
        sanitised = app_name.replace("/", os.sep).replace("\\", os.sep)
        sanitised = os.path.basename(sanitised)
        # Remove all occurrences of ".." to prevent embedded traversal
        import re as _re
        safe_name = _re.sub(r'\.\.+', '', sanitised).strip("_").strip(".") or "unknown"
        self.app_name = safe_name
        self.path = os.path.join(knowledge_dir, f"{safe_name}.json")
        self._data: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    def load(self) -> Dict[str, Any]:
        """Load knowledge from JSON (cached after first call)."""
        if self._data is None:
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except FileNotFoundError:
                raise FileNotFoundError(
                    f"AppKnowledge: knowledge file not found for app '{self.app_name}' "
                    f"(expected at '{self.path}')"
                )
            except json.JSONDecodeError as exc:
                raise json.JSONDecodeError(
                    f"AppKnowledge: malformed JSON in '{self.path}' for app '{self.app_name}': {exc.msg}",
                    exc.doc,
                    exc.pos,
                )
        return self._data

    def save(self, data: Dict[str, Any]) -> None:
        """Persist updated knowledge to disk."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self._data = copy.deepcopy(data)

    def add_element(self, element: Dict[str, Any]) -> None:
        """Append a newly discovered UI element to the knowledge base."""
        data = self.load()
        data.setdefault("elements", []).append(element)
        self.save(data)

    # ------------------------------------------------------------------
    # Prompt injection
    # ------------------------------------------------------------------

    def get_prompt_context(self, page_name: Optional[str] = None) -> str:
        """
        Convert knowledge to a Mobile-Agent-v2 style System Context string.

        Args:
            page_name: Optional page filter (e.g. 'home', 'job_list').
                       Elements without a 'page' field are always included.

        Returns:
            Formatted string ready for prompt injection, or "" if unavailable.
        """
        try:
            data = self.load()
        except FileNotFoundError:
            return ""

        elements: List[Dict[str, Any]] = data.get("elements", [])
        if page_name:
            elements = [
                e for e in elements
                if not e.get("page") or page_name.lower() in e.get("page", "").lower()
            ]

        if not elements:
            return ""

        display_name = data.get("app_name", self.app_name)
        lines = [f"## {display_name} 应用知识库\n"]
        for elem in elements:
            desc = elem.get("visual_description", "")
            action = elem.get("action", "")
            doc = elem.get("documentation", {})
            func = doc.get("function", "")
            post = doc.get("post_conditions", "")
            entry = f"- **{desc}**：操作「{action}」→ {func}"
            if post:
                entry += f"（预期结果：{post}）"
            lines.append(entry)

        return "\n".join(lines)
