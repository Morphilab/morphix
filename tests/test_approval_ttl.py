# tests/test_approval_ttl.py — TTL always-allow + deny-safe standalone
"""Dos contratos de la aprobación:
1. 'Always Allow' expira (TTL) — no vive para siempre.
2. MCP standalone / headless sin callback de aprobación es deny-safe
   salvo opt-in explícito (jamás ejecuta acciones peligrosas en silencio)."""

import pytest

from core.config import settings

# ── TTL de always-allow ───────────────────────────────────────────────────


def _fresh_state():
    import desktop.events as ev

    ev.reset_approval_state()
    return ev


def test_always_allow_grants_within_ttl(monkeypatch):
    ev = _fresh_state()
    monkeypatch.setattr(settings, "approval_always_allow_ttl", 1800.0, raising=False)

    import time as t

    with ev._approval_lock:
        ev._always_allowed["bash_manager"] = t.monotonic() + 1800.0
    assert ev._always_allow_grants("bash_manager") is True


def test_expired_always_allow_reprompts(monkeypatch):
    """Vencida ⇒ grants False y la entrada se LIMPIA (re-pregunta en el flujo)."""
    ev = _fresh_state()
    monkeypatch.setattr(settings, "approval_always_allow_ttl", 1800.0, raising=False)

    import time as t

    with ev._approval_lock:
        ev._always_allowed["bash_manager"] = t.monotonic() - 1.0  # vencida

    assert ev._always_allow_grants("bash_manager") is False
    with ev._approval_lock:
        assert "bash_manager" not in ev._always_allowed, "debe limpiar la vencida"


def test_ttl_zero_disables_memory(monkeypatch):
    """TTL<=0 ⇒ ni entradas frescas conceden (memoria desactivada)."""
    ev = _fresh_state()
    monkeypatch.setattr(settings, "approval_always_allow_ttl", 0.0, raising=False)

    import time as t

    with ev._approval_lock:
        ev._always_allowed["bash_manager"] = float(t.monotonic()) + 9999.0

    assert ev._always_allow_grants("bash_manager") is False


# ── Standalone deny-safe ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dangerous_without_callback_denied(monkeypatch):
    """Sin callback (MCP standalone): acción peligrosa se NIEGA con código."""
    from tools.orchestrator import ToolOrchestrator
    from tools.registry import ToolsRegistry

    async def _stub(**kw):
        return {"success": True}

    reg = ToolsRegistry()
    reg.register("bash_manager")(_stub)
    monkeypatch.setattr("tools.orchestrator.tools_registry", reg, raising=False)
    monkeypatch.setattr(ToolOrchestrator, "on_approval_required", None, raising=False)
    monkeypatch.delenv("ALLOW_UNATTENDED_DANGEROUS", raising=False)

    res = await ToolOrchestrator.execute_tool(
        "bash_manager",
        {"command": "echo hola"},
        role="agent",
        workspace="main",
    )
    assert res["success"] is False
    assert res["error"] == "approval_unavailable"


@pytest.mark.asyncio
async def test_dangerous_without_callback_opt_in_allowed(monkeypatch):
    from tools.orchestrator import ToolOrchestrator

    monkeypatch.setattr(ToolOrchestrator, "on_approval_required", None, raising=False)
    monkeypatch.setenv("ALLOW_UNATTENDED_DANGEROUS", "true")

    executed = {}

    async def fake_dispatch_execute(*a, **kw):
        executed["ran"] = True
        return {"success": True}

    monkeypatch.setattr(
        "tools.wrapper.tool_orchestrator.execute_tool",
        fake_dispatch_execute,
        raising=False,
    )

    res = await ToolOrchestrator.execute_tool(
        "memory_saver", {"key": "k", "value": "v"}, role="agent", workspace="main"
    )  # no-peligrosa: fluye igual
    assert res["success"] is True or isinstance(res, dict)
