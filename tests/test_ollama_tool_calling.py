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


# ---------------------------------------------------------------------------
# Flag gating: tool_calling=False blocks tools
# ---------------------------------------------------------------------------
_STANDARD_MOCK_ROLES = {
    "agent": {"provider": "ollama", "model": "llama3.1", "temperature": 0.7},
    "default": {"provider": "ollama", "model": "llama3.1", "temperature": 0.7},
}


@pytest.mark.asyncio
async def test_call_ollama_skips_tools_when_role_disabled():
    """tool_calling=False per role blocks tools= in non-streaming Ollama call."""
    ollama_client = MagicMock()
    ollama_client.chat = MagicMock(return_value=_make_ollama_response())

    roles = {k: {**v, "tool_calling": k != "agent"} for k, v in _STANDARD_MOCK_ROLES.items()}

    with (
        patch(
            "llm.controller.LLMProvider.get_client",
            return_value=(ollama_client, "test-model", 0.0),
        ),
        patch("core.circuit_breaker.CircuitBreakerRegistry") as mock_cb,
        patch("core.rate_limiter.get_rate_limiter") as mock_rl,
        patch("core.config.settings.model_roles", roles),
        patch("core.config.settings.tool_calling_global", True),
    ):
        mock_rl.return_value.acquire = AsyncMock(return_value=True)
        mock_cb.get.return_value = MagicMock()

        mc = ModelsController()
        await mc.call(
            messages=[{"role": "user", "content": "test"}],
            role="agent",
            tools=TOOLS_FIXTURE,
        )

    call_kwargs = ollama_client.chat.call_args
    assert call_kwargs is not None
    assert (
        "tools" not in call_kwargs.kwargs
    ), "client.chat() received tools= when tool_calling=False per role"


@pytest.mark.asyncio
async def test_call_ollama_skips_tools_when_global_disabled():
    """TOOL_CALLING=False blocks tools= even if role says True."""
    ollama_client = MagicMock()
    ollama_client.chat = MagicMock(return_value=_make_ollama_response())

    with (
        patch(
            "llm.controller.LLMProvider.get_client",
            return_value=(ollama_client, "test-model", 0.0),
        ),
        patch("core.circuit_breaker.CircuitBreakerRegistry") as mock_cb,
        patch("core.rate_limiter.get_rate_limiter") as mock_rl,
        patch("core.config.settings.model_roles", _STANDARD_MOCK_ROLES),
        patch("core.config.settings.tool_calling_global", False),
    ):
        mock_rl.return_value.acquire = AsyncMock(return_value=True)
        mock_cb.get.return_value = MagicMock()

        mc = ModelsController()
        await mc.call(
            messages=[{"role": "user", "content": "test"}],
            role="agent",
            tools=TOOLS_FIXTURE,
        )

    call_kwargs = ollama_client.chat.call_args
    assert call_kwargs is not None
    assert (
        "tools" not in call_kwargs.kwargs
    ), "client.chat() received tools= when TOOL_CALLING=False globally"


@pytest.mark.asyncio
async def test_call_stream_ollama_skips_tools_when_disabled():
    """call_stream() skips tools= when tool_calling per-role is False."""
    ollama_client = MagicMock()
    chunks = list(_make_ollama_stream_chunks())
    ollama_client.chat = MagicMock(return_value=iter(chunks))

    roles = {k: {**v, "tool_calling": k != "agent"} for k, v in _STANDARD_MOCK_ROLES.items()}

    mc = ModelsController()
    with (
        patch(
            "llm.controller.LLMProvider.get_async_client",
            return_value=(ollama_client, "llama3.1", 0.5),
        ),
        patch("core.circuit_breaker.CircuitBreakerRegistry") as mock_cb,
        patch("core.context_manager.ContextManager") as mock_cm,
        patch("core.config.settings.model_roles", roles),
        patch("core.config.settings.tool_calling_global", True),
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
    assert (
        "tools" not in call_kwargs.kwargs
    ), "client.chat() received tools= when tool_calling=False in streaming"


# ---------------------------------------------------------------------------
# Ollama model precedence
# ---------------------------------------------------------------------------


async def test_ollama_model_per_role():
    """Role's ollama_model is used when set."""
    import core.config
    from llm.provider import LLMProvider

    saved_ollama = core.config.settings.ollama_model
    saved_roles = dict(core.config.settings.model_roles)

    try:
        core.config.settings.ollama_model = "global-model"
        core.config.settings.model_roles["agent"] = {
            "provider": "ollama",
            "model": "deepseek-v4-flash",
            "ollama_model": "role-specific-model",
            "temperature": 0.7,
        }
        _, model, _ = LLMProvider._create_ollama_client("agent")
        assert model == "role-specific-model", f"Expected role-specific-model, got {model}"
    finally:
        core.config.settings.ollama_model = saved_ollama
        core.config.settings.model_roles = saved_roles


async def test_ollama_model_fallback_to_global():
    """Global OLLAMA_MODEL used when role has no ollama_model key."""
    import core.config
    from llm.provider import LLMProvider

    saved_ollama = core.config.settings.ollama_model
    saved_roles = dict(core.config.settings.model_roles)

    try:
        core.config.settings.ollama_model = "global-fallback"
        core.config.settings.model_roles["agent"] = {
            "provider": "ollama",
            "temperature": 0.7,
        }
        _, model, _ = LLMProvider._create_ollama_client("agent")
        assert model == "global-fallback", f"Expected global-fallback, got {model}"
    finally:
        core.config.settings.ollama_model = saved_ollama
        core.config.settings.model_roles = saved_roles


# ---------------------------------------------------------------------------
# Regression: caller-provided max_tokens must not leak into Ollama client.chat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_ollama_translates_max_tokens_to_num_predict():
    """max_tokens kwarg (OpenAI style) → num_predict option, NOT a chat() kwarg.

    Regression: subtask.py Safety Net passes max_tokens=4000, which used to
    raise `TypeError: Client.chat() got an unexpected keyword argument
    'max_tokens'` on the Ollama path, breaking the Safety Net offline.
    """
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
            max_tokens=4000,
        )

    assert ollama_client.chat.called
    call_kwargs = ollama_client.chat.call_args.kwargs
    assert "max_tokens" not in call_kwargs, (
        "max_tokens leaked into client.chat() — " "Ollama SDK raises TypeError for this kwarg"
    )
    options = call_kwargs.get("options", {})
    assert (
        options.get("num_predict") == 4000
    ), f"expected num_predict=4000 in options, got {options}"


@pytest.mark.asyncio
async def test_call_ollama_fallback_translates_max_tokens():
    """Same translation applies to the forced-Ollama fallback path."""
    failing_client = MagicMock()
    failing_client.chat.completions.create = MagicMock(side_effect=Exception("API down"))

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

        mock_get_client.side_effect = [
            (failing_client, "deepseek-v4-flash", 0.7),
            (ollama_client, "llama3.1", 0.5),
        ]

        mc = ModelsController()
        mc._max_retries = 1
        await mc.call(
            messages=[{"role": "user", "content": "test"}],
            role="agent",
            tools=TOOLS_FIXTURE,
            max_tokens=4000,
        )

    assert ollama_client.chat.called, "Fallback Ollama was not called"
    call_kwargs = ollama_client.chat.call_args.kwargs
    assert "max_tokens" not in call_kwargs, "max_tokens leaked into fallback chat()"
    options = call_kwargs.get("options", {})
    assert (
        options.get("num_predict") == 4000
    ), f"expected num_predict=4000 in fallback options, got {options}"


# ---------------------------------------------------------------------------
# Real SDK structures — pydantic validation (síntesis 4.6)
# ---------------------------------------------------------------------------


def _sdk_client_module():
    import importlib

    return importlib.import_module("ollama._client")


def test_sanitize_prevents_sdk_validation_error():
    """Historial mixto (args string OpenAI) → sanitize → la validación
    pydantic real del SDK (``_copy_messages``) NO lanza ValidationError."""
    try:
        _copy_messages = _sdk_client_module()._copy_messages
        from pydantic import ValidationError
    except ImportError:
        pytest.skip("ollama SDK not available")

    from llm.tool_calls import sanitize_messages_for_ollama

    raw = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"function": {"name": "file_manager", "arguments": '{"action": "write"}'}}
            ],
        }
    ]

    with pytest.raises(ValidationError):
        list(_copy_messages(raw))

    sanitized = sanitize_messages_for_ollama(raw)
    copied = list(_copy_messages(sanitized))
    assert copied[0].tool_calls[0].function.arguments == {"action": "write"}


def test_sdk_preserves_tool_name_in_result_message():
    """El repair nativo (``tool_name``) sobrevive la serialización real del SDK."""
    try:
        _copy_messages = _sdk_client_module()._copy_messages
    except ImportError:
        pytest.skip("ollama SDK not available")

    from llm.tool_calls import tool_result_message

    msg = tool_result_message("ollama", "file_manager", "call_1", "ok")
    copied = list(_copy_messages([msg]))
    assert copied[0].tool_name == "file_manager"
    assert copied[0].content == "ok"


def test_sdk_accepts_our_tool_specs():
    """Nuestro spec de tool pasa por ``_copy_tools`` real del SDK."""
    try:
        _copy_tools = _sdk_client_module()._copy_tools
    except ImportError:
        pytest.skip("ollama SDK not available")

    copied = list(_copy_tools(TOOLS_FIXTURE))
    assert len(copied) == 1
    assert copied[0].function.name == "file_manager"
