# tests/test_resume_paths.py — cobertura de las rutas de resume
"""Cobertura de las rutas de recuperación de pausas: get_unresolved_pause
(recovery GUI), resume_workflow con SELECT real (marca resolved + answer),
origen bot (_resume_bot_turn e2e con identidad del bot canónico), bot
desaparecido y rechazo del legacy retirado."""

import datetime
import json
import secrets
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.checkpoint import CHECKPOINT_QUESTION_PREFIX
from orchestration.workflows.orchestrator import WorkflowOrchestrator

_pg = pytest.mark.skipif(
    not __import__("os").environ.get("DATABASE_URL"), reason="requiere DATABASE_URL (PG real)"
)


def _session(conv_id: int | None, workspace: str):
    ctx = SimpleNamespace(
        query="tarea",
        active_workflow="demo_dsl",
        conversation_history=[],
        conversation_id=conv_id,
        project_root=".",
        allowed_tools=[],
        skills_enabled=False,
        is_follow_up=False,
        cancelled=False,
        workspace=workspace,
        last_clarification="",
        policy=None,
        force_agent=None,
    )
    events = SimpleNamespace(
        on_stream_chunk=None,
        on_system_message=AsyncMock(),
        on_stats=AsyncMock(),
        on_approval_required=None,
    )
    return SimpleNamespace(context=ctx, events=events, emitter=None)


async def _insert_pause(sch: str, question: str, paused_state: dict, created_at=None):
    from sqlalchemy import select

    from core.database import bound_schema, get_async_session
    from core.models import Conversation, PausedSession

    async with bound_schema(sch):
        async with get_async_session() as s:
            existe = await s.execute(
                select(Conversation).where(Conversation.id == 7)  # type: ignore[arg-type]
            )
            if existe.scalar() is None:
                s.add(Conversation(id=7, title="conv de prueba"))  # type: ignore[call-arg]
            s.add(
                PausedSession(
                    conversation_id=7,
                    clarification_question=question,
                    clarification_options=None,
                    paused_state=json.dumps(paused_state),
                    created_at=created_at,
                )
            )


async def _get_row(sch: str):
    from sqlalchemy import select

    from core.database import bound_schema, get_async_session
    from core.models import PausedSession

    async with bound_schema(sch):
        async with get_async_session() as s:
            res = await s.execute(select(PausedSession).order_by(PausedSession.id))
            return res.scalars().all()


@_pg
@pytest.mark.asyncio
async def test_get_unresolved_pause_encuentra_humana_y_filtra_autocheckpoint():
    """Recovery GUI: retorna la pausa humana más reciente; los
    snapshots [auto-checkpoint] JAMÁS son reanudables; None si no hay nada."""
    from core.database import bound_schema, create_schema, create_tables_in_schema, drop_schema

    sch = f"resume_gp_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            old = datetime.datetime(2026, 9, 1, 10, 0, 0)
            new = datetime.datetime(2026, 9, 2, 10, 0, 0)
            await _insert_pause(sch, "tarea inicial", {"origin": "dsl"}, created_at=old)
            await _insert_pause(
                sch,
                f"{CHECKPOINT_QUESTION_PREFIX}snapshot",
                {"origin": "dsl"},
                created_at=new,
            )
            found = await WorkflowOrchestrator.get_unresolved_pause(7)
            assert found is not None
            assert found["question"] == "tarea inicial"
            ps = found["paused_state"]
            if isinstance(ps, str):
                ps = json.loads(ps)
            assert ps["origin"] == "dsl"

            # sin pausa para otra conversación
            assert await WorkflowOrchestrator.get_unresolved_pause(999) is None
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_resume_workflow_sin_pausa_devuelve_none():
    from core.database import bound_schema, create_schema, create_tables_in_schema, drop_schema

    sch = f"resume_np_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
        session = _session(conv_id=7, workspace=sch)
        assert await WorkflowOrchestrator.resume_workflow(session, "x") is None
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_resume_workflow_dsl_marca_resolved_y_persiste_answer():
    """El SELECT real de resume_workflow: resuelve la fila (resolved_at) y
    persiste la respuesta ANTES de delegar al motor DSL."""
    from core.database import bound_schema, create_schema, create_tables_in_schema, drop_schema

    sch = f"resume_dsl_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
        await _insert_pause(
            sch,
            "¿continuamos?",
            {
                "origin": "dsl",
                "workflow": "demo_dsl",
                "query": "tarea",
                "paused_step": "humano",
                "paused_kind": "checkpoint",
                "snapshot": {
                    "vars": {},
                    "completed": [],
                    "loop_state": {},
                    "children": {},
                    "results": [],
                },
            },
        )
        session = _session(conv_id=7, workspace=sch)
        with patch.object(
            WorkflowOrchestrator, "_resume_dsl", new_callable=AsyncMock, return_value="REANUDADO"
        ) as mock_resume:
            out = await WorkflowOrchestrator.resume_workflow(session, "sí, sigue")
        assert out == "REANUDADO"
        assert mock_resume.await_count == 1
        assert mock_resume.await_args.kwargs["answer"] == "sí, sigue"

        rows = await _get_row(sch)
        assert len(rows) == 1
        assert rows[0].resolved_at is not None, "la pausa debió marcarse resuelta"
        assert rows[0].clarification_answer == "sí, sigue"
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_resume_bot_turn_e2e_con_identidad_y_persistencia():
    """_resume_bot_turn: reconstruye el loop con la identidad del
    bot, marca la pausa resuelta y finaliza con scorecard recuperadas=1."""
    from core.database import bound_schema, create_schema, create_tables_in_schema, drop_schema

    sch = f"resume_bot_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
        await _insert_pause(
            sch,
            "¿qué ORM?",
            {
                "origin": "bot",
                "bot_slug": "alfa",
                "query": "recomiéndame un ORM",
                "paused_loop_state": {
                    "task": "recomiéndame un ORM",
                    "messages": [{"role": "user", "content": "hola"}],
                },
                "allowed_tools": [],
            },
        )
        session = _session(conv_id=7, workspace=sch)
        bot_def = {"slug": "alfa", "enabled": True, "tool_names": ["memory_saver"], "model": None}

        with (
            patch("core.bots.BotsService.get_bot", new_callable=AsyncMock, return_value=bot_def),
            patch(
                "orchestration.loop.execute_agent_loop",
                new_callable=AsyncMock,
                return_value={"result": "te recomiendo sqlmodel", "tokens_used": 42},
            ) as mock_loop,
            patch(
                "orchestration.utils.apply_undercover",
                new_callable=AsyncMock,
                side_effect=lambda text: text,
            ),
            patch(
                "orchestration.bots_dispatch.finalize_workflow", new_callable=AsyncMock
            ) as mock_final,
        ):
            out = await WorkflowOrchestrator.resume_workflow(session, "sqlmodel")

        assert out == "te recomiendo sqlmodel"
        assert mock_loop.await_count == 1
        bot_ctx = mock_loop.await_args.kwargs["bot_context"]
        assert bot_ctx["slug"] == "alfa" and bot_ctx["dm_enabled"] is True
        historial = mock_loop.await_args.kwargs["history"]
        assert historial[-1]["content"] == "[Respuesta a: ¿qué ORM?] sqlmodel"
        assert mock_final.await_count == 1
        assert mock_final.await_args.kwargs["scorecard"]["recuperadas"] == 1

        rows = await _get_row(sch)
        assert rows[0].resolved_at is not None
        assert rows[0].clarification_answer == "sqlmodel"
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_resume_bot_turn_bot_desaparecido_es_accionable():
    from core.database import bound_schema, create_schema, create_tables_in_schema, drop_schema

    sch = f"resume_bd_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
        await _insert_pause(
            sch,
            "¿sigues?",
            {"origin": "bot", "bot_slug": "fantasma", "paused_loop_state": {}, "query": "x"},
        )
        session = _session(conv_id=7, workspace=sch)
        with patch("core.bots.BotsService.get_bot", new_callable=AsyncMock, return_value=None):
            out = await WorkflowOrchestrator.resume_workflow(session, "hola")
        assert "fantasma" in str(out) and "no existe" in str(out)
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_resume_workflow_legacy_no_reanudable():
    """Retiro del legacy: una pausa origin=tdd responde amable
    que no es reanudable (jamás un crash)."""
    from core.database import bound_schema, create_schema, create_tables_in_schema, drop_schema

    sch = f"resume_lg_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
        await _insert_pause(sch, "¿ok?", {"origin": "tdd", "query": "x"})
        session = _session(conv_id=7, workspace=sch)
        out = await WorkflowOrchestrator.resume_workflow(session, "sí")
        assert "no es reanudable" in str(out)
    finally:
        await drop_schema(sch)
