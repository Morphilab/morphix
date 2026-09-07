"""Saneo de pares tool_calls/result tras compresión de historial."""

from llm.tool_calls import sanitize_tool_message_pairs


def test_orphan_tool_result_dropped():
    # Simula un corte de compress que dejó FUERA al assistant con el tool_call:
    msgs = [
        {"role": "user", "content": "hola"},
        {"role": "user", "content": "sigue"},
        {"role": "tool", "tool_call_id": "c1", "content": "resultado huérfano"},
    ]
    out = sanitize_tool_message_pairs(msgs)
    assert not any(m.get("role") == "tool" for m in out), "huérfano llegó al provider"


def test_valid_pairs_survive():
    msgs = [
        {"role": "user", "content": "u"},
        {"role": "assistant", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "r"},
    ]
    out = sanitize_tool_message_pairs(msgs)
    assert len(out) == 3


def test_base_applies_sanitizer_after_compression():
    """Guard de orden: base.py sanea DESPUÉS de la compresión de historial y ANTES de
    cualquier uso de messages por el provider.

    La compresión es cache-first (cache_friendly_compress → stabilize_messages,
    absorción deepseek), por lo que el guard acepta ambas implementaciones.
    """
    from pathlib import Path

    src = Path("agents/base.py").read_text(encoding="utf-8")
    i_compress_stable = src.find("cache_friendly_compress(history")
    i_compress_legacy = src.find("compress_history(history")
    i_sanitize = src.find("sanitize_tool_message_pairs(messages)")
    assert i_sanitize != -1
    i_compress = i_compress_stable if i_compress_stable != -1 else i_compress_legacy
    assert i_compress != -1, "base.py debe comprimir el historial (cache-first o clásico)"
    assert i_compress < i_sanitize, "el saneo debe ir tras la compresión"
