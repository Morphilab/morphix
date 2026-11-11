"""bots_dispatch — cuerpo del chat canónico de bots (dispatch, pausa,
finalize) y anotación de menciones, ejecutado por el orquestador como
ruta delegada: el orquestador detecta la ruta, este módulo la ejecuta."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestration.context import PAUSED_MARKER, WorkflowEvents


def _events() -> WorkflowEvents:
    return WorkflowEvents()


def _ctx(conv_id: int = 5) -> MagicMock:
    ctx = MagicMock()
    ctx.conversation_id = conv_id
    ctx.workspace = "main"
    ctx.query = "q"
    ctx.last_clarification = None
    return ctx


# ── annotate_query_mentions ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_annotate_query_mentions_sin_roster_no_toca_nada():
    from orchestration.bots_dispatch import annotate_query_mentions

    ctx = _ctx()
    with patch(
        "orchestration.bots_dispatch.bots_roster_safe", new_callable=AsyncMock, return_value=[]
    ):
        out = await annotate_query_mentions("hola @alfa", ctx)
    assert out == "hola @alfa"
    assert ctx.query == "q"


@pytest.mark.asyncio
async def test_annotate_query_mentions_anota_y_sincroniza_ctx():
    from orchestration.bots_dispatch import annotate_query_mentions

    ctx = _ctx()
    roster = [{"slug": "alfa"}, {"slug": "beta"}]
    with patch(
        "orchestration.bots_dispatch.bots_roster_safe",
        new_callable=AsyncMock,
        return_value=roster,
    ):
        out = await annotate_query_mentions("hola @alfa", ctx)
    assert "[mención @alfa" in out
    assert ctx.query == out


@pytest.mark.asyncio
async def test_annotate_query_mentions_roto_degrada_a_query_intacta():
    """El middleware de anotación jamás rompe el turno: si el roster falla,
    la query sigue intacta (identification-only, nunca bloquea)."""
    from orchestration.bots_dispatch import annotate_query_mentions

    ctx = _ctx()
    with patch(
        "orchestration.bots_dispatch.bots_roster_safe",
        new_callable=AsyncMock,
        side_effect=RuntimeError("bd caída"),
    ):
        out = await annotate_query_mentions("hola", ctx)
    assert out == "hola"


# ── dispatch_canonical_turn ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_canonical_turn_llama_runner_con_transport_canonical():
    from orchestration.bots_dispatch import dispatch_canonical_turn

    with (
        patch(
            "orchestration.bots_runner.run_bot_turn",
            new_callable=AsyncMock,
            return_value="respuesta del bot",
        ) as mock_turn,
        patch("orchestration.bots_dispatch.finalize_workflow", new_callable=AsyncMock),
        patch("orchestration.bots_dispatch.emit_system", new_callable=AsyncMock),
    ):
        out = await dispatch_canonical_turn(
            owner={"slug": "alfa"},
            query="hola",
            ctx=_ctx(conv_id=5),
            events=_events(),
            start_time=0.0,
            persist=True,
        )
    assert out == "respuesta del bot"
    assert mock_turn.await_args.args[0] == "alfa"
    assert mock_turn.await_args.args[2] == 5
    assert mock_turn.await_args.kwargs["transport"] == "canonical"


@pytest.mark.asyncio
async def test_canonical_turn_persist_true_finaliza_con_scorecard_bot():
    from orchestration.bots_dispatch import dispatch_canonical_turn

    with (
        patch(
            "orchestration.bots_runner.run_bot_turn",
            new_callable=AsyncMock,
            return_value="respuesta del bot",
        ),
        patch(
            "orchestration.bots_dispatch.finalize_workflow", new_callable=AsyncMock
        ) as mock_final,
        patch("orchestration.bots_dispatch.emit_system", new_callable=AsyncMock),
    ):
        out = await dispatch_canonical_turn(
            owner={"slug": "alfa"},
            query="hola",
            ctx=_ctx(conv_id=5),
            events=_events(),
            start_time=0.0,
            persist=True,
        )
    assert out == "respuesta del bot"
    mock_final.assert_awaited_once()
    assert mock_final.await_args.kwargs["scorecard"]["tipo_tarea"] == "bot_canonical_chat"
    assert mock_final.await_args.kwargs["conversation_id"] == 5
    assert mock_final.await_args.kwargs["query"] == "hola"


@pytest.mark.asyncio
async def test_canonical_turn_persist_false_no_finaliza():
    """persist=False (transporte machine-local): la persistencia del turno
    corre por su cuenta (persist_turn_exchange) — aquí no se duplica."""
    from orchestration.bots_dispatch import dispatch_canonical_turn

    with (
        patch(
            "orchestration.bots_runner.run_bot_turn",
            new_callable=AsyncMock,
            return_value="x",
        ),
        patch(
            "orchestration.bots_dispatch.finalize_workflow", new_callable=AsyncMock
        ) as mock_final,
        patch("orchestration.bots_dispatch.emit_system", new_callable=AsyncMock),
    ):
        out = await dispatch_canonical_turn(
            owner={"slug": "alfa"},
            query="hola",
            ctx=_ctx(),
            events=_events(),
            start_time=0.0,
            persist=False,
        )
    assert out == "x"
    mock_final.assert_not_awaited()


@pytest.mark.asyncio
async def test_canonical_turn_sin_respuesta_es_accionable():
    from orchestration.bots_dispatch import dispatch_canonical_turn

    with (
        patch(
            "orchestration.bots_runner.run_bot_turn",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("orchestration.bots_dispatch.finalize_workflow", new_callable=AsyncMock),
        patch("orchestration.bots_dispatch.emit_system", new_callable=AsyncMock) as mock_emit,
    ):
        out = await dispatch_canonical_turn(
            owner={"slug": "alfa"},
            query="hola",
            ctx=_ctx(),
            events=_events(),
            start_time=0.0,
            persist=True,
        )
    assert "no produjo respuesta" in str(out)
    # 2 emits: el aviso de identidad al entrar + el error accionable
    assert mock_emit.await_count == 2
    assert "no produjo respuesta" in mock_emit.await_args_list[1].args[1]


@pytest.mark.asyncio
async def test_canonical_turn_clarification_pausa_con_origin_bot():
    from orchestration.bots_dispatch import dispatch_canonical_turn

    final = {
        "status": "clarification_needed",
        "clarification_question": "¿confirmas?",
        "clarification_options": ["Sí", "No"],
        "paused_loop_state": {"messages": []},
        "bot_slug": "alfa",
    }
    ctx = _ctx()
    with (
        patch(
            "orchestration.bots_runner.run_bot_turn",
            new_callable=AsyncMock,
            return_value=final,
        ),
        patch("orchestration.pauses.save_paused_session", new_callable=AsyncMock) as mock_save,
        patch("orchestration.bots_dispatch.emit_system", new_callable=AsyncMock),
    ):
        out = await dispatch_canonical_turn(
            owner={"slug": "alfa"},
            query="hola",
            ctx=ctx,
            events=_events(),
            start_time=0.0,
            persist=True,
        )
    assert out == PAUSED_MARKER
    mock_save.assert_awaited_once()
    assert mock_save.await_args.kwargs["paused_state"]["origin"] == "bot"
    assert mock_save.await_args.kwargs["paused_state"]["bot_slug"] == "alfa"
    assert ctx.last_clarification == "¿confirmas?"
