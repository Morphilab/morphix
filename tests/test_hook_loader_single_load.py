"""Carga ÚNICA de hooks globales (core/hook_loader).

Bug espejo del de tools/loader: load_global_hooks() re-ejecutaba
core/hooks/*.py vía spec SIN registrar sys.modules → el import canónico
creaba un SEGUNDO juego de handlers ⇒ cada tool call audita 2×.
Evidencia real: 62/105 eventos duplicados (Δt=0ms) en audit.jsonl.
"""

import asyncio
import importlib
import json
import sys
from unittest.mock import patch

import pytest

from core.hooks_registry import hooks_registry


@pytest.fixture
def cold_hook_state():
    """Arranque en frío: sin core.hooks.* precargados, registry vacío.

    Restaura sys.modules y registry al terminar (higiene total).
    """
    from core.hooks_registry import hooks_registry

    saved_modules = {k: v for k, v in sys.modules.items() if k.startswith("core.hooks.")}
    saved_hooks = {k: list(v) for k, v in hooks_registry._hooks.items()}
    saved_sources = dict(hooks_registry._source_map)

    for k in list(sys.modules):
        if k.startswith("core.hooks.") and k != "core.hooks_registry":
            del sys.modules[k]
    hooks_registry._hooks.clear()
    hooks_registry._source_map.clear()

    yield

    for k in [k for k in sys.modules if k.startswith("core.hooks.")]:
        del sys.modules[k]
    sys.modules.update(saved_modules)
    hooks_registry._hooks.clear()
    for point, handlers in saved_hooks.items():
        hooks_registry._hooks[point] = list(handlers)
    hooks_registry._source_map.update(saved_sources)


def test_cold_load_registers_each_hook_once(cold_hook_state):
    """t1: arranque en frío → exactamente 2 handlers en on_before_tool."""
    from core.hook_loader import load_global_hooks

    load_global_hooks()
    n = len(hooks_registry._hooks.get("on_before_tool", []))
    assert n == 2, f"esperaba audit+distillation_guard, hay {n}"


def test_reload_does_not_duplicate_handlers(cold_hook_state):
    """t2: re-llamada a load_global_hooks NO duplica handlers."""
    from core.hook_loader import load_global_hooks

    load_global_hooks()
    n1 = len(hooks_registry._hooks.get("on_before_tool", []))
    load_global_hooks()
    n2 = len(hooks_registry._hooks.get("on_before_tool", []))
    assert n2 == n1, f"carga duplicada: {n1} → {n2}"


def test_canonical_import_then_loader_no_duplicates(cold_hook_state):
    """t3: orden de la app real (import canónico → loader) no duplica."""
    import core.hooks.audit  # noqa: F401 — registra H1
    from core.hook_loader import load_global_hooks

    n0 = len(hooks_registry._hooks.get("on_before_tool", []))
    load_global_hooks()
    n1 = len(hooks_registry._hooks.get("on_before_tool", []))

    assert n0 == 1, f"el import canónico debió registrar solo audit: {n0}"
    # Loader añade distillation_guard (fresco) PERO no duplica audit
    assert n1 == 2, f"esperaba audit+distillation sin duplicar audit: {n0} → {n1}"
    handlers = [
        h for h in hooks_registry._hooks["on_before_tool"] if h.__name__ == "audit_on_before_tool"
    ]
    assert len(handlers) == 1, f"audit_on_before_tool duplicado: {len(handlers)}"
    # El módulo canónico y el registrado por el loader son EL MISMO objeto
    mod = importlib.import_module("core.hooks.audit")
    assert sys.modules["core.hooks.audit"] is mod


def test_single_audit_line_per_dispatch(tmp_path, cold_hook_state):
    """t4 (integración): UN dispatch ⇒ UNA línea tool_before en audit.jsonl."""
    from agents import audit as audit_mod
    from core.hook_loader import load_global_hooks
    from core.hooks_registry import HookContext, hooks_registry

    audit_file = tmp_path / "audit.jsonl"
    with patch.object(audit_mod, "AUDIT_FILE", audit_file):
        load_global_hooks()
        ctx = HookContext(hook_point="on_before_tool", tool_name="eco_test", parameters={})
        asyncio.run(hooks_registry.dispatch("on_before_tool", ctx))

        lines = [
            json.loads(ln)
            for ln in audit_file.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]

    tool_lines = [e for e in lines if e.get("operation") == "tool_before"]
    assert (
        len(tool_lines) == 1
    ), f"doble auditoría: {len(tool_lines)} líneas tool_before para 1 dispatch"
