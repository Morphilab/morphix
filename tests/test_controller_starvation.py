# tests/test_controller_starvation.py
"""Tests del retry por reasoning-starvation en ModelsController.call.

Bug 2026-08-15: cuando el caller pasa max_tokens (p. ej. Safety Net con
max_tokens=4000), el retry B3 dobla effective_max_tokens pero call_kwargs.update(kwargs)
re-aplica el valor del caller → el reintento nunca crece el presupuesto.
"""

from unittest.mock import MagicMock, patch

import pytest

from llm.controller import models


def _make_response(finish_reason: str, content: str | None) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].finish_reason = finish_reason
    resp.choices[0].message.content = content
    resp.choices[0].message.tool_calls = None
    resp.usage = None
    return resp


@pytest.mark.asyncio
async def test_starvation_retry_doubles_caller_max_tokens():
    """Si el caller pasa max_tokens=4000 y hay starvation, el reintento debe
    usar el doble (8000), no re-aplicar 4000."""
    from openai import OpenAI

    client = OpenAI(api_key="test-key")
    starved = _make_response("length", None)
    valid = _make_response("stop", "hola")

    with (
        patch.object(
            client.chat.completions, "create", side_effect=[starved, valid]
        ) as mock_create,
        patch(
            "llm.controller.LLMProvider.get_client", return_value=(client, "deepseek-v4-flash", 0.7)
        ),
        patch("llm.controller.LLMProvider.get_provider_name", return_value="deepseek"),
        patch("core.circuit_breaker.CircuitBreakerRegistry.get"),
    ):
        resp = await models.call(
            [{"role": "user", "content": "hi"}],
            role="fast",
            max_tokens=4000,
            max_retries=2,
        )

    assert resp is valid
    assert mock_create.call_count == 2
    second_kwargs = mock_create.call_args_list[1].kwargs
    assert second_kwargs["max_tokens"] == 8000


@pytest.mark.asyncio
async def test_no_caller_max_tokens_retry_doubles_role_value():
    """Sin override del caller, el retry dobla el max_tokens del rol."""
    from openai import OpenAI

    client = OpenAI(api_key="test-key")
    starved = _make_response("length", None)
    valid = _make_response("stop", "hola")

    with (
        patch.object(
            client.chat.completions, "create", side_effect=[starved, valid]
        ) as mock_create,
        patch(
            "llm.controller.LLMProvider.get_client", return_value=(client, "deepseek-v4-flash", 0.7)
        ),
        patch("llm.controller.LLMProvider.get_provider_name", return_value="deepseek"),
        patch("core.circuit_breaker.CircuitBreakerRegistry.get"),
    ):
        resp = await models.call(
            [{"role": "user", "content": "hi"}],
            role="fast",
            max_retries=2,
        )

    assert resp is valid
    assert mock_create.call_count == 2
    second_kwargs = mock_create.call_args_list[1].kwargs
    assert second_kwargs["max_tokens"] == 2048


@pytest.mark.asyncio
async def test_ollama_stream_serializes_empty_args_dict():
    """Un tool call Ollama con arguments={} debe serializarse como '{}' (no
    None): así el accumulador downstream lo procesa y el repair loop actúa
    (bug 2026-08-15: json.dumps(raw_args) if raw_args → {} falsy → None)."""
    from llm.controller import models as _models

    client = MagicMock()

    def _chunk(done: bool, tool_call: dict | None = None):
        msg = {"content": None, "tool_calls": [tool_call] if tool_call else None}
        c = MagicMock()
        c.get = lambda k, default=None: (
            {"done": done, "done_reason": "stop" if done else None, "message": msg}.get(k, default)
        )
        return c

    client.chat.return_value = iter(
        [
            _chunk(False, {"function": {"name": "file_manager", "arguments": {}}}),
            _chunk(True),
        ]
    )

    chunks = [c async for c in _models._stream_ollama(client, "m", [], 0.7)]
    tool_chunks = [c for c in chunks if c.tool_name == "file_manager"]
    assert len(tool_chunks) == 1
    assert tool_chunks[0].tool_arguments == "{}"


@pytest.mark.asyncio
async def test_ollama_stream_keeps_none_args_as_none():
    """Sin arguments (None) el chunk no lleva tool_arguments — el repair
    downstream lo detecta igual."""
    from llm.controller import models as _models

    client = MagicMock()

    def _chunk(done: bool, tool_call: dict | None = None):
        msg = {"content": None, "tool_calls": [tool_call] if tool_call else None}
        c = MagicMock()
        c.get = lambda k, default=None: (
            {"done": done, "done_reason": "stop" if done else None, "message": msg}.get(k, default)
        )
        return c

    client.chat.return_value = iter(
        [
            _chunk(False, {"function": {"name": "file_manager", "arguments": None}}),
            _chunk(True),
        ]
    )

    chunks = [c async for c in _models._stream_ollama(client, "m", [], 0.7)]
    tool_chunks = [c for c in chunks if c.tool_name == "file_manager"]
    assert len(tool_chunks) == 1
    assert tool_chunks[0].tool_arguments is None


@pytest.mark.asyncio
async def test_ollama_starvation_retry_grows_num_predict():
    """Path Ollama: el reintento debe crecer num_predict, no quedarse en el
    valor del caller."""
    client = MagicMock()
    starved = MagicMock()
    starved.usage = None
    starved_msg = {"content": None, "tool_calls": None}
    starved.get = lambda k, default=None: (
        {"done_reason": "length", "message": starved_msg}.get(k, default)
    )

    valid = MagicMock()
    valid.usage = None
    valid_msg = {"content": "hola", "tool_calls": None}
    valid.get = lambda k, default=None: (
        {"done_reason": "stop", "message": valid_msg}.get(k, default)
    )
    valid.choices = None

    with (
        patch.object(client, "chat") as mock_chat,
        patch(
            "llm.controller.LLMProvider.get_client", return_value=(client, "gpt-oss:20b-cloud", 0.7)
        ),
        patch("llm.controller.LLMProvider.get_provider_name", return_value="ollama"),
        patch("core.circuit_breaker.CircuitBreakerRegistry.get"),
    ):
        mock_chat.side_effect = [starved, valid]
        resp = await models.call(
            [{"role": "user", "content": "hi"}],
            role="fast",
            max_tokens=4000,
            max_retries=2,
        )

    assert mock_chat.call_count == 2
    second_options = mock_chat.call_args_list[1].kwargs["options"]
    assert second_options["num_predict"] == 8000
    assert resp is not None
