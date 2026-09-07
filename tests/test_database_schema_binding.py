# tests/test_database_schema_binding.py
"""Binding schema↔ejecución vía contextvar.

Un workflow largo debe conservar su workspace aunque otra corrutina cambie
el schema global (set_async_schema) a mitad de ejecución. También el resume
de un workflow pausado corre contra el schema ORIGINAL de la pausa.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import database as db
from orchestration.context import Session, WorkflowContext, WorkflowEvents


class FakeSession:
    """Fake mínimo con la interfaz que get_async_session usa realmente."""

    def __init__(self, sink: dict):
        self._sink = sink

    async def execute(self, sql, *args, **kwargs):
        self._sink.setdefault("sqls", []).append(str(sql))
        self._sink["sql"] = str(sql)
        return self._sink.get("result")

    def add(self, obj):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def close(self):
        pass


def _patch_factory(monkeypatch, sink: dict):
    monkeypatch.setattr(db, "get_async_session_factory", lambda: (lambda: FakeSession(sink)))


@pytest.mark.asyncio
async def test_session_uses_bound_schema_over_global(monkeypatch):
    """Con bound_schema, la sesión usa el schema del contexto aunque el global cambie."""
    seen: dict = {}
    _patch_factory(monkeypatch, seen)
    monkeypatch.setattr(db, "_current_async_schema", "other_ws")

    async with db.bound_schema("main"):
        async with db.get_async_session():
            pass

    assert "SET search_path TO main" in seen["sql"]


@pytest.mark.asyncio
async def test_unbound_session_falls_back_to_global(monkeypatch):
    """Sin bound_schema → usa _current_async_schema (comportamiento actual intacto)."""
    seen: dict = {}
    _patch_factory(monkeypatch, seen)
    monkeypatch.setattr(db, "_current_async_schema", "ws_global")

    async with db.get_async_session():
        pass

    assert "SET search_path TO ws_global" in seen["sql"]


@pytest.mark.asyncio
async def test_bound_schema_validates_name():
    with pytest.raises(ValueError):
        async with db.bound_schema("Bad-Schema"):
            pass


@pytest.mark.asyncio
async def test_nested_or_sequential_reset_restores_previous():
    """Tras salir del with el binding vuelve al valor previo (None si raíz)."""
    assert db._bound_schema.get() is None
    async with db.bound_schema("main"):
        async with db.bound_schema("ws_nested"):
            assert db._bound_schema.get() == "ws_nested"
        assert db._bound_schema.get() == "main"
    assert db._bound_schema.get() is None


# ── Integración: run_full_workflow ejecuta su dispatch dentro del binding ──


def _make_ctx(query: str = "Hola") -> WorkflowContext:
    return WorkflowContext(
        query=query,
        mode="chat",
        conversation_history=[],
        workspace="main",
        project_root=None,
        current_pdf_text=None,
        active_workflow=None,
        settings=MagicMock(),
        agents_registry=MagicMock(),
        enc=MagicMock(),
        allowed_tools=None,
    )


def _make_events() -> WorkflowEvents:
    return WorkflowEvents(
        on_stream_chunk=AsyncMock(),
        on_system_message=AsyncMock(),
        on_assistant_message=AsyncMock(),
        on_stats_update=AsyncMock(),
        on_ui_refresh=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_run_full_workflow_binds_workspace_schema(monkeypatch):
    """El dispatch corre dentro de bound_schema(workspace) aunque el global sea otro."""
    import orchestration.workflows.orchestrator as orch

    seen: dict = {}

    async def fake_dispatch(**kwargs):
        seen["bound"] = db._bound_schema.get()
        return "ok"

    monkeypatch.setattr(orch.WorkflowOrchestrator, "_dispatch_route", fake_dispatch)
    monkeypatch.setattr(
        "core.security.undercover_mode.undercover.check_query",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(orch, "get_global_workspaces", lambda: MagicMock(current="main"))
    monkeypatch.setattr(db, "_current_async_schema", "otro_ws_global")

    result = await orch.WorkflowOrchestrator.run_full_workflow(
        session=Session(context=_make_ctx(), events=_make_events())
    )

    assert result == "ok"
    assert seen["bound"] == "main"
    # Al salir, el binding se libera (no contamina otras corrutinas).
    assert db._bound_schema.get() is None


@pytest.mark.asyncio
async def test_run_full_workflow_direct_tool_also_bound(monkeypatch):
    """La ruta directa (returns tempranos antes del dispatch) también corre bound."""
    import orchestration.workflows.orchestrator as orch

    seen: dict = {}

    async def fake_direct(*args, **kwargs):
        seen["bound"] = db._bound_schema.get()
        return "ok"

    async def _no_dispatch(**kwargs):
        raise AssertionError("falló el parseo directo: se alcanzó _dispatch_route")

    monkeypatch.setattr(orch.WorkflowOrchestrator, "_run_direct_tool", fake_direct)
    monkeypatch.setattr(orch.WorkflowOrchestrator, "_dispatch_route", _no_dispatch)
    monkeypatch.setattr(
        "core.security.undercover_mode.undercover.check_query",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(orch, "get_global_workspaces", lambda: MagicMock(current="main"))
    monkeypatch.setattr("orchestration.dsl.compiler.is_dsl_document", lambda _d: True)
    monkeypatch.setattr(
        orch,
        "load_workflow_document",
        lambda *a, **k: {
            "version": 1,
            "name": "development",
            "tools": {"allowed": ["file_manager"]},
        },
    )
    monkeypatch.setattr("tools.registry.tools_registry.get_tool", lambda name: (lambda **kw: {}))
    with patch("tools.specs.tool_matches_allowlist", return_value=True):
        await orch.WorkflowOrchestrator.run_full_workflow(
            session=Session(
                context=_make_ctx(query="file_manager: read, path=test.txt"),
                events=_make_events(),
            )
        )

    assert seen["bound"] == "main"


# ── resume_workflow corre contra el schema ORIGINAL de la pausa ──


@pytest.mark.asyncio
async def test_resume_binds_to_paused_workspace_schema(monkeypatch):
    """Pausa en ws_origen → switch global a otro ws → el resume busca la
    PausedSession y ejecuta las rutas contra ws_origen, no contra el global."""
    import orchestration.workflows.orchestrator as orch

    seen: dict = {}
    paused_row = SimpleNamespace(
        paused_state=json.dumps({"origin": "dsl"}),
        clarification_question="¿q?",
        clarification_answer=None,
        resolved_at=None,
    )
    seen["result"] = MagicMock(scalar=lambda: paused_row)
    _patch_factory(monkeypatch, seen)

    # Simula el switch global DESPUÉS de la pausa: el global ya no es el original.
    monkeypatch.setattr(db, "_current_async_schema", "ws_switched")

    async def fake_resume_dsl(session, **kwargs):
        seen["bound_in_route"] = db._bound_schema.get()
        return "reanudado"

    monkeypatch.setattr(orch.WorkflowOrchestrator, "_resume_dsl", fake_resume_dsl)

    ctx = _make_ctx()
    ctx.workspace = "ws_origen"
    result = await orch.WorkflowOrchestrator.resume_workflow(
        session=Session(context=ctx, events=_make_events()), answer="42"
    )

    assert result == "reanudado"
    # El SELECT de PausedSession corrió bound al schema ORIGINAL (filas por-schema).
    assert any("SET search_path TO ws_origen" in s for s in seen["sqls"])
    # La ruta de continuación también hereda el binding.
    assert seen["bound_in_route"] == "ws_origen"
    # Al salir, sin contaminación del contexto.
    assert db._bound_schema.get() is None
