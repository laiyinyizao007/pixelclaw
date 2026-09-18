"""
LLM Client: 共享的 Anthropic 双 relay 客户端

从 scenarios/boss/scripts/smart_match_greet.py 提取，供 agent 层复用。
Primary: minnimax relay (ANTHROPIC_BACKUP_API_KEY / BACKUP_BASE_URL) → MiniMax-M3
Fallback: klugai relay (ANTHROPIC_API_KEY / BASE_URL) → claude-haiku-4-5-20251001
"""

import os
import time as _time
from pathlib import Path

import anthropic
from anthropic import RateLimitError, InternalServerError, APIStatusError
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]


def make_client() -> tuple[anthropic.Anthropic, anthropic.Anthropic | None]:
    """返回 (primary, fallback) 客户端对，fallback 可能为 None。"""
    load_dotenv(REPO_ROOT / ".env", override=True)

    primary_key = os.environ.get("ANTHROPIC_BACKUP_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    primary_url = os.environ.get("ANTHROPIC_BACKUP_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL")
    primary = anthropic.Anthropic(api_key=primary_key, base_url=primary_url)

    backup_key = os.environ.get("ANTHROPIC_API_KEY")
    backup_url = os.environ.get("ANTHROPIC_BASE_URL")
    fallback = None
    if backup_key and (backup_key != primary_key or backup_url != primary_url):
        fallback = anthropic.Anthropic(api_key=backup_key, base_url=backup_url)

    return primary, fallback


def call_with_fallback(
    primary: anthropic.Anthropic,
    fallback: anthropic.Anthropic | None,
    fallback_model: str | None = None,
    **kwargs,
) -> anthropic.types.Message:
    """
    带重试 + fallback 的 messages.create 调用。
    每个 client 最多重试 2 次，失败后切换到 fallback；fallback 使用 fallback_model。
    """
    last_exc = None
    clients = [(primary, "primary")]
    if fallback:
        clients.append((fallback, "fallback"))

    for client, label in clients:
        call_kwargs = dict(kwargs)
        if label == "fallback" and fallback_model:
            call_kwargs["model"] = fallback_model
        for attempt in range(2):
            try:
                return client.messages.create(**call_kwargs)
            except (RateLimitError, InternalServerError, APIStatusError) as exc:
                last_exc = exc
                _time.sleep(1.5 * (attempt + 1))
            except Exception:
                raise

    raise last_exc
