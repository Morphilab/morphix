"""llm/tool_calls.py — Provider-aware tool call normalization layer.

Single source of truth for all tool call parsing, serialization, and
validation across both Ollama (native dict-args) and OpenAI (string-args)
providers.  Transport-agnostic — designed for the SDK first, swappable
to a raw HTTP transport in the future without touching loop/orchestrator.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_INJECTED_KEYS = {"project_root", "workspace"}


# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------


def detect_provider_from_raw_tool_call(tc: Any) -> str:
    """Detect provider kind from a *raw* tool-call object.

    Inspects ``function.arguments``: if the value is a ``dict`` the
    provider is ``"ollama"`` (native Ollama format); if it is ``str`` it
    is ``"openai"`` (OpenAI / DeepSeek / Grok format).

    Handles pydantic ``Message.ToolCall`` objects, plain dicts, and
    OpenAI SDK objects.  Returns ``"openai"`` as the safe default when
    detection is ambiguous.
    """
    if tc is None:
        return "openai"

    func = getattr(tc, "function", None)

    if func is None and isinstance(tc, dict):
        func = tc.get("function", {})

    if isinstance(func, dict):
        raw = func.get("arguments", {})
    elif func is not None:
        raw = getattr(func, "arguments", None)
        if raw is None and hasattr(func, "get"):
            try:
                raw = func.get("arguments", {})
            except (TypeError, AttributeError):
                raw = {}
    else:
        return "openai"

    return "ollama" if isinstance(raw, dict) else "openai"


# ---------------------------------------------------------------------------
# Model capability registry (synthesis 3.2)
# ---------------------------------------------------------------------------


def model_supports_tool_calling(model_name: str) -> bool:
    """Return *True* if *model_name* is known to support native tool calling.

    Reads the ``settings.model_capabilities`` registry.  Unknown models
    are assumed capable (optimistic default) — degradation is detected
    dynamically via the repair telemetry (3.4) instead.
    """
    from core.config import settings

    capabilities = settings.model_capabilities.get(model_name)
    if capabilities is None:
        return True
    return bool(capabilities.get("tools", True))


def log_raw_tool_args(context: str, raw: Any) -> None:
    """Log RAW tool arguments at DEBUG level, behind ``VERBOSE_LOGGING``.

    Used in ``_stream_ollama`` and the non-streaming parsing to capture
    the exact type/value the provider sent — the diagnostic that would
    have exposed the original ``json.loads(dict)`` bug immediately.
    """
    from core.config import settings

    if not settings.verbose_logging:
        return
    logger.debug(
        "RAW tool args [%s] type=%s value=%s",
        context,
        type(raw).__name__,
        repr(raw)[:500],
    )


# ---------------------------------------------------------------------------
# Argument normalisation — the core fix (never destroys data)
# ---------------------------------------------------------------------------


def normalize_arguments(raw: Any, *, default: dict | None = None) -> dict:
    """Normalise tool-call arguments to a ``dict``.  **Never throws.**

    Accepts:
    * ``dict`` — Ollama native format (pass-through).
    * ``str``  — OpenAI JSON string (parsed).
    * ``None`` — empty call (returns *default*).

    Unexpected types are logged at ``DEBUG`` and *default* is returned.
    """
    if default is None:
        default = {}

    # Fast path — Ollama native
    if isinstance(raw, dict):
        return raw

    # OpenAI/DeepSeek path
    if isinstance(raw, str):
        if not raw.strip():
            return default
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
            logger.debug(
                "normalize_arguments: parsed non-dict %s → %s",
                type(parsed).__name__,
                repr(parsed)[:200],
            )
            return default
        except (json.JSONDecodeError, TypeError) as exc:
            logger.debug(
                "normalize_arguments: JSON parse failed: %s  " "(raw: %s...)", exc, str(raw)[:200]
            )
            return default

    if raw is None:
        return default

    # Catch-all for unexpected types
    logger.debug(
        "normalize_arguments: unexpected type %s value=%s", type(raw).__name__, repr(raw)[:200]
    )
    return default


# ---------------------------------------------------------------------------
# Tool-call object normalisation — standard internal dict
# ---------------------------------------------------------------------------


def normalize_tool_call(tc: Any, index: int = 0) -> dict:
    """Convert *any* tool-call representation to a canonical internal dict.

    Returns ``{"name": str, "id": str, "arguments": dict}``.

    ``name`` and ``id`` are never empty — a synthetic ``id`` is generated
    when the provider omits it (Ollama native format has no ``id`` field).

    Accepted input shapes:

    * Pydantic ``ollama.Message.ToolCall`` (has ``.function``, no ``id``).
    * OpenAI SDK tool-call object (``.function``, ``.id``).
    * Plain ``dict`` from ``_accumulate_stream``.
    """
    func = getattr(tc, "function", None)

    if func is None and isinstance(tc, dict):
        func = tc.get("function", {})

    # -- name --
    if isinstance(func, dict):
        name = str(func.get("name", ""))
    elif func is not None:
        name = str(getattr(func, "name", ""))
        if not name and hasattr(func, "get"):
            try:
                name = str(func.get("name", ""))
            except (TypeError, AttributeError):
                pass
    else:
        name = ""

    # -- id (synthesised when missing) --
    call_id = ""
    if isinstance(tc, dict):
        call_id = tc.get("id", "")
    else:
        call_id = getattr(tc, "id", "")
        if not call_id and hasattr(tc, "get"):
            try:
                call_id = tc.get("id", "")
            except (TypeError, AttributeError):
                pass
    call_id = call_id or f"call_{index}"

    # -- arguments (via the normalised, never-throw path) --
    if isinstance(func, dict):
        raw_args = func.get("arguments", {})
    elif func is not None:
        raw_args = getattr(func, "arguments", None)
        if raw_args is None and hasattr(func, "get"):
            try:
                raw_args = func.get("arguments", {})
            except (TypeError, AttributeError):
                raw_args = {}
    else:
        raw_args = {}

    return {
        "name": name,
        "id": str(call_id),
        "arguments": normalize_arguments(raw_args),
    }


# ---------------------------------------------------------------------------
# Tool-result message — provider-native format
# ---------------------------------------------------------------------------


def tool_result_message(provider_kind: str, name: str, call_id: str, content: str) -> dict:
    """Build a tool-result message in the provider's native format.

    +---------------+--------------------------------------------------+
    | Ollama        | ``{"role":"tool", "tool_name":name, "content":…}``|
    +---------------+--------------------------------------------------+
    | OpenAI / etc. | ``{"role":"tool", "tool_call_id":id, "content":…}``|
    +---------------+--------------------------------------------------+
    """
    if provider_kind == "ollama":
        return {"role": "tool", "tool_name": name, "content": content}
    return {"role": "tool", "tool_call_id": call_id, "content": content}


# ---------------------------------------------------------------------------
# History sanitisation — prevents ValidationError in Ollama SDK
# ---------------------------------------------------------------------------


def sanitize_messages_for_ollama(messages: list) -> list[dict]:
    """Convert string-args tool_calls to dict-args for Ollama SDK compat.

    The Ollama SDK 0.6.1 validates ``function.arguments`` as
    ``Mapping[str, Any]``.  Passing a JSON string causes a
    ``ValidationError`` that kills the entire ``client.chat()`` call.

    This mirrors the NB3 conversion in ``llm/controller.py:236-249``
    but is designed to be called universally — from non-streaming,
    streaming, and pause/resume paths.

    Returns a **new** list with deep-copied tool_calls; does not
    mutate the input.
    """
    import copy

    sanitized: list[dict] = []
    for msg in messages:
        copy_msg = dict(msg)
        tool_calls = copy_msg.get("tool_calls")
        if tool_calls:
            tool_calls = copy_msg["tool_calls"] = copy.deepcopy(tool_calls)
            for tc in tool_calls:
                func = tc.get("function", {}) if isinstance(tc, dict) else {}
                raw = func.get("arguments") if isinstance(func, dict) else None
                if isinstance(raw, str):
                    try:
                        func["arguments"] = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        func["arguments"] = {}
        sanitized.append(copy_msg)
    return sanitized


# ---------------------------------------------------------------------------
# Tool-call validation (non-empty, non-injected)
# ---------------------------------------------------------------------------


def is_valid_tool_call(tc: dict) -> bool:
    """Return *True* if *tc* has at least one non-injected, truthy argument.

    ``project_root`` and ``workspace`` are injected server-side and do
    **not** count as user-provided valid parameters.
    """
    args = tc.get("arguments", {})
    if not isinstance(args, dict):
        return False
    for key, val in args.items():
        if key not in _INJECTED_KEYS and val:
            return True
    return False


# ---------------------------------------------------------------------------
# Filter helpers — keep tool messages for both providers
# ---------------------------------------------------------------------------


def has_tool_association(msg: dict) -> bool:
    """Return *True* if *msg* (role==``"tool"``) has a provider-native
    association field — ``tool_call_id`` (OpenAI) *or* ``tool_name``
    (Ollama)."""
    return bool(msg.get("tool_call_id") or msg.get("tool_name"))
