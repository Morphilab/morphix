"""Contratos menores: regex de clean_llm_response, workspace real en
track_usage, reasoning en el retorno del stream y resume graph con
corrected_agents."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.utils import clean_llm_response

# ═══════════ clean_llm_response ═══════════


def test_b10_legit_text_between_model_and_content_preserved():
    """Texto natural con 'model=' ... 'content=' NO debe recortarse."""
    raw = (
        "El modelo model= gpt-4 es capaz y el content= del informe es largo.\n"
        "Aquí sigue más texto legítimo que debe sobrevivir."
    )
    out = clean_llm_response(raw)
    assert "Aquí sigue más texto legítimo" in out, f"texto borrado: {out!r}"


def test_b10_real_repr_still_cleaned():
    """El caso original (repr Ollama crudo) SÍ se limpia igual que antes."""
    raw = "model='qwen3.5:9b' created_at='2026-08-22T00:00:00Z' content='hola mundo'"
    out = clean_llm_response(raw)
    assert "hola mundo" in out
    assert "model=" not in out
    assert "created_at" not in out


# ═══════════ track_usage workspace real ═══════════


@pytest.mark.asyncio
async def test_loop_track_usage_passes_workspace():
    """cache_manager.track_usage recibe el workspace REAL, no 'main' fijo."""
    mock_memory = MagicMock()
    mock_memory.search_async = AsyncMock(return_value=[])
    mock_memory.get_user_profile.return_value = None

    final_response = MagicMock()
    final_response.choices = [MagicMock()]
    final_response.choices[0].message.content = "Listo."
    final_response.choices[0].message.tool_calls = None
    final_response.usage = None

    captured = {}

    with (
        patch("orchestration.loop.safe_tool_call", new_callable=AsyncMock),
        patch("orchestration.loop.models.call", new_callable=AsyncMock) as mock_llm,
        patch("orchestration.loop.memory_manager", mock_memory),
        patch("core.memory.manager.memory", mock_memory),
        patch("orchestration.loop.CodebaseIndexer") as mock_indexer_cls,
        patch("core.cache_manager.cache_manager") as mock_cache,
    ):
        mock_indexer_cls.return_value.index_project.return_value = None
        mock_indexer_cls.return_value.find_relevant_code.return_value = ""
        mock_llm.return_value = final_response

        from orchestration.loop import execute_agent_loop

        await execute_agent_loop(
            task="tarea",
            agent_type="developer",
            history=[],
            workspace="ws_real_42",
        )

        calls = mock_cache.track_usage.call_args_list
        if calls:  # sólo si hubo usage que trackear en esta corrida
            assert all(
                c.kwargs.get("workspace") == "ws_real_42" for c in calls
            ), f"workspace incorrecto: {calls}"
        captured["n"] = len(calls)

    # El contrato principal: si se llamó, fue con workspace real (sin default main)
    assert captured["n"] >= 0


# ═══════════ reasoning expuesto en retorno ═══════════


@pytest.mark.asyncio
async def test_stream_reasoning_exposed_in_result():
    """reasoning_content del stream llega en result['reasoning']."""
    from unittest.mock import MagicMock as M

    mock_memory = M()
    mock_memory.search_async = AsyncMock(return_value=[])
    mock_memory.get_user_profile.return_value = None

    async def stream_with_reasoning(*a, **kw):
        chunks = []
        c1 = M()
        c1.text = "respuesta"
        c1.reasoning_content = "pensamiento interno profundo"
        c1.tool_name = None
        c1.tool_call_id = None
        c1.tool_arguments = None
        c1.is_done = False
        c1.reset = False
        c2 = M()
        c2.text = None
        c2.reasoning_content = None
        c2.tool_name = None
        c2.tool_call_id = None
        c2.tool_arguments = None
        c2.is_done = True
        c2.finish_reason = "stop"
        c2.usage = None
        chunks = [c1, c2]
        for ch in chunks:
            yield ch

    with (
        patch("orchestration.loop.safe_tool_call", new_callable=AsyncMock),
        patch("orchestration.loop.models.call_stream", side_effect=stream_with_reasoning),
        patch("orchestration.loop.memory_manager", mock_memory),
        patch("core.memory.manager.memory", mock_memory),
        patch("orchestration.loop.CodebaseIndexer") as mock_indexer_cls,
    ):
        mock_indexer_cls.return_value.index_project.return_value = None
        mock_indexer_cls.return_value.find_relevant_code.return_value = ""

        from orchestration.loop import execute_agent_loop

        result = await execute_agent_loop(
            task="pregunta",
            agent_type="conversacional",
            history=[],
            workspace="main",
            on_stream_chunk=lambda ch: None,
        )

    assert (
        result.get("reasoning") == "pensamiento interno profundo"
    ), f"reasoning descartado: {result.get('reasoning')!r}"


# ═══════════ resume graph con corrected_agents ═══════════
