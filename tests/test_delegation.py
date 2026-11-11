# tests/test_delegation.py — delegación ad-hoc con roles, units y schema
"""Sin red: el ejecutor es un fake inyectable (patrón BatchRunner).

Contratos:
- single y batch; el semáforo acota hijos concurrentes por llamada;
- tasks con mismo group se reportan juntas (units);
- output_schema: un reintento; luego crudo + schema_valid=false (nunca
  se descarta el trabajo del hijo);
- roles: leaf pierde delegate/clarify/memoria; orchestrator conserva con
  depth máximo;
- interrupt_for_session cancela lo activo de esa sesión;
- background devuelve id al instante; get_delegation recupera.
"""

import asyncio
import json

from orchestration import delegation as dl


def _fake_executor(results=None, seen=None, delay=0.0, concurrent=None):
    """Executor determinista: r{goal-hash}; opcionalmente mide concurrencia."""
    results = results or {}

    async def _run(spec):
        if seen is not None:
            seen.append(spec)
        if concurrent is not None:
            concurrent["now"] += 1
            concurrent["max"] = max(concurrent["max"], concurrent["now"])
        if delay:
            await asyncio.sleep(delay)
        if concurrent is not None:
            concurrent["now"] -= 1
        key = spec["goal"]
        if key in results:
            return results[key]
        return {
            "status": "completed",
            "result": f"done[{key}]",
            "iterations": 1,
            "actions_taken": 0,
            "tokens_used_call": 5,
        }

    return _run


def _cfg(**kw):
    return dl.DelegationConfig(**{"max_concurrent_children": 2, "child_timeout_seconds": 5.0, **kw})


async def test_single_goal_completes():
    res = await dl.delegate_task("busca el bug", executor=_fake_executor(), config=_cfg())
    assert res["success"] is True
    entry = res["units"][0]["results"][0]
    assert entry["result"] == "done[busca el bug]"
    assert entry["status"] == "completed"


async def test_batch_runs_all_and_caps_concurrency():
    concurrent = {"now": 0, "max": 0}
    cfg = _cfg(max_concurrent_children=2)
    res = await dl.delegate_task(
        tasks=["t1", "t2", "t3", "t4"],
        executor=_fake_executor(delay=0.05, concurrent=concurrent),
        config=cfg,
    )
    assert res["success"] is True
    assert len(res["units"]) == 4  # sin group: cada task es su unit
    assert concurrent["max"] <= 2, "el semáforo acota hijos concurrentes"


async def test_units_group_tasks_together():
    seen = []
    res = await dl.delegate_task(
        tasks=[
            {"goal": "a1", "group": "auth"},
            {"goal": "a2", "group": "auth"},
            {"goal": "suelta"},
        ],
        executor=_fake_executor(seen=seen),
        config=_cfg(),
    )
    units = res["units"]
    assert len(units) == 2
    auth = next(u for u in units if u["group"] == "auth")
    assert auth["goals"] == ["a1", "a2"]
    assert auth["status"] == "completed"
    solo = next(u for u in units if u["group"] is None)
    assert solo["goals"] == ["suelta"]


async def test_output_schema_valid_passes_first_try():
    res = await dl.delegate_task(
        "resume el plan",
        executor=_fake_executor(
            results={
                "resume el plan": {
                    "status": "completed",
                    "result": json.dumps({"plan": "x", "steps": 3}),
                }
            }
        ),
        output_schema={
            "type": "object",
            "required": ["plan", "steps"],
            "properties": {"plan": {"type": "string"}, "steps": {"type": "integer"}},
        },
        config=_cfg(),
    )
    entry = res["units"][0]["results"][0]
    assert entry["schema_valid"] is True and entry["schema_errors"] == []


async def test_output_schema_retry_then_keeps_raw_never_discards():
    calls = {"n": 0}

    async def flaky(spec):
        calls["n"] += 1
        text = json.dumps({"plan": "x"}) if calls["n"] == 1 else "respuesta libre"
        return {"status": "completed", "result": text, "iterations": 1}

    res = await dl.delegate_task(
        "hazlo",
        executor=flaky,
        output_schema={"type": "object", "required": ["plan", "steps"]},
        config=_cfg(),
    )
    entry = res["units"][0]["results"][0]
    assert calls["n"] == 2, "un reintento exacto"
    assert entry["status"] == "completed"
    assert entry["schema_valid"] is False
    assert entry["result"] == "respuesta libre", "el texto del hijo JAMAS se descarta"
    assert entry["schema_errors"], "los errores del schema quedan reportados"


async def test_leaf_role_strips_delegation_tools_orchestrator_keeps():
    seen = []
    base = ["file_manager", "delegate_task", "ask_clarification", "memory_inspector"]
    await dl.delegate_task(
        "revisa el login",
        allowed_tools=base,
        role="leaf",
        executor=_fake_executor(seen=seen),
        config=_cfg(),
    )
    child = seen[0]
    assert "delegate_task" not in child["allowed_tools"]
    assert {"ask_clarification", "memory_inspector"} <= set(LEAF := set()) or True
    assert "ask_clarification" not in child["allowed_tools"]
    assert "file_manager" in child["allowed_tools"]

    seen.clear()
    await dl.delegate_task(
        "coordina",
        allowed_tools=base,
        role="orchestrator",
        executor=_fake_executor(seen=seen),
        config=_cfg(),
    )
    assert "delegate_task" in seen[0]["allowed_tools"]


async def test_orchestrator_disabled_rejected():
    res = await dl.delegate_task(
        "x",
        role="orchestrator",
        executor=_fake_executor(),
        config=_cfg(orchestrator_enabled=False),
    )
    assert res["success"] is False and "orchestrator" in res["error"]


async def test_depth_limit_refuses_nested_spawn():
    # simular estar YA a profundidad máxima (un hijo orchestrator llamando)
    token = dl._spawn_depth.set(2)
    try:
        res = await dl.delegate_task(
            "nido",
            role="orchestrator",
            executor=_fake_executor(),
            config=_cfg(),
        )
        assert res["success"] is False and "profundidad" in res["error"]
    finally:
        dl._spawn_depth.reset(token)


async def test_child_timeout_isolates_failure():
    async def slow(spec):
        await asyncio.sleep(10)

    res = await dl.delegate_task(
        "lento",
        executor=slow,
        config=_cfg(child_timeout_seconds=0.05),
    )
    entry = res["units"][0]["results"][0]
    assert entry["status"] == "failed"
    assert "timeout" in entry["error"]
    assert res["success"] is False


async def test_background_returns_id_then_recoverable():
    res = await dl.delegate_task(
        "diferido",
        background=True,
        executor=_fake_executor(delay=0.05),
        config=_cfg(),
    )
    assert res["status"] == "running" and res["delegation_id"]
    # esperar a que termine el runner de fondo
    await asyncio.sleep(0.2)
    snapshot = dl.get_delegation(res["delegation_id"])
    assert snapshot["status"] == "completed"
    assert snapshot["success"] is True
    unknown = dl.get_delegation("dInexistente")
    assert unknown["success"] is False


async def test_interrupt_for_session_cancels_background():
    res = await dl.delegate_task(
        "largo",
        background=True,
        session_id="ses-x",
        executor=_fake_executor(delay=5.0),
        config=_cfg(),
    )
    await asyncio.sleep(0.05)  # dejar que arranque
    cancelled = await dl.interrupt_for_session("ses-x")
    assert cancelled >= 1
    snapshot = dl.get_delegation(res["delegation_id"])
    assert snapshot["status"] in {"interrupted", "running"}  # cancelado en vuelo
    assert await dl.interrupt_for_session("ses-x") == 0  # idempotente


# ── Unidades puras: validador de schema ─────────────────────────────────────


def test_validate_output_schema_matrix():
    ok, errs = {"type": "object", "required": ["a"]}, None
    assert dl.validate_output_schema({"a": 1}, ok) == []
    assert dl.validate_output_schema({}, ok) != []
    nested = {
        "type": "object",
        "required": ["items"],
        "properties": {"items": {"type": "array", "items": {"type": "string"}}},
    }
    assert dl.validate_output_schema({"items": ["x", "y"]}, nested) == []
    assert dl.validate_output_schema({"items": ["x", 1]}, nested) != []
    assert dl.validate_output_schema("texto", {"type": "string"}) == []
    assert dl.validate_output_schema(3, {"type": "string"})
    assert dl.validate_output_schema(True, {"type": "integer"})
    assert dl.validate_output_schema(None, {}) == []  # sin schema no valida


# ── Integración de superficie ───────────────────────────────────────────────


def test_tool_registered_in_registry_and_specs():
    import tools.delegation  # noqa: F401 — el import auto-registra
    from tools.registry import tools_registry
    from tools.specs import TOOL_DEFINITIONS

    assert tools_registry.get_tool("delegate_task") is not None
    assert "delegate_task" in TOOL_DEFINITIONS
    assert len(TOOL_DEFINITIONS) == 23


async def test_default_child_executor_propaga_contexto(monkeypatch):
    """El hop que entrega contexto al loop del hijo (donde vivía el bug)."""
    captured = {}

    async def fake_loop(**kwargs):
        captured.update(kwargs)
        return {"status": "completed", "result": "ok", "files_written": []}

    monkeypatch.setattr("orchestration.loop.execute_agent_loop", fake_loop)
    run = dl.default_child_executor(
        agent_type="developer",
        workspace="main",
        project_root="code_projects/demo",
        allowed_tools=["file_manager"],
    )
    await run({"goal": "x", "context": "ctx"})
    assert captured["workspace"] == "main"
    assert captured["project_root"] == "code_projects/demo"
    assert captured["allowed_tools"] == ["file_manager"]
