# tests/test_bots_clock_coverage.py — cobertura del reloj de rutinas
"""bots_clock era la zona menos cubierta de la suite (56%) — justo el camino
de entrega de rutinas (history con/sin bot, bot-chat encolado, persistencia
C12 y resiliencia del loop)."""

import asyncio
import secrets
from unittest.mock import patch

import pytest
from sqlalchemy import text

from core.database import (
    bound_schema,
    create_schema,
    create_tables_in_schema,
    drop_schema,
    get_async_session,
)

_pg = pytest.mark.skipif(
    not __import__("os").environ.get("DATABASE_URL"), reason="requiere DATABASE_URL (PG real)"
)


async def _mensajes(sch: str, conv_title: str) -> list[tuple[str, str]]:
    async with bound_schema(sch):
        async with get_async_session() as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT m.role, m.content FROM message m "
                        "JOIN conversation c ON c.id = m.conversation_id "
                        "WHERE c.title = :t ORDER BY m.id"
                    ),
                    {"t": conv_title},
                )
            ).fetchall()
    return [(r[0], r[1]) for r in rows]


@_pg
@pytest.mark.asyncio
async def test_execute_prompt_botchat_encola_y_no_persiste_directo():
    """deliver='bot-chat': el prompt se ENCOLA (pending_turns) — no es turno
    directo; la persistencia la hace el wake al despacharlo."""
    from orchestration.bots_clock import execute_prompt

    encolados: list[tuple[str, str]] = []

    async def fake_enqueue(slug, prompt, **kwargs):
        encolados.append((slug, prompt))

    with patch("core.bots_routines.enqueue_bot_chat_delivery", side_effect=fake_enqueue):
        await execute_prompt("dime la hora", "bot-chat", "alfa", "reloj")

    assert encolados == [("alfa", "dime la hora")]


@_pg
@pytest.mark.asyncio
async def test_execute_prompt_history_con_bot_persiste_intercambio():
    """deliver='history' con bot: turno por bots_runner (transport='routine',
    dm_enabled=False) + persistencia user/assistant en la conv '⏰'."""
    from core.bots import BotsService
    from orchestration.bots_clock import execute_prompt

    sch = f"clock_bot_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            await BotsService.create_bot("alfa", display_name="Alfa")

            async def fake_run_bot_turn(slug, query, conv_id, **kwargs):
                assert kwargs.get("transport") == "routine"
                assert kwargs.get("dm_enabled") is False
                assert slug == "alfa"
                return "respuesta del bot"

            with patch("orchestration.bots_runner.run_bot_turn", side_effect=fake_run_bot_turn):
                await execute_prompt("chiste corto", "history", "alfa", "humor")

        msgs = await _mensajes(sch, "⏰ humor")
        assert [m[0] for m in msgs] == ["user", "assistant"]
        assert "[Rutina humor]" in msgs[0][1] and "chiste corto" in msgs[0][1]
        assert msgs[1][1] == "respuesta del bot"
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_execute_prompt_history_sin_bot_agente_simple():
    """RC-Botmode (8e67514): rutina history SIN bot = agente conversacional
    (JAMÁS workflow) con clarification_denied activo (REC-4)."""
    from orchestration import context as orch_ctx
    from orchestration.bots_clock import execute_prompt

    sch = f"clock_sb_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)

            async def fake_loop(**kwargs):
                assert kwargs.get("agent_type") == "conversacional"
                assert kwargs.get("project_root") is None
                assert orch_ctx.clarification_denied.get() is True, "REC-4"
                return {"result": "respuesta simple"}

            with patch("orchestration.loop.execute_agent_loop", side_effect=fake_loop):
                await execute_prompt("refrán del día", "history", None, "refranes")

        msgs = await _mensajes(sch, "⏰ refranes")
        assert [m[0] for m in msgs] == ["user", "assistant"]
        assert "refrán del día" in msgs[0][1]
        assert msgs[1][1] == "respuesta simple"
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_routines_loop_tolerA_fallos_del_tick():
    """Un tick que explota NO mata el daemon (routines_loop continúa)."""
    from orchestration.bots_clock import routines_loop

    ticks = {"n": 0}

    async def exploding_tick(**kwargs):
        ticks["n"] += 1
        raise RuntimeError("BD caída simulada")

    with (
        patch("core.bots_routines.scheduler_tick", side_effect=exploding_tick),
        patch("core.feature_flags.kairos.get", return_value=0.01),
    ):
        task = asyncio.create_task(routines_loop())
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    assert ticks["n"] >= 2, "el loop debió seguir tras el fallo del tick"
