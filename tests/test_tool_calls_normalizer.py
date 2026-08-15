# tests/test_tool_calls_normalizer.py
"""Provider-aware tool call normalisation — unit tests.

Verifica que el normalizador maneja correctamente ambos formatos
(Ollama dict y OpenAI string) y nunca destruye datos válidos.
"""

import json
from unittest.mock import MagicMock

import pytest

from llm.tool_calls import (
    detect_provider_from_raw_tool_call,
    has_tool_association,
    is_valid_tool_call,
    model_supports_tool_calling,
    normalize_arguments,
    normalize_tool_call,
    sanitize_messages_for_ollama,
    tool_result_message,
)

# ---------------------------------------------------------------------------
# model_supports_tool_calling
# ---------------------------------------------------------------------------


def test_model_capabilities_known_unsupported(monkeypatch):
    """Un modelo declarado con tools=False no soporta tool calling."""
    from core.config import settings

    monkeypatch.setattr(
        settings,
        "model_capabilities",
        {"minimax-m3:cloud": {"tools": False}},
    )
    assert model_supports_tool_calling("minimax-m3:cloud") is False


def test_model_capabilities_known_supported(monkeypatch):
    """Un modelo declarado con tools=True soporta tool calling."""
    from core.config import settings

    monkeypatch.setattr(
        settings,
        "model_capabilities",
        {"qwen2.5-coder:7b": {"tools": True}},
    )
    assert model_supports_tool_calling("qwen2.5-coder:7b") is True


def test_model_capabilities_unknown_is_optimistic():
    """Modelos no listados se asumen capaces (default optimista)."""
    assert model_supports_tool_calling("modelo-desconocido") is True


def test_default_registry_does_not_disable_cloud_models():
    """gpt-oss:20b-cloud soporta tool calling nativo (docs oficiales Ollama:
    tag 'tools' + 'Agentic capabilities... function calling'). El registry
    por defecto NO debe marcarlo como sin tools — la evidencia original de
    'cloud rotos' era el bug json.loads(dict), ya corregido."""
    assert model_supports_tool_calling("gpt-oss:20b-cloud") is True


def test_default_registry_has_no_unsupported_entries():
    """Ninguna entrada por defecto marca tools:False — no existe un modelo
    verificado post-fix como incapaz; la degradación se detecta
    dinámicamente (repair loop + telemetría)."""
    from core.config import settings

    for model, caps in settings.model_capabilities.items():
        assert (
            caps.get("tools", True) is True
        ), f"'{model}' marcado tools:False sin evidencia post-fix"


# ---------------------------------------------------------------------------
# normalize_arguments
# ---------------------------------------------------------------------------


def test_normalize_dict_passthrough():
    """Dict arguments pass through unchanged (Ollama native format)."""
    args = {"action": "write", "path": "test.py", "content": "print(1)"}
    assert normalize_arguments(args) == args
    assert normalize_arguments(args) is args  # same object for dict


def test_normalize_string_json():
    """String JSON arguments (OpenAI format) are parsed correctly."""
    assert normalize_arguments('{"action": "write", "path": "x.py"}') == {
        "action": "write",
        "path": "x.py",
    }


def test_normalize_empty_string():
    """Empty string returns empty dict."""
    assert normalize_arguments("") == {}
    assert normalize_arguments("   ") == {}


def test_normalize_none():
    """None returns empty dict."""
    assert normalize_arguments(None) == {}


def test_normalize_invalid_json():
    """Invalid JSON string returns empty dict, does not raise."""
    assert normalize_arguments("{invalid json") == {}


def test_normalize_string_non_dict():
    """JSON that parses to non-dict returns empty dict."""
    assert normalize_arguments('"just a string"') == {}


def test_normalize_unexpected_type():
    """Unexpected type returns empty dict, does not raise."""
    assert normalize_arguments(42) == {}
    assert normalize_arguments([1, 2, 3]) == {}


def test_normalize_custom_default():
    """Custom default is returned on failure."""
    fallback = {"fallback": True}
    assert normalize_arguments(None, default=fallback) is fallback


# ---------------------------------------------------------------------------
# THE BUG — json.loads(dict) must not destroy valid Ollama arguments
# ---------------------------------------------------------------------------


def test_normalize_real_ollama_tool_call():
    """Simulate the exact bug: pydantic ToolCall with dict arguments
    (what the ollama SDK 0.6.1 returns).  json.loads(dict) → TypeError.
    normalize_arguments must handle this correctly."""
    # Build a real Message.ToolCall via the ollama SDK
    try:
        from ollama._types import Message
    except ImportError:
        pytest.skip("ollama SDK not available")

    msg = Message(
        role="assistant",
        content="",
        tool_calls=[
            {
                "function": {
                    "name": "file_manager",
                    "arguments": {
                        "action": "write",
                        "path": "hola.py",
                        "content": "print(1)",
                    },
                }
            }
        ],
    )
    tc = msg.tool_calls[0]

    # This is what loop.py:798-806 did BEFORE the fix
    # json.loads on dict → TypeError → data destroyed
    raw = tc.function.arguments
    assert isinstance(raw, dict), "SDK returns dict args"

    with pytest.raises(TypeError):
        json.loads(raw)  # the bug

    # normalize_arguments handles it correctly
    result = normalize_arguments(raw)
    assert result == {"action": "write", "path": "hola.py", "content": "print(1)"}
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# normalize_tool_call
# ---------------------------------------------------------------------------


def test_normalize_tool_call_from_pydantic():
    """normalize_tool_call handles pydantic Message.ToolCall correctly."""
    try:
        from ollama._types import Message
    except ImportError:
        pytest.skip("ollama SDK not available")

    msg = Message(
        role="assistant",
        content="",
        tool_calls=[
            {
                "function": {
                    "name": "file_manager",
                    "arguments": {"action": "read", "path": "main.py"},
                }
            }
        ],
    )
    tc = msg.tool_calls[0]

    result = normalize_tool_call(tc, index=0)
    assert result["name"] == "file_manager"
    assert result["arguments"] == {"action": "read", "path": "main.py"}
    assert result["id"] == "call_0"  # synthesised (ollama ToolCall has no id)


def test_normalize_tool_call_from_dict():
    """normalize_tool_call handles plain dict tool_calls (from accumulator)."""
    tc = {
        "id": "call_1_0",
        "function": {"name": "bash_manager", "arguments": '{"command": "ls"}'},
    }
    result = normalize_tool_call(tc, index=0)
    assert result["name"] == "bash_manager"
    assert result["id"] == "call_1_0"
    assert result["arguments"] == {"command": "ls"}


def test_normalize_tool_call_synthesizes_missing_id():
    """When tool call has no id, a synthetic one is generated."""
    result = normalize_tool_call({"function": {"name": "test_runner", "arguments": {}}}, index=5)
    assert result["id"] == "call_5"


# ---------------------------------------------------------------------------
# detect_provider_from_raw_tool_call
# ---------------------------------------------------------------------------


def test_detect_ollama_from_dict_args():
    """dict arguments → ollama."""
    try:
        from ollama._types import Message
    except ImportError:
        pytest.skip("ollama SDK not available")

    msg = Message(
        role="assistant",
        content="",
        tool_calls=[{"function": {"name": "f", "arguments": {"a": 1}}}],
    )
    assert detect_provider_from_raw_tool_call(msg.tool_calls[0]) == "ollama"


def test_detect_openai_from_string_args():
    """string arguments → openai (via mock)."""
    tc = MagicMock()
    tc.function = MagicMock()
    tc.function.arguments = '{"a": 1}'
    assert detect_provider_from_raw_tool_call(tc) == "openai"


# ---------------------------------------------------------------------------
# tool_result_message
# ---------------------------------------------------------------------------


def test_tool_result_ollama_format():
    msg = tool_result_message("ollama", "file_manager", "call_1", "result")
    assert msg["role"] == "tool"
    assert "tool_name" in msg
    assert msg["tool_name"] == "file_manager"
    assert "tool_call_id" not in msg


def test_tool_result_openai_format():
    msg = tool_result_message("openai", "file_manager", "call_1", "result")
    assert msg["role"] == "tool"
    assert "tool_call_id" in msg
    assert msg["tool_call_id"] == "call_1"
    assert "tool_name" not in msg


# ---------------------------------------------------------------------------
# sanitize_messages_for_ollama
# ---------------------------------------------------------------------------


def test_sanitize_converts_string_args():
    messages = [
        {"role": "system", "content": "hi"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "file_manager",
                        "arguments": '{"action": "write", "path": "x.py"}',
                    },
                }
            ],
        },
    ]
    result = sanitize_messages_for_ollama(messages)
    assert isinstance(result[1]["tool_calls"][0]["function"]["arguments"], dict)
    assert result[1]["tool_calls"][0]["function"]["arguments"] == {
        "action": "write",
        "path": "x.py",
    }


def test_sanitize_does_not_mutate_original():
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "function": {"name": "f", "arguments": '{"a": 1}'},
                }
            ],
        },
    ]
    original = messages[0]["tool_calls"][0]["function"]["arguments"]
    result = sanitize_messages_for_ollama(messages)
    # Original must still be string
    assert messages[0]["tool_calls"][0]["function"]["arguments"] == '{"a": 1}'
    assert isinstance(original, str)
    # Sanitised must be dict
    assert isinstance(result[0]["tool_calls"][0]["function"]["arguments"], dict)


def test_sanitize_handles_dict_already():
    """Sanitize is a no-op when args are already dict."""
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "function": {"name": "f", "arguments": {"a": 1}},
                }
            ],
        },
    ]
    result = sanitize_messages_for_ollama(messages)
    assert result[0]["tool_calls"][0]["function"]["arguments"] == {"a": 1}


def test_sanitize_via_real_sdk():
    """sanitize_messages_for_ollama → _copy_messages must not raise ValidationError."""
    try:
        from ollama._client import _copy_messages
    except ImportError:
        pytest.skip("ollama SDK not available")

    # Build a message with string args (OpenAI format)
    mixed = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "file_manager",
                        "arguments": '{"action": "write", "path": "x.py"}',
                    },
                }
            ],
        }
    ]
    # Without sanitize, _copy_messages raises ValidationError
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        list(_copy_messages(mixed))

    # After sanitize, it must succeed
    safe = sanitize_messages_for_ollama(mixed)
    result = list(_copy_messages(safe))
    assert len(result) == 1


# ---------------------------------------------------------------------------
# is_valid_tool_call
# ---------------------------------------------------------------------------


def test_is_valid_with_real_args():
    assert is_valid_tool_call({"arguments": {"action": "write", "path": "x.py"}})


def test_is_valid_with_empty_args():
    assert not is_valid_tool_call({"arguments": {"action": "", "path": ""}})


def test_is_valid_with_only_injected():
    assert not is_valid_tool_call({"arguments": {"project_root": "/tmp", "workspace": "main"}})


def test_is_valid_with_mixed():
    assert is_valid_tool_call({"arguments": {"action": "write", "project_root": "/tmp"}})


# ---------------------------------------------------------------------------
# has_tool_association
# ---------------------------------------------------------------------------


def test_has_association_with_tool_call_id():
    assert has_tool_association({"role": "tool", "tool_call_id": "c1"})
    assert not has_tool_association({"role": "tool", "tool_call_id": ""})
    assert not has_tool_association({"role": "tool", "tool_call_id": None})


def test_has_association_with_tool_name():
    assert has_tool_association({"role": "tool", "tool_name": "file_manager"})
    assert not has_tool_association({"role": "tool", "tool_name": ""})


def test_has_association_with_neither():
    assert not has_tool_association({"role": "tool", "content": "orphan"})
