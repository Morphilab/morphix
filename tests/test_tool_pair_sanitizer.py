"""Sanitización de pares tool_calls/result y huérfanos del loop."""

from orchestration.loop import _sanitize_tool_message_pairs


def test_sanitizer_drops_orphan_pairs():
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "tool_calls": [{"id": "c1"}]},  # huérfano
        {"role": "user", "content": "u"},
        {
            "role": "assistant",
            "tool_calls": [{"id": "c2"}],
        },
        {"role": "tool", "tool_call_id": "c2", "content": "ok"},
        {"role": "tool", "tool_call_id": "c3", "content": "huérfano"},
    ]
    out = _sanitize_tool_message_pairs(msgs)
    ids_with_calls = {
        tc["id"] for m in out if m["role"] == "assistant" for tc in m.get("tool_calls") or []
    }
    ids_with_results = {m["tool_call_id"] for m in out if m["role"] == "tool"}
    assert ids_with_calls == ids_with_results == {"c2"}


def test_sanitizer_keeps_clean_history_intact():
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "respuesta"},
    ]
    out = _sanitize_tool_message_pairs(msgs)
    assert out == msgs
