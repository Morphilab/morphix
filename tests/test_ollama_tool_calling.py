# tests/test_ollama_tool_calling.py
"""Verify Ollama receives native tools= parameter.

Before the fix, controller.call() and call_stream() dropped the `tools`
argument for Ollama clients, so tool-calling models (llama3.1, qwen2.5)
never saw the function definitions and could not invoke tools natively.
"""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm.controller import ModelsController

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ollama_response(tool_calls=None):
    """Return a dict-like object mimicking Ollama ChatResponse."""
    msg = {"role": "assistant", "content": ""}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return NS(
        get=lambda key, default=None: {
            "message": msg,
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 10,
            "eval_count": 5,
        }.get(key, default),
    )


def _make_ollama_stream_chunks(tool_calls=None):
    """Yield stream chunks: content + tool call + done."""
    msg = {"role": "assistant", "content": ""}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    yield NS(get=lambda k, d=None: {"message": {"content": ""}, "done": False}.get(k, d))
    if tool_calls:
        yield NS(get=lambda k, d=None: {"message": msg, "done": False}.get(k, d))
    yield NS(
        get=lambda k, d=None: {
            "message": {},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 10,
            "eval_count": 5,
        }.get(k, d)
    )


TOOLS_FIXTURE = [
    {
        "type": "function",
        "function": {
            "name": "file_manager",
            "description": "Read/write files",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "path": {"type": "string"},
                },
            },
        },
    }
]


# ---------------------------------------------------------------------------
# Non-streaming: controller.call() must pass tools to Ollama client.chat()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_ollama_receives_tools_kwarg():
    """controller.call() with an Ollama client must include tools= in client.chat()."""
    ollama_client = MagicMock()
    ollama_client.chat = MagicMock(return_value=_make_ollama_response())

    with (
        patch(
            "llm.controller.LLMProvider.get_client", return_value=(ollama_client, "llama3.1", 0.5)
        ),
        patch("core.circuit_breaker.CircuitBreakerRegistry") as mock_cb,
        patch("core.rate_limiter.get_rate_limiter") as mock_rl,
        patch("core.metrics.metrics") as mock_m,
    ):
        mock_rl.return_value.acquire = AsyncMock(return_value=True)
        mock_cb.get.return_value = MagicMock()

        mc = ModelsController()
        await mc.call(
            messages=[{"role": "user", "content": "test"}],
            role="agent",
            tools=TOOLS_FIXTURE,
        )

    assert ollama_client.chat.called, "Ollama client.chat() was never called"
    call_kwargs = ollama_client.chat.call_args
    assert "tools" in call_kwargs.kwargs, (
        "Ollama client.chat() did NOT receive tools= parameter. "
        f"Received kwargs: {list(call_kwargs.kwargs.keys())}"
    )
    assert call_kwargs.kwargs["tools"] == TOOLS_FIXTURE


@pytest.mark.asyncio
async def test_call_ollama_no_tools_when_none():
    """controller.call() without tools must NOT inject tools kwarg."""
    ollama_client = MagicMock()
    ollama_client.chat = MagicMock(return_value=_make_ollama_response())

    with (
        patch(
            "llm.controller.LLMProvider.get_client", return_value=(ollama_client, "llama3.1", 0.5)
        ),
        patch("core.circuit_breaker.CircuitBreakerRegistry") as mock_cb,
        patch("core.rate_limiter.get_rate_limiter") as mock_rl,
        patch("core.metrics.metrics") as mock_m,
    ):
        mock_rl.return_value.acquire = AsyncMock(return_value=True)
        mock_cb.get.return_value = MagicMock()

        mc = ModelsController()
        await mc.call(
            messages=[{"role": "user", "content": "test"}],
            role="agent",
            tools=None,
        )

    call_kwargs = ollama_client.chat.call_args
    assert (
        "tools" not in call_kwargs.kwargs
    ), "Ollama client.chat() received tools= when none were provided"


# ---------------------------------------------------------------------------
# Streaming: call_stream() must forward tools to _stream_ollama → client.chat()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_stream_ollama_forwards_tools():
    """call_stream() must pass tools to _stream_ollama when client is Ollama."""
    ollama_client = MagicMock()
    chunks = list(_make_ollama_stream_chunks())
    ollama_client.chat = MagicMock(return_value=iter(chunks))

    mc = ModelsController()
    with (
        patch(
            "llm.controller.LLMProvider.get_async_client",
            return_value=(ollama_client, "llama3.1", 0.5),
        ),
        patch("core.circuit_breaker.CircuitBreakerRegistry") as mock_cb,
        patch("core.context_manager.ContextManager") as mock_cm,
    ):
        mock_cb.get.return_value = MagicMock()
        mock_cm.estimate_tokens = MagicMock(return_value=100)
        mock_cm._max_tokens = MagicMock(return_value=100000)

        emitted = []
        async for chunk in mc.call_stream(
            messages=[{"role": "user", "content": "hi"}],
            role="agent",
            tools=TOOLS_FIXTURE,
        ):
            emitted.append(chunk)

    assert ollama_client.chat.called
    call_kwargs = ollama_client.chat.call_args
    assert "tools" in call_kwargs.kwargs, (
        "call_stream() did NOT forward tools= to Ollama _stream_ollama(). "
        f"Received kwargs: {list(call_kwargs.kwargs.keys())}"
    )
    assert call_kwargs.kwargs["tools"] == TOOLS_FIXTURE


# ---------------------------------------------------------------------------
# Fallback: forced Ollama fallback must also pass tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_fallback_ollama_receives_tools():
    """When primary provider fails and fallback forces Ollama, tools must be passed."""
    failing_client = MagicMock()
    failing_client.chat.completions.create = AsyncMock(side_effect=Exception("API down"))

    ollama_client = MagicMock()
    ollama_client.chat = MagicMock(return_value=_make_ollama_response())

    with (
        patch("llm.controller.LLMProvider.get_client") as mock_get_client,
        patch("core.circuit_breaker.CircuitBreakerRegistry") as mock_cb,
        patch("core.rate_limiter.get_rate_limiter") as mock_rl,
        patch("core.metrics.metrics") as mock_m,
    ):
        mock_rl.return_value.acquire = AsyncMock(return_value=True)
        cb_instance = MagicMock()
        mock_cb.get.return_value = cb_instance

        # First call: return OpenAI client (which fails)
        # Second call (fallback): return Ollama client
        mock_get_client.side_effect = [
            (failing_client, "deepseek-v4-flash", 0.7),
            (ollama_client, "llama3.1", 0.5),
        ]

        mc = ModelsController()
        mc._max_retries = 1
        result = await mc.call(
            messages=[{"role": "user", "content": "test"}],
            role="agent",
            tools=TOOLS_FIXTURE,
        )

    assert ollama_client.chat.called, "Fallback Ollama client.chat() was never called"
    call_kwargs = ollama_client.chat.call_args
    assert "tools" in call_kwargs.kwargs, (
        "Fallback Ollama client.chat() did NOT receive tools=. "
        f"Received kwargs: {list(call_kwargs.kwargs.keys())}"
    )
    assert call_kwargs.kwargs["tools"] == TOOLS_FIXTURE
