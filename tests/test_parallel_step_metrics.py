# tests/test_parallel_step_metrics.py — la métrica per-step (tokens_used_call)
# es exacta por hermano bajo gather: cada loop cuenta SOLO sus llamadas LLM.
import asyncio

import pytest


def _poison_budget_ctx(monkeypatch):
    """Aísla el budget (evita tocar el techo real) y arranca el contador en 0."""
    import tools.orchestrator as orch

    monkeypatch.setattr(orch.ToolOrchestrator, "ENABLE_TOKEN_BUDGET", False)
    monkeypatch.setattr(orch.ToolOrchestrator, "MAX_TOKENS_PER_WORKFLOW", 10**9)
    monkeypatch.setattr(
        orch, "_llm_call_tokens", orch._llm_call_tokens
    )  # no-op: ContextVar ya por-task


@pytest.mark.asyncio
async def test_tokens_used_call_is_exact_per_parallel_sibling(monkeypatch):
    """Dos execute_agent_loop concurrentes: el delta de cada uno cuenta SOLO
    sus propias llamadas — el doble-conteo entre hermanos (budget compartido)
    no puede filtrarse en la métrica per-step."""
    import tools.orchestrator as orch
    from orchestration.loop import execute_agent_loop

    _poison_budget_ctx(monkeypatch)

    async def _fake_impl(**kwargs):
        # cada hermano hace SU llamada con SU uso: 100 y 200 tokens
        uso = 100 if kwargs.get("extra_context") == "A" else 200
        orch.add_llm_token_usage(uso)
        return {"result": "ok", "iterations": 1, "status": "completed"}

    monkeypatch.setattr(
        "orchestration.loop._execute_agent_loop_impl", lambda **kw: _fake_impl(**kw)
    )
    # ambos hermanos arrancan desde el MISMO snapshot del contador (como gather)
    a, b = await asyncio.gather(
        execute_agent_loop(task="A", agent_type="conversacional", history=[], extra_context="A"),
        execute_agent_loop(task="B", agent_type="conversacional", history=[], extra_context="B"),
    )
    assert a["tokens_used_call"] == 100, f"hermano A contó {a['tokens_used_call']} (esperado 100)"
    assert b["tokens_used_call"] == 200, f"hermano B contó {b['tokens_used_call']} (esperado 200)"


@pytest.mark.asyncio
async def test_budget_state_is_shared_by_design_under_gather(monkeypatch):
    """El techo compartido entre hermanos es diseño deliberado (AGENTS):
    'shared mutable budget object, deterministic across gather'. El test
    documenta el contrato: ambas subtareas ven el MISMO acumulado total."""
    import tools.orchestrator as orch

    monkeypatch.setattr(orch.ToolOrchestrator, "ENABLE_TOKEN_BUDGET", True)
    monkeypatch.setattr(orch.ToolOrchestrator, "MAX_TOKENS_PER_WORKFLOW", 10**9)
    # sembrar un estado fresco en ESTE contexto
    monkeypatch.setattr(orch, "_token_budget_ctx", orch._token_budget_ctx)
    from tools.orchestrator import _token_budget_ctx, _TokenBudgetState

    _token_budget_ctx.set(_TokenBudgetState(10**9))

    async def _bump(n):
        orch.add_llm_token_usage(n)
        return _token_budget_ctx.get().total

    t1, t2 = await asyncio.gather(_bump(50), _bump(70))
    # al menos UN hermano observó el acumulado del otro (estado compartido):
    # 50+70=120, y algún hermano vio > su propio aporte
    assert max(t1, t2) == 120, f"el estado compartido perdió aportes: {t1}, {t2}"
