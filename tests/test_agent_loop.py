"""PixelClawAgent._loop 集成测试：真实 Provider 序列化 + 假 client 脚本化响应。

用真实的 MiniMaxProvider / KlugaiProvider 搭配假 SDK client，既验证 agent 的循环控制，
也顺带验证「中间消息格式 → 各协议请求体」的往返正确性。
"""

import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from api.services import agent as ag
from api.services import llm_client as llm


# --------------------------------------------------------------------------
# 假 SDK client：按脚本返回/抛错，并记录收到的请求体
# --------------------------------------------------------------------------


class _FakeOpenAIClient:
    """模拟 openai.OpenAI：client.chat.completions.create(**request)。"""

    def __init__(self, script):
        self._script = list(script)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.requests.append(request)
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeAnthropicClient:
    """模拟 anthropic.Anthropic：client.messages.create(**request)。"""

    def __init__(self, script):
        self._script = list(script)
        self.requests = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **request):
        self.requests.append(request)
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _openai_msg(content=None, tool_calls=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))]
    )


def _openai_tool_call(call_id, name, arguments):
    return SimpleNamespace(
        id=call_id, function=SimpleNamespace(name=name, arguments=arguments)
    )


def _anthropic_msg(*blocks):
    return SimpleNamespace(content=list(blocks))


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(block_id, name, inp):
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=inp)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def broadcast():
    """收集 (task_id, payload)，并暴露便捷的文本视图。"""
    events = []

    async def _broadcast(task_id, payload):
        events.append(payload)

    _broadcast.events = events
    _broadcast.texts = lambda: [e["text"] for e in events if e["type"] == "log"]
    return _broadcast


@pytest.fixture
def agent_factory(monkeypatch):
    """构造 PixelClawAgent 并注入指定的 provider 列表，绕过 make_client()。"""

    def _make(providers):
        monkeypatch.setattr(ag, "_PROVIDERS", providers)
        return ag.PixelClawAgent()

    return _make


@pytest.fixture(autouse=True)
def _stub_tools(monkeypatch):
    """工具执行替换为可控 stub，避免触碰 DB / ADB / 子进程。"""
    calls = []

    async def _execute(self, name, inp, task_id, broadcast):
        calls.append((name, inp))
        return f"stub-result::{name}"

    monkeypatch.setattr(ag.PixelClawAgent, "_execute_tool", _execute)
    return calls


# --------------------------------------------------------------------------
# 主路径：MiniMax tool_calls 往返
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_minimax_tool_call_roundtrip(agent_factory, broadcast, _stub_tools):
    client = _FakeOpenAIClient(
        [
            _openai_msg(
                content="<think>先看统计</think>我先查一下数据。",
                tool_calls=[_openai_tool_call("c1", "check_boss_stats", "{}")],
            ),
            _openai_msg(content="今日已打招呼 3 次。"),
        ]
    )
    agent = agent_factory([(client, llm.MiniMaxProvider())])

    await agent._loop("看看今天的情况", "t1", broadcast)

    assert _stub_tools == [("check_boss_stats", {})]

    texts = broadcast.texts()
    assert "[思考] 先看统计" in texts
    assert "[思考] 我先查一下数据。" in texts
    assert "[工具] check_boss_stats" in texts
    assert "[结果] stub-result::check_boss_stats" in texts
    assert "[思考] 今日已打招呼 3 次。" in texts
    # <think> 不得泄漏到广播正文
    assert not any("<think>" in t for t in texts)

    # 第二轮请求体：assistant 轮次 + tool 结果都已按 OpenAI 协议回写
    second = client.requests[1]["messages"]
    assert second[0]["role"] == "system"
    assert second[1] == {"role": "user", "content": "看看今天的情况"}
    assistant = second[2]
    assert assistant["content"] == "我先查一下数据。"  # 剥离 <think> 后的正文
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {}
    assert second[3] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": "stub-result::check_boss_stats",
    }

    # 工具元数据按 OpenAI function 格式下发
    assert client.requests[0]["tools"][0]["type"] == "function"
    assert {t["function"]["name"] for t in client.requests[0]["tools"]} == {
        t["name"] for t in ag.TOOLS
    }


@pytest.mark.asyncio
async def test_parallel_tool_calls_all_executed(agent_factory, broadcast, _stub_tools):
    client = _FakeOpenAIClient(
        [
            _openai_msg(
                tool_calls=[
                    _openai_tool_call("c1", "check_boss_stats", "{}"),
                    _openai_tool_call("c2", "get_recent_greetings", '{"limit": 5}'),
                ]
            ),
            _openai_msg(content="done"),
        ]
    )
    agent = agent_factory([(client, llm.MiniMaxProvider())])

    await agent._loop("both", "t1", broadcast)

    assert _stub_tools == [("check_boss_stats", {}), ("get_recent_greetings", {"limit": 5})]
    # 一条 assistant + 两条 tool 结果
    roles = [m["role"] for m in client.requests[1]["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "tool"]


# --------------------------------------------------------------------------
# fallback 路径：MiniMax 凭证失效 → klugai 完成同一轮 tool_use
# --------------------------------------------------------------------------


def _openai_auth_error():
    """真实 SDK 异常需要真实 httpx.Response（其 __init__ 会读 response.request）。"""
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    return openai.AuthenticationError(
        "invalid key",
        response=httpx.Response(401, request=request),
        body=None,
    )


@pytest.mark.asyncio
async def test_falls_back_to_klugai_and_completes_tool_roundtrip(
    agent_factory, broadcast, _stub_tools, monkeypatch
):
    monkeypatch.setattr(llm._time, "sleep", lambda _s: None)

    bad = _FakeOpenAIClient([_openai_auth_error(), _openai_auth_error()])
    good = _FakeAnthropicClient(
        [
            _anthropic_msg(
                _text_block("查一下设备"),
                _tool_use_block("tu1", "get_device_status", {}),
            ),
            _anthropic_msg(_text_block("设备已连接。")),
        ]
    )
    agent = agent_factory(
        [(bad, llm.MiniMaxProvider()), (good, llm.KlugaiProvider())]
    )

    await agent._loop("设备在线吗", "t1", broadcast)

    assert _stub_tools == [("get_device_status", {})]
    texts = broadcast.texts()
    assert "[思考] 查一下设备" in texts
    assert "[思考] 设备已连接。" in texts

    # 凭证错误不浪费重试：每轮只打一次 primary
    assert len(bad.requests) == 2
    assert len(good.requests) == 2

    # 第二轮按 Anthropic 协议回写：tool_result 合并进单条 user 消息
    second = good.requests[1]
    assert second["system"] == ag.SYSTEM_PROMPT
    msgs = second["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert [b["type"] for b in msgs[1]["content"]] == ["text", "tool_use"]
    assert msgs[2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "tu1",
        "content": "stub-result::get_device_status",
    }

    # 工具元数据按 Anthropic input_schema 格式下发
    assert "input_schema" in good.requests[0]["tools"][0]


# --------------------------------------------------------------------------
# 循环控制
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stops_when_no_tool_calls(agent_factory, broadcast, _stub_tools):
    client = _FakeOpenAIClient([_openai_msg(content="不需要工具。")])
    agent = agent_factory([(client, llm.MiniMaxProvider())])

    await agent._loop("hi", "t1", broadcast)

    assert _stub_tools == []
    assert len(client.requests) == 1


@pytest.mark.asyncio
async def test_max_steps_emits_warning(agent_factory, broadcast, monkeypatch):
    monkeypatch.setattr(ag, "MAX_STEPS", 3)
    client = _FakeOpenAIClient(
        [
            _openai_msg(tool_calls=[_openai_tool_call(f"c{i}", "check_boss_stats", "{}")])
            for i in range(3)
        ]
    )
    agent = agent_factory([(client, llm.MiniMaxProvider())])

    await agent._loop("loop forever", "t1", broadcast)

    assert len(client.requests) == 3
    warnings = [e for e in broadcast.events if e.get("level") == "WARNING"]
    assert len(warnings) == 1
    assert "已达最大步数 3" in warnings[0]["text"]
