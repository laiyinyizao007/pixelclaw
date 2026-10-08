"""llm_client Provider 抽象层单测：协议转换、<think> 剥离、fallback 决策。"""

import json
from types import SimpleNamespace

import anthropic
import openai
import pytest

from api.services import llm_client as llm

TOOLS = [
    {
        "name": "get_weather",
        "description": "Get weather for a city",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    }
]


# --------------------------------------------------------------------------
# split_think
# --------------------------------------------------------------------------


def test_split_think_extracts_and_strips():
    body, reasoning = llm.split_think("<think>weighing options</think>Final answer.")
    assert body == "Final answer."
    assert reasoning == "weighing options"


def test_split_think_handles_multiple_blocks():
    body, reasoning = llm.split_think("<think>a</think>X<think>b</think>Y")
    assert body == "XY"
    assert reasoning == "a\nb"


def test_split_think_handles_unclosed_block():
    """响应被 max_tokens 截断时 <think> 可能未闭合，不得泄漏到正文。"""
    body, reasoning = llm.split_think("Partial.<think>still thinking and cut off")
    assert body == "Partial."
    assert "still thinking" in reasoning
    assert "<think>" not in body


def test_split_think_passthrough_without_marker():
    assert llm.split_think("plain text") == ("plain text", "")


def test_split_think_empty():
    assert llm.split_think("") == ("", "")


# --------------------------------------------------------------------------
# 工具元数据 → 各协议的转换一致性
# --------------------------------------------------------------------------


def test_tool_conversion_preserves_same_schema_across_providers():
    openai_tools = llm.MiniMaxProvider._tools(TOOLS)
    anthropic_tools = llm.KlugaiProvider._tools(TOOLS)

    assert openai_tools == [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather for a city",
                "parameters": TOOLS[0]["parameters"],
            },
        }
    ]
    assert anthropic_tools == [
        {
            "name": "get_weather",
            "description": "Get weather for a city",
            "input_schema": TOOLS[0]["parameters"],
        }
    ]

    # 同一份 source of truth：name/description/schema 三者必须逐字一致
    assert openai_tools[0]["function"]["name"] == anthropic_tools[0]["name"]
    assert openai_tools[0]["function"]["description"] == anthropic_tools[0]["description"]
    assert openai_tools[0]["function"]["parameters"] is anthropic_tools[0]["input_schema"]


# --------------------------------------------------------------------------
# 中间消息格式 → 各协议的序列化
# --------------------------------------------------------------------------

NEUTRAL_MESSAGES = [
    {"role": "user", "content": "weather in Beijing?"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "c1", "name": "get_weather", "input": {"city": "Beijing"}}],
    },
    {"role": "tool", "tool_call_id": "c1", "content": "Sunny"},
]


def test_minimax_serializes_tool_calls_with_json_string_arguments():
    out = llm.MiniMaxProvider._messages(NEUTRAL_MESSAGES, system="be terse")

    assert out[0] == {"role": "system", "content": "be terse"}
    assert out[1] == {"role": "user", "content": "weather in Beijing?"}

    assistant = out[2]
    assert assistant["content"] is None  # 空正文必须传 None，不能传 ""
    call = assistant["tool_calls"][0]
    assert call["type"] == "function"
    assert call["id"] == "c1"
    # OpenAI 协议要求 arguments 是 JSON 字符串而非 dict
    assert json.loads(call["function"]["arguments"]) == {"city": "Beijing"}

    assert out[3] == {"role": "tool", "tool_call_id": "c1", "content": "Sunny"}


def test_klugai_groups_consecutive_tool_results_into_one_user_message():
    """Anthropic 要求同一轮的多个 tool_result 合并进单条 user 消息。"""
    messages = [
        {"role": "user", "content": "check both"},
        {
            "role": "assistant",
            "content": "working",
            "tool_calls": [
                {"id": "c1", "name": "get_weather", "input": {"city": "A"}},
                {"id": "c2", "name": "get_weather", "input": {"city": "B"}},
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "Sunny"},
        {"role": "tool", "tool_call_id": "c2", "content": "Rainy"},
    ]
    out = llm.KlugaiProvider._messages(messages)

    assert len(out) == 3
    assert out[0] == {"role": "user", "content": "check both"}

    blocks = out[1]["content"]
    assert out[1]["role"] == "assistant"
    assert blocks[0] == {"type": "text", "text": "working"}
    assert [b["type"] for b in blocks[1:]] == ["tool_use", "tool_use"]
    assert blocks[1]["input"] == {"city": "A"}  # input 保持 dict，不序列化

    results = out[2]
    assert results["role"] == "user"
    assert [b["tool_use_id"] for b in results["content"]] == ["c1", "c2"]
    assert all(b["type"] == "tool_result" for b in results["content"])


def test_klugai_omits_text_block_when_content_empty():
    out = llm.KlugaiProvider._messages(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "name": "get_weather", "input": {}}],
            }
        ]
    )
    assert [b["type"] for b in out[0]["content"]] == ["tool_use"]


def test_system_prompt_goes_to_kwarg_for_klugai_and_message_for_minimax():
    klugai_req = llm.KlugaiProvider().build_request([], TOOLS, "be terse", 128)
    minimax_req = llm.MiniMaxProvider().build_request([], TOOLS, "be terse", 128)

    assert klugai_req["system"] == "be terse"
    assert "system" not in minimax_req
    assert minimax_req["messages"][0] == {"role": "system", "content": "be terse"}


def test_build_request_omits_tools_key_when_no_tools():
    for provider in (llm.MiniMaxProvider(), llm.KlugaiProvider()):
        assert "tools" not in provider.build_request([], None, None, 128)


# --------------------------------------------------------------------------
# 响应解析 → 统一中间格式
# --------------------------------------------------------------------------


def _openai_response(content=None, tool_calls=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))]
    )


def test_minimax_parse_strips_think_and_normalizes_tool_calls():
    response = _openai_response(
        content="<think>deciding</think>Here you go.",
        tool_calls=[
            SimpleNamespace(
                id="c1",
                function=SimpleNamespace(name="get_weather", arguments='{"city": "Beijing"}'),
            )
        ],
    )
    parsed = llm.MiniMaxProvider().parse_response(response)

    assert parsed["content"] == "Here you go."
    assert parsed["reasoning"] == "deciding"
    assert parsed["tool_calls"] == [
        {"id": "c1", "name": "get_weather", "input": {"city": "Beijing"}}
    ]
    assert parsed["stop"] == "tool_calls"


def test_minimax_parse_tolerates_malformed_tool_arguments():
    response = _openai_response(
        tool_calls=[
            SimpleNamespace(id="c1", function=SimpleNamespace(name="x", arguments="{not json"))
        ]
    )
    assert llm.MiniMaxProvider().parse_response(response)["tool_calls"][0]["input"] == {}


def test_minimax_parse_end_turn():
    parsed = llm.MiniMaxProvider().parse_response(_openai_response(content="done"))
    assert parsed == {"content": "done", "reasoning": "", "tool_calls": [], "stop": "end"}


def test_klugai_parse_merges_text_blocks_and_tool_use():
    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="first"),
            SimpleNamespace(type="text", text="  "),
            SimpleNamespace(type="tool_use", id="c1", name="get_weather", input={"city": "A"}),
        ]
    )
    parsed = llm.KlugaiProvider().parse_response(response)

    assert parsed["content"] == "first"  # 空白块被丢弃
    assert parsed["reasoning"] == ""  # Anthropic 无内联推理
    assert parsed["tool_calls"] == [{"id": "c1", "name": "get_weather", "input": {"city": "A"}}]
    assert parsed["stop"] == "tool_calls"


# --------------------------------------------------------------------------
# extract_reset_at（klugai 成本限额 429）
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"resetAt": "2026-01-01T00:00:00Z"},
        {"error": {"resetAt": "2026-01-01T00:00:00Z"}},
    ],
)
def test_extract_reset_at_reads_both_shapes(body):
    reset = llm.extract_reset_at(SimpleNamespace(body=body))
    assert reset is not None
    assert reset.year == 2026
    assert reset.utcoffset().total_seconds() == 0


@pytest.mark.parametrize(
    "exc",
    [
        SimpleNamespace(body=None),
        SimpleNamespace(body="not a dict"),
        SimpleNamespace(body={}),
        SimpleNamespace(body={"resetAt": "garbage"}),
        Exception("no body attr"),
    ],
)
def test_extract_reset_at_returns_none_on_bad_input(exc):
    assert llm.extract_reset_at(exc) is None


# --------------------------------------------------------------------------
# call_with_fallback 决策逻辑
# --------------------------------------------------------------------------


class _FakeProvider:
    """按预设脚本抛错/返回的假 provider，用于驱动 fallback 分支。"""

    model = "fake"
    retry_errors = (RuntimeError,)
    failover_errors = (PermissionError,)

    def __init__(self, name, script):
        self.name = name
        self._script = list(script)
        self.calls = 0

    def build_request(self, messages, tools, system, max_tokens):
        return {"messages": messages}

    def call(self, client, request):
        self.calls += 1
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def parse_response(self, response):
        return {"content": response, "reasoning": "", "tool_calls": [], "stop": "end"}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(llm._time, "sleep", lambda _s: None)


def test_retryable_error_retries_same_provider_then_succeeds():
    provider = _FakeProvider("p1", [RuntimeError("503"), "ok"])
    response, hit = llm.call_with_fallback([(None, provider)], messages=[])

    assert response == "ok"
    assert hit is provider
    assert provider.calls == 2


def test_retry_exhaustion_falls_through_to_next_provider():
    first = _FakeProvider("p1", [RuntimeError("503"), RuntimeError("503")])
    second = _FakeProvider("p2", ["from-fallback"])

    response, hit = llm.call_with_fallback(
        [(None, first), (None, second)], messages=[], attempts_per_provider=2
    )

    assert response == "from-fallback"
    assert hit is second
    assert first.calls == 2  # 用尽重试配额


def test_auth_error_fails_over_immediately_without_retrying():
    first = _FakeProvider("p1", [PermissionError("401"), "never reached"])
    second = _FakeProvider("p2", ["from-fallback"])

    response, hit = llm.call_with_fallback([(None, first), (None, second)], messages=[])

    assert response == "from-fallback"
    assert hit is second
    assert first.calls == 1  # 凭证错误不浪费重试


def test_unclassified_error_raises_immediately_without_masking():
    """400 类错误是调用方的 bug，必须原样抛出而不是被 fallback 的报错掩盖。"""
    first = _FakeProvider("p1", [ValueError("bad request")])
    second = _FakeProvider("p2", ["should not be reached"])

    with pytest.raises(ValueError, match="bad request"):
        llm.call_with_fallback([(None, first), (None, second)], messages=[])

    assert second.calls == 0


def test_all_providers_failing_raises_last_exception():
    first = _FakeProvider("p1", [RuntimeError("a"), RuntimeError("a")])
    second = _FakeProvider("p2", [RuntimeError("last one"), RuntimeError("last one")])

    with pytest.raises(RuntimeError, match="last one"):
        llm.call_with_fallback([(None, first), (None, second)], messages=[])


def test_logger_receives_warning_per_failure():
    logged = []
    provider = _FakeProvider("p1", [RuntimeError("503"), "ok"])
    logger = SimpleNamespace(warning=lambda *a, **k: logged.append(a))

    llm.call_with_fallback([(None, provider)], messages=[], logger=logger)

    assert len(logged) == 1
    assert "p1" in logged[0]


def test_complete_text_returns_parsed_body():
    provider = _FakeProvider("p1", ["hello"])
    assert llm.complete_text([(None, provider)], "prompt") == "hello"


# --------------------------------------------------------------------------
# 真实 SDK 异常分类（确保 MRO 归类没写错）
# --------------------------------------------------------------------------


def test_real_sdk_errors_are_classified_into_the_right_buckets():
    for provider, mod in ((llm.MiniMaxProvider(), openai), (llm.KlugaiProvider(), anthropic)):
        assert issubclass(mod.RateLimitError, provider.retry_errors)
        assert issubclass(mod.InternalServerError, provider.retry_errors)
        assert issubclass(mod.APIConnectionError, provider.retry_errors)

        assert issubclass(mod.AuthenticationError, provider.failover_errors)
        assert issubclass(mod.PermissionDeniedError, provider.failover_errors)

        # 400 必须两边都不命中，从而走"立即抛出"分支
        assert not issubclass(mod.BadRequestError, provider.retry_errors)
        assert not issubclass(mod.BadRequestError, provider.failover_errors)


# --------------------------------------------------------------------------
# make_client 组装与降级
# --------------------------------------------------------------------------


def _clear_env(monkeypatch):
    monkeypatch.setattr(llm, "load_dotenv", lambda *a, **k: None)
    for var in (
        "MINIMAX_API_KEY",
        "MINIMAX_BASE_URL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)


def test_make_client_orders_minimax_before_klugai(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k2")

    assert [p.name for _, p in llm.make_client()] == ["minimax", "klugai"]


def test_make_client_skips_minimax_when_key_absent(monkeypatch):
    """回滚开关：清空 MINIMAX_API_KEY 即退化为纯 klugai 模式。"""
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k2")

    assert [p.name for _, p in llm.make_client()] == ["klugai"]


def test_make_client_skips_minimax_when_base_url_absent(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k2")

    assert [p.name for _, p in llm.make_client()] == ["klugai"]


def test_make_client_raises_when_nothing_configured(monkeypatch):
    _clear_env(monkeypatch)
    with pytest.raises(RuntimeError, match="未配置任何 LLM provider"):
        llm.make_client()


def test_providers_satisfy_the_protocol():
    assert isinstance(llm.MiniMaxProvider(), llm.LLMProvider)
    assert isinstance(llm.KlugaiProvider(), llm.LLMProvider)
