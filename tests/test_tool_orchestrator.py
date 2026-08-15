# tests/test_tool_orchestrator.py
"""Tests para el orchestrator de herramientas y su fast-fail."""

from unittest.mock import AsyncMock, patch

import pytest


class TestResultBasedFastFail:
    @pytest.mark.asyncio
    async def test_file_not_found_skips_retry(self):
        """File-not-found en result-based failure no debe reintentar."""
        from tools.orchestrator import ToolOrchestrator

        tool_func = AsyncMock()
        tool_func.return_value = {
            "success": False,
            "output": "Archivo no encontrado: app.py",
        }

        with patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func):
            result = await ToolOrchestrator.execute_tool(
                tool_name="test_runner",
                parameters={"file_path": "app.py"},
                workspace="test_ws",
            )

        assert result["success"] is False
        assert tool_func.call_count == 1

    @pytest.mark.asyncio
    async def test_other_failure_still_retries(self):
        """Errores que no son file-not-found sí reintentan."""
        from tools.orchestrator import ToolOrchestrator

        tool_func = AsyncMock()
        tool_func.return_value = {
            "success": False,
            "output": "Syntax error in test execution",
        }

        with patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func):
            result = await ToolOrchestrator.execute_tool(
                tool_name="test_runner",
                parameters={"file_path": "test.py"},
                workspace="test_ws",
            )

        assert result["success"] is False
        assert tool_func.call_count >= 2


def _mock_budget_settings(max_budget: int = 100, enabled: bool = True):
    from unittest.mock import MagicMock

    s = MagicMock()
    s.tools_enabled = True
    s.hooks_enabled = False
    s.tool_max_retries = 3
    s.tool_backoff_base = 2.0
    s.tool_max_tokens_per_workflow = max_budget
    s.tool_enable_token_budget = enabled
    s.active_workspace = "test_ws"
    return s


class TestTokenBudgetSkipAndTopology:
    @pytest.mark.asyncio
    async def test_skip_budget_bypasses_rejection(self):
        from tools.orchestrator import ToolOrchestrator, add_llm_token_usage

        tool_func = AsyncMock()
        tool_func.return_value = {"success": True, "output": "ok"}

        with (
            patch("tools.orchestrator.settings", _mock_budget_settings(max_budget=100)),
            patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func),
        ):
            ToolOrchestrator.reset_token_budget()
            add_llm_token_usage(150)  # excede el límite de 100

            rejected = await ToolOrchestrator.execute_tool(
                tool_name="file_manager",
                parameters={"action": "write", "path": "x.py", "content": "x" * 100},
                workspace="test_ws",
            )
            assert rejected["error"] == "token_budget_exceeded"
            assert tool_func.call_count == 0

            ok = await ToolOrchestrator.execute_tool(
                tool_name="file_manager",
                parameters={"action": "write", "path": "x.py", "content": "x" * 100},
                workspace="test_ws",
                skip_budget=True,
            )
            assert ok["success"] is True
            assert tool_func.call_count == 1

    @pytest.mark.asyncio
    async def test_child_task_accumulation_visible_to_root(self):
        """Topología: el budget acumulado en un task hijo (subtarea) debe ser
        visible para el task raíz (finalizer/auto-commit)."""
        import asyncio

        from tools.orchestrator import ToolOrchestrator, add_llm_token_usage

        tool_func = AsyncMock()
        tool_func.return_value = {"success": True, "output": "ok"}

        with (
            patch("tools.orchestrator.settings", _mock_budget_settings(max_budget=100)),
            patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func),
        ):
            ToolOrchestrator.reset_token_budget()

            async def child_subtask():
                add_llm_token_usage(150)

            await asyncio.create_task(child_subtask())

            result = await ToolOrchestrator.execute_tool(
                tool_name="file_manager",
                parameters={"action": "write", "path": "x.py", "content": "y" * 100},
                workspace="test_ws",
            )
            assert result["error"] == "token_budget_exceeded"
            assert tool_func.call_count == 0

    @pytest.mark.asyncio
    async def test_safe_tool_call_propagates_skip_budget(self):
        from tools.wrapper import safe_tool_call

        with patch(
            "tools.wrapper.tool_orchestrator.execute_tool", new_callable=AsyncMock
        ) as mock_exec:
            mock_exec.return_value = {"success": True, "output": "ok"}
            await safe_tool_call("git_manager", {"action": "init"}, workspace="w", skip_budget=True)
            assert mock_exec.call_args.kwargs.get("skip_budget") is True

    @pytest.mark.asyncio
    async def test_no_reset_means_no_budget_checks(self):
        """Sin reset_token_budget() (contextos fuera de workflow) no debe aplicarse el gate."""
        from tools.orchestrator import ToolOrchestrator

        tool_func = AsyncMock()
        tool_func.return_value = {"success": True, "output": "ok"}

        with patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func):
            result = await ToolOrchestrator.execute_tool(
                tool_name="file_manager",
                parameters={"action": "write", "path": "x.py", "content": "z" * 100},
                workspace="test_ws",
            )
        assert result["success"] is True
        assert tool_func.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_output",
    [
        "Command blocked for security: pattern ';\\s*rm\\s+'",
        "❌ 'python3 -c' está bloqueado por seguridad. Alternativas: usa un .py",
        "❌ Presupuesto de tokens excedido (121597/80000)",
        "❌ No hay un repositorio Git inicializado en este proyecto.",
        "❌ Diff aplicado pero tiene errores de sintaxis. Se revirtió el cambio.",
        "❌ No se pudo aplicar el diff. Puede que los números de línea hayan cambiado.",
    ],
)
async def test_unrecoverable_failures_skip_retry(failure_output):
    """Fallos irrecuperables (seguridad/budget/git/diff) no deben reintentarse."""
    from tools.orchestrator import ToolOrchestrator

    tool_func = AsyncMock()
    tool_func.return_value = {"success": False, "output": failure_output}

    with patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func):
        result = await ToolOrchestrator.execute_tool(
            tool_name="bash_manager",
            parameters={"command": "echo hi"},
            workspace="test_ws",
        )
    assert result["success"] is False
    assert tool_func.call_count == 1


class TestTokenBudgetAtomicity:
    def test_reserve_charges_when_under_budget(self):
        from tools.orchestrator import _TokenBudgetState

        state = _TokenBudgetState(max_budget=100)
        assert state.reserve(60) is True
        assert state.total == 60

    def test_reserve_blocks_when_over_budget(self):
        from tools.orchestrator import _TokenBudgetState

        state = _TokenBudgetState(max_budget=100)
        state.reserve(90)
        assert state.reserve(20) is False
        assert state.total == 90  # sin carga en el rechazo

    def test_reconcile_replaces_estimate_with_actual(self):
        from tools.orchestrator import _TokenBudgetState

        state = _TokenBudgetState(max_budget=100)
        state.reserve(60)
        state.reconcile(estimated=60, actual=80)
        assert state.total == 80

    @pytest.mark.asyncio
    async def test_concurrent_reservations_no_toctou(self):
        """Dos tareas reservando a la vez: la suma no excede el presupuesto.

        Antes del fix, el check-then-act con awaits entre medio permitía que
        ambas pasaran el chequeo y cargaran después (total > max). Con
        reserve() sincrónico (sin awaits), una de las dos es rechazada.
        """
        import asyncio

        from tools.orchestrator import _TokenBudgetState

        state = _TokenBudgetState(max_budget=100)

        async def reserve_60():
            await asyncio.sleep(0)  # ceder el loop para entrelazar tareas
            return state.reserve(60)

        results = await asyncio.gather(reserve_60(), reserve_60())
        assert sum(1 for ok in results if ok) == 1
        assert state.total == 60
        assert state.total <= state.max_budget
