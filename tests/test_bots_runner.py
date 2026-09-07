# tests/test_bots_runner.py — los turnos de bot no tocan la capa de workflows
"""E2E con ORQUESTADOR REAL (sin fakes del runner): los turnos de bot corren
execute_agent_loop con plantilla de bot — el no-orquestador queda cubierto
por test_bot_turn_never_touches_workflow_layer.

Mocks: SOLO la frontera LLM (execute_agent_loop). Toda la maquinaria de
plantillas/transportes/persistencia corre real sobre PG.
"""

import secrets
import shutil

import pytest
import yaml

from core.bots import BotsService
from core.database import (
    bound_schema,
    create_schema,
    create_tables_in_schema,
    drop_schema,
    get_async_session,
)
from core.path_resolver import paths
from orchestration import bots_runner

_pg = pytest.mark.skipif(
    not __import__("os").environ.get("DATABASE_URL"), reason="requiere DATABASE_URL (PG real)"
)


@pytest.fixture()
def fake_llm(monkeypatch):
    """Mock de execute_agent_loop: captura el contexto y devuelve texto."""
    calls: list[dict] = []

    async def _fake_loop(**kwargs):
        calls.append(kwargs)
        return {
            "status": "completed",
            "result": "respuesta del modelo fake",
            "files_written": [],
        }

    monkeypatch.setattr(bots_runner, "execute_agent_loop", _fake_loop)
    return calls


def _tpl_dir():
    from pathlib import Path

    return Path(paths.workspace_bots_dir("main").parent) / f"bots_test_{secrets.token_hex(4)}"


def _write_tpl(d, slug: str, **overrides) -> None:
    tpl = {
        "slug": slug,
        "display_name": slug.capitalize(),
        "description": "test",
        "soul_md": f"Eres @{slug}, bot de prueba.",
        "temperature": 0.4,
        "tool_names": ["web_fetch"],
        "skill_allowlist": [],
        "can_pause": False,
        "dm_enabled": True,
        "agent": "conversacional",
    }
    tpl.update(overrides)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.yaml").write_text(yaml.safe_dump(tpl), encoding="utf-8")


def _bounded(sch, d, monkeypatch):
    """Parchea el dir de plantillas de bots del workspace 'main'."""
    monkeypatch.setattr(paths, "workspace_bots_dir", lambda ws: d)


@_pg
@pytest.mark.asyncio
async def test_bot_turn_uses_template_and_own_tools(fake_llm, monkeypatch):
    """El turno lee la plantilla: toolset del bot (no de un workflow) y
    dm_enabled según template."""
    sch = f"brun_{secrets.token_hex(4)}"
    d = _tpl_dir()
    try:
        await create_schema(sch)
        _write_tpl(d, "nora")
        _bounded(sch, d, monkeypatch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            await BotsService.create_bot("nora", soul_md="Eres nora.", tool_names=["web_fetch"])

            final = await bots_runner.run_bot_turn("nora", "hola nora", 1, transport="dm")

            assert final == "respuesta del modelo fake"
            assert len(fake_llm) == 1
            kw = fake_llm[0]
            assert kw["allowed_tools"] == ["web_fetch"], "toolset del bot, no de un workflow"
            assert kw["bot_context"]["slug"] == "nora"
            assert kw["bot_context"]["dm_enabled"] is True

            # proyección DB: el YAML gobierna vía sync (switch); el runner lee
            # la fila. Cambio directo en DB se refleja sin re-escribir YAML.
            await BotsService.update_bot("nora", tool_names=[])
            await bots_runner.run_bot_turn("nora", "otra", 1, transport="room")
            assert fake_llm[1]["bot_context"]["dm_enabled"] is False
            assert fake_llm[1]["allowed_tools"] == []
    finally:
        await drop_schema(sch)
        shutil.rmtree(d, ignore_errors=True)


@_pg
@pytest.mark.asyncio
async def test_non_canonical_transports_deny_clarification(fake_llm, monkeypatch):
    """can_pause=true NO abre pausas en routine (matriz de transporte):
    el runner corrige a None + log en vez de propagar el dict."""
    sch = f"brun_{secrets.token_hex(4)}"
    d = _tpl_dir()
    try:
        await create_schema(sch)
        _write_tpl(d, "pasia", can_pause=True)
        _bounded(sch, d, monkeypatch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            await BotsService.create_bot("pasia", soul_md="Soul.")

            async def _fake_loop_clarifying(**kwargs):
                return {
                    "status": "clarification_needed",
                    "clarification_question": "¿?",
                    "clarification_options": [],
                    "paused_loop_state": {},
                }

            monkeypatch.setattr(bots_runner, "execute_agent_loop", _fake_loop_clarifying)

            res_routine = await bots_runner.run_bot_turn("pasia", "x", 1, transport="routine")
            res_dm = await bots_runner.run_bot_turn("pasia", "x", 1, transport="dm")
            res_room = await bots_runner.run_bot_turn("pasia", "x", 1, transport="room")
            assert res_routine is None
            assert res_dm is None
            assert res_room is None
    finally:
        await drop_schema(sch)
        shutil.rmtree(d, ignore_errors=True)


@_pg
@pytest.mark.asyncio
async def test_canonical_transport_propagates_clarification(fake_llm, monkeypatch):
    """can_pause=true + transport='canonical' ⇒ dict clarification_needed."""
    sch = f"brun_{secrets.token_hex(4)}"
    d = _tpl_dir()
    try:
        await create_schema(sch)
        _write_tpl(d, "pausa", can_pause=True)
        _bounded(sch, d, monkeypatch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            await BotsService.create_bot("pausa", soul_md="Soul.")

            async def _fake_loop_clarifying(**kwargs):
                return {
                    "status": "clarification_needed",
                    "clarification_question": "¿qué formato?",
                    "clarification_options": ["A", "B"],
                    "paused_loop_state": {"messages": [{"role": "user", "content": "x"}]},
                }

            monkeypatch.setattr(bots_runner, "execute_agent_loop", _fake_loop_clarifying)
            res = await bots_runner.run_bot_turn("pausa", "pregunta", 1, transport="canonical")
            assert isinstance(res, dict)
            assert res["status"] == "clarification_needed"
            assert res["bot_slug"] == "pausa"
            assert res["clarification_options"] == ["A", "B"]
    finally:
        await drop_schema(sch)
        shutil.rmtree(d, ignore_errors=True)


@_pg
@pytest.mark.asyncio
async def test_bot_turn_never_touches_workflow_layer(fake_llm, monkeypatch):
    """Regresión de acoplamiento: el runner no referencia plantillas de
    workflow NI el orquestador — la clase de bug 'default' muere aquí."""
    sch = f"brun_{secrets.token_hex(4)}"
    d = _tpl_dir()
    try:
        await create_schema(sch)
        _write_tpl(d, "sol")
        _bounded(sch, d, monkeypatch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            await BotsService.create_bot("sol", soul_md="Soul.")

            import inspect

            src = inspect.getsource(bots_runner)
            assert "load_workflow_template" not in src
            # solo mención documental en el docstring; cero import/uso en código
            body = src.split('"""', 2)[2]  # tras el docstring de módulo
            assert "WorkflowOrchestrator" not in body
            assert "run_full_workflow" not in body

            final = await bots_runner.run_bot_turn("sol", "turno", 1, transport="canonical")
            assert final == "respuesta del modelo fake"
    finally:
        await drop_schema(sch)
        shutil.rmtree(d, ignore_errors=True)


@_pg
@pytest.mark.asyncio
async def test_wake_end_to_end_with_real_runner(fake_llm, monkeypatch):
    """E2E sin fakes del runner: DM en pending_turns → dispatch_row →
    run_bot_turn REAL (LLM fake) → persist_turn_exchange → complete_row."""
    from core import bots_messaging
    from core.bots_chat import ensure_open
    from core.bots_messaging import send_dm
    from orchestration import bots_wake

    sch = f"brun_{secrets.token_hex(4)}"
    d = _tpl_dir()
    try:
        await create_schema(sch)
        _write_tpl(d, "destinataria")
        _write_tpl(d, "emisora")
        _bounded(sch, d, monkeypatch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            await BotsService.create_bot("emisora", soul_md="Soul A.")
            await BotsService.create_bot("destinataria", soul_md="Soul B.")
            canon = await ensure_open("destinataria")
            conv_id = int(canon["conversation_id"])

            ack = await send_dm("emisora", "destinataria", "informe de estado")
            assert ack["status"] == "sent"

            delivered = await bots_wake.drain_tick()
            assert delivered == 1

            # el turno llegó al runner con el wrapper + directiva
            assert len(fake_llm) == 1
            task = fake_llm[0]["task"]
            assert task.startswith("⟪DATOS-NO-CONFIABLES⟫")
            assert "informe de estado" in task
            assert "send_to_bot" in task  # directiva confiable tras el wrapper

            # persistencia + entrega terminal
            from sqlalchemy import select

            from core.models import Message

            async with get_async_session() as s:
                msgs = (
                    (await s.execute(select(Message).where(Message.conversation_id == conv_id)))
                    .scalars()
                    .all()
                )
            contents = [m.content for m in msgs]
            assert any("respuesta del modelo fake" in c for c in contents), contents
            assert await bots_messaging.pending_count("destinataria") == 0
    finally:
        await drop_schema(sch)
        shutil.rmtree(d, ignore_errors=True)
