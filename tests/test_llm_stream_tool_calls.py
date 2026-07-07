# tests/test_llm_stream_tool_calls.py
"""Streaming tool-call argument accumulation.

Regression test: in OpenAI/DeepSeek streaming, only the FIRST tool-call delta
carries the `id` + `name`; subsequent deltas carry `id=None` and only fragments
of `function.arguments`, associated by `index`. The accumulator must reconstruct
the full arguments JSON instead of dropping the id=None fragments.
"""

import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest

from llm.controller import ModelsController
from orchestration.loop import _accumulate_stream


def _tc(index, tid, name, arguments):
    return NS(index=index, id=tid, function=NS(name=name, arguments=arguments))


def _chunk(delta=None, finish=None, usage=None):
    return NS(choices=[NS(delta=delta, finish_reason=finish)], usage=usage)


def _delta(content=None, tool_calls=None):
    return NS(content=content, reasoning_content=None, tool_calls=tool_calls)


@pytest.mark.asyncio
async def test_streaming_tool_args_accumulate_across_id_none_deltas():
    """Args streamed across deltas with id=None must reconstruct fully."""
    # Real DeepSeek streaming shape: name on first delta, args in fragments after.
    chunks = [
        _chunk(_delta(tool_calls=[_tc(0, "call_abc", "file_manager", "")])),
        _chunk(_delta(tool_calls=[_tc(0, None, None, '{"action": "write", ')])),
        _chunk(_delta(tool_calls=[_tc(0, None, None, '"path": "saludo.py", ')])),
        _chunk(_delta(tool_calls=[_tc(0, None, None, '"content": "print(1)"}')])),
        _chunk(delta=None, finish="tool_calls"),
    ]

    async def _fake_stream():
        for ch in chunks:
            yield ch

    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_fake_stream())

    mc = ModelsController()
    emitted = []
    async for sc in mc._stream_openai_async(
        client, "deepseek-v4-flash", [], 0.5, tools=[{"type": "function"}], tool_choice="auto"
    ):
        emitted.append(sc)

    # Pipe the emitted StreamChunks through the loop accumulator (the real path)
    async def _gen():
        for sc in emitted:
            yield sc

    full_text, tool_calls, finish, _ = await _accumulate_stream(_gen(), None)

    assert len(tool_calls) == 1, f"esperaba 1 tool call, obtuve {len(tool_calls)}"
    fn = tool_calls[0]["function"]
    assert fn["name"] == "file_manager"
    parsed = json.loads(fn["arguments"])
    assert parsed == {"action": "write", "path": "saludo.py", "content": "print(1)"}


@pytest.mark.asyncio
async def test_streaming_two_parallel_tool_calls_keep_args_separate():
    """Two tool calls (index 0 and 1) must not mix their argument fragments."""
    chunks = [
        _chunk(_delta(tool_calls=[_tc(0, "call_a", "file_manager", "")])),
        _chunk(_delta(tool_calls=[_tc(1, "call_b", "code_search", "")])),
        _chunk(_delta(tool_calls=[_tc(0, None, None, '{"action": "read", ')])),
        _chunk(_delta(tool_calls=[_tc(1, None, None, '{"pattern": ')])),
        _chunk(_delta(tool_calls=[_tc(0, None, None, '"path": "a.py"}')])),
        _chunk(_delta(tool_calls=[_tc(1, None, None, '"def x"}')])),
        _chunk(delta=None, finish="tool_calls"),
    ]

    async def _fake_stream():
        for ch in chunks:
            yield ch

    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_fake_stream())

    mc = ModelsController()
    emitted = []
    async for sc in mc._stream_openai_async(client, "m", [], 0.5, tools=[{}], tool_choice="auto"):
        emitted.append(sc)

    async def _gen():
        for sc in emitted:
            yield sc

    _, tool_calls, _, _ = await _accumulate_stream(_gen(), None)
    by_name = {tc["function"]["name"]: json.loads(tc["function"]["arguments"]) for tc in tool_calls}
    assert by_name["file_manager"] == {"action": "read", "path": "a.py"}
    assert by_name["code_search"] == {"pattern": "def x"}
