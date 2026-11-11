# tests/test_context_prune.py — poda de tool results antiguos
"""El transcript re-envía todos los tool results en cada iteración: en
runs largos el coste por iteración crece con el acumulado. La poda trunca
los resultados fuera de la ventana reciente sin romper el protocolo
(cada tool_call conserva su mensaje de resultado)."""

from orchestration.loop import (
    _TOOL_RESULT_MAX_CHARS,
    _TOOL_RESULT_PRUNE_THRESHOLD_CHARS,
    _TOOL_RESULT_PRUNED_MARK,
    _prune_old_tool_results,
)


def _mensajes(num_tools: int, chars: int) -> list[dict]:
    msgs: list[dict] = [{"role": "user", "content": "tarea"}]
    for i in range(num_tools):
        msgs.append({"role": "assistant", "content": None, "tool_calls": []})
        msgs.append(
            {
                "role": "tool",
                "tool_call_id": f"call_{i}",
                "content": "x" * chars,
            }
        )
    return msgs


def test_bajo_umbral_no_poda():
    msgs = _mensajes(10, chars=1000)
    assert _prune_old_tool_results(msgs) == 0
    assert all(len(m["content"]) == 1000 for m in msgs if m["role"] == "tool")


def test_sobre_umbral_poda_solo_los_antiguos():
    msgs = _mensajes(12, chars=_TOOL_RESULT_PRUNE_THRESHOLD_CHARS // 10)
    podados = _prune_old_tool_results(msgs)
    tools = [m for m in msgs if m["role"] == "tool"]
    assert podados == len(tools) - 6  # se conservan los últimos 6
    # últimos intactos
    assert all(len(m["content"]) > _TOOL_RESULT_MAX_CHARS for m in tools[-6:])
    # antiguos truncados con marcador
    assert all(_TOOL_RESULT_PRUNED_MARK in m["content"] for m in tools[:-6])
    # ningún mensaje eliminado: cada tool_call conserva su resultado
    assert len(msgs) == 1 + 12 * 2


def test_poda_es_idempotente():
    msgs = _mensajes(12, chars=_TOOL_RESULT_PRUNE_THRESHOLD_CHARS // 10)
    assert _prune_old_tool_results(msgs) > 0
    assert _prune_old_tool_results(msgs) == 0


def test_no_elimina_mensajes_y_mantiene_orden():
    msgs = _mensajes(10, chars=_TOOL_RESULT_PRUNE_THRESHOLD_CHARS // 8)
    ids_antes = [m.get("tool_call_id") for m in msgs if m["role"] == "tool"]
    _prune_old_tool_results(msgs)
    ids_despues = [m.get("tool_call_id") for m in msgs if m["role"] == "tool"]
    assert ids_antes == ids_despues
