"""
LLM Client: Provider 抽象 + 顺序 fallback

Primary : MiniMax 官方 API（OpenAI 兼容协议）→ MiniMax-M3
Fallback: klugai relay（Anthropic 协议）→ claude-haiku-4-5-20251001

上层（agent.py / scenarios 脚本）只使用厂商无关的中间消息格式：
    {"role": "user"|"assistant"|"tool",
     "content": str,
     "tool_calls": [{"id": str, "name": str, "input": dict}] | None,
     "tool_call_id": str | None}

工具元数据同样是厂商无关的单一 source of truth：
    {"name": str, "description": str, "parameters": <JSON Schema>}
由各 Provider 自行转成 OpenAI function 或 Anthropic input_schema 格式。
"""

import json
import os
import re
import time as _time
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import anthropic
import openai
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

MINIMAX_MODEL = "MiniMax-M3"
KLUGAI_MODEL = "claude-haiku-4-5-20251001"

_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)
_THINK_OPEN_RE = re.compile(r"<think>(.*)\Z", re.DOTALL)


def extract_reset_at(exc: Exception) -> datetime | None:
    """klugai 成本限额 429 响应体里的 UTC 恢复时间，取不到返回 None。"""
    try:
        body = getattr(exc, "body", None)
        if not isinstance(body, dict):
            return None
        reset_str = body.get("resetAt") or (body.get("error") or {}).get("resetAt")
        if reset_str:
            return datetime.fromisoformat(reset_str.replace("Z", "+00:00"))
    except Exception:
        pass
    return None


def split_think(text: str) -> tuple[str, str]:
    """把 MiniMax 内联的 <think> 推理块从正文中剥离，返回 (正文, 推理)。

    响应被截断时可能出现未闭合的 <think>，此处一并吞掉，避免泄漏到消息历史。
    """
    if not text:
        return "", ""
    reasoning = "\n".join(m.strip() for m in _THINK_RE.findall(text))
    body = _THINK_RE.sub("", text)
    open_only = _THINK_OPEN_RE.search(body)
    if open_only:
        reasoning = (reasoning + "\n" + open_only.group(1).strip()).strip()
        body = body[: open_only.start()]
    return body.strip(), reasoning.strip()


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str
    # 暂态错误：同一 provider 内重试
    retry_errors: tuple[type[Exception], ...]
    # 凭证/额度类错误：不重试，直接切换到下一个 provider
    failover_errors: tuple[type[Exception], ...]

    def build_request(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        system: str | None,
        max_tokens: int,
    ) -> dict: ...

    def call(self, client: Any, request: dict) -> Any: ...

    def parse_response(self, response: Any) -> dict: ...


class MiniMaxProvider:
    """MiniMax 官方 API（OpenAI 兼容协议）。"""

    name = "minimax"
    model = MINIMAX_MODEL
    retry_errors = (
        openai.RateLimitError,
        openai.InternalServerError,
        openai.APIConnectionError,
    )
    failover_errors = (
        openai.AuthenticationError,
        openai.PermissionDeniedError,
    )

    @staticmethod
    def _tools(tools: list[dict]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            }
            for t in tools
        ]

    @staticmethod
    def _messages(messages: list[dict], system: str | None) -> list[dict]:
        out: list[dict] = []
        if system:
            out.append({"role": "system", "content": system})
        for m in messages:
            if m["role"] == "tool":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": m["tool_call_id"],
                        "content": m.get("content") or "",
                    }
                )
                continue
            if m["role"] == "assistant" and m.get("tool_calls"):
                out.append(
                    {
                        "role": "assistant",
                        "content": m.get("content") or None,
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {
                                    "name": tc["name"],
                                    "arguments": json.dumps(tc["input"], ensure_ascii=False),
                                },
                            }
                            for tc in m["tool_calls"]
                        ],
                    }
                )
                continue
            out.append({"role": m["role"], "content": m.get("content") or ""})
        return out

    def build_request(self, messages, tools, system, max_tokens) -> dict:
        request = {
            "model": self.model,
            "messages": self._messages(messages, system),
            "max_tokens": max_tokens,
        }
        if tools:
            request["tools"] = self._tools(tools)
        return request

    def call(self, client, request):
        return client.chat.completions.create(**request)

    def parse_response(self, response) -> dict:
        message = response.choices[0].message
        content, reasoning = split_think(message.content or "")
        tool_calls = []
        for tc in message.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append({"id": tc.id, "name": tc.function.name, "input": args})
        return {
            "content": content,
            "reasoning": reasoning,
            "tool_calls": tool_calls,
            "stop": "tool_calls" if tool_calls else "end",
        }


class KlugaiProvider:
    """klugai relay（Anthropic Messages 协议）。"""

    name = "klugai"
    model = KLUGAI_MODEL
    retry_errors = (
        anthropic.RateLimitError,
        anthropic.InternalServerError,
        anthropic.APIConnectionError,
    )
    failover_errors = (
        anthropic.AuthenticationError,
        anthropic.PermissionDeniedError,
    )

    @staticmethod
    def _tools(tools: list[dict]) -> list[dict]:
        return [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["parameters"],
            }
            for t in tools
        ]

    @staticmethod
    def _messages(messages: list[dict]) -> list[dict]:
        """转 Anthropic 格式。

        Anthropic 要求同一 assistant 轮次的所有 tool_result 合并进一条 user 消息，
        因此连续的 tool 角色消息要先攒起来再一次性 flush。
        """
        out: list[dict] = []
        pending_results: list[dict] = []

        def flush() -> None:
            if pending_results:
                out.append({"role": "user", "content": list(pending_results)})
                pending_results.clear()

        for m in messages:
            if m["role"] == "tool":
                pending_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": m["tool_call_id"],
                        "content": m.get("content") or "",
                    }
                )
                continue
            flush()
            if m["role"] == "assistant" and m.get("tool_calls"):
                blocks: list[dict] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                blocks.extend(
                    {
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc["input"],
                    }
                    for tc in m["tool_calls"]
                )
                out.append({"role": "assistant", "content": blocks})
                continue
            out.append({"role": m["role"], "content": m.get("content") or ""})

        flush()
        return out

    def build_request(self, messages, tools, system, max_tokens) -> dict:
        request = {
            "model": self.model,
            "messages": self._messages(messages),
            "max_tokens": max_tokens,
        }
        if system:
            request["system"] = system
        if tools:
            request["tools"] = self._tools(tools)
        return request

    def call(self, client, request):
        return client.messages.create(**request)

    def parse_response(self, response) -> dict:
        texts: list[str] = []
        tool_calls: list[dict] = []
        for block in response.content:
            if block.type == "text":
                texts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({"id": block.id, "name": block.name, "input": block.input})
        return {
            "content": "\n".join(t.strip() for t in texts if t.strip()),
            "reasoning": "",
            "tool_calls": tool_calls,
            "stop": "tool_calls" if tool_calls else "end",
        }


def make_client() -> list[tuple[Any, LLMProvider]]:
    """返回按优先级排序的 (client, provider) 列表。

    MINIMAX_API_KEY 缺失时自动跳过 primary，退化为纯 klugai 模式（回滚开关）。
    """
    load_dotenv(REPO_ROOT / ".env", override=True)

    pairs: list[tuple[Any, LLMProvider]] = []

    minimax_key = os.environ.get("MINIMAX_API_KEY")
    minimax_url = os.environ.get("MINIMAX_BASE_URL")
    if minimax_key and minimax_url:
        pairs.append(
            (openai.OpenAI(api_key=minimax_key, base_url=minimax_url), MiniMaxProvider())
        )

    klugai_key = os.environ.get("ANTHROPIC_API_KEY")
    klugai_url = os.environ.get("ANTHROPIC_BASE_URL")
    if klugai_key:
        pairs.append(
            (anthropic.Anthropic(api_key=klugai_key, base_url=klugai_url), KlugaiProvider())
        )

    if not pairs:
        raise RuntimeError(
            "未配置任何 LLM provider：.env 需提供 MINIMAX_API_KEY + MINIMAX_BASE_URL "
            "或 ANTHROPIC_API_KEY"
        )
    return pairs


def call_with_fallback(
    providers: list[tuple[Any, LLMProvider]],
    messages: list[dict],
    tools: list[dict] | None = None,
    system: str | None = None,
    max_tokens: int = 2048,
    attempts_per_provider: int = 2,
    logger=None,
) -> tuple[Any, LLMProvider]:
    """按顺序尝试每个 provider，返回 (原始响应, 命中的 provider)。

    - 暂态错误（限流/5xx/网络）：同一 provider 内退避重试，耗尽后切到下一个
    - 凭证/权限错误：不重试，立即切到下一个 provider
    - 其余错误（如 400 请求格式错误）：立即抛出，避免被 fallback 的报错掩盖真实原因
    """
    last_exc: Exception | None = None

    for client, provider in providers:
        request = provider.build_request(messages, tools, system, max_tokens)
        for attempt in range(attempts_per_provider):
            try:
                return provider.call(client, request), provider
            except provider.failover_errors as exc:
                last_exc = exc
                if logger:
                    logger.warning(
                        "[%s] 凭证/权限错误 %s，直接切换 provider: %s",
                        provider.name,
                        type(exc).__name__,
                        str(exc)[:160],
                    )
                break
            except provider.retry_errors as exc:
                last_exc = exc
                wait = 1.5 * (attempt + 1)
                if logger:
                    logger.warning(
                        "[%s] 暂态错误 %s (attempt %d)，%.1fs 后重试: %s",
                        provider.name,
                        type(exc).__name__,
                        attempt + 1,
                        wait,
                        str(exc)[:160],
                    )
                _time.sleep(wait)

    assert last_exc is not None
    raise last_exc


def complete_text(
    providers: list[tuple[Any, LLMProvider]],
    prompt: str,
    max_tokens: int = 2048,
    system: str | None = None,
    logger=None,
) -> str:
    """单轮纯文本补全，返回已剥离协议细节与 <think> 块的正文。"""
    response, provider = call_with_fallback(
        providers,
        messages=[{"role": "user", "content": prompt}],
        system=system,
        max_tokens=max_tokens,
        logger=logger,
    )
    return provider.parse_response(response)["content"]
