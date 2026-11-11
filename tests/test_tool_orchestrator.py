# tests/test_tool_orchestrator.py
"""Tests para el orchestrator de herramientas y su fast-fail."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def _no_backoff_wait(monkeypatch):
    """Backoff real entre reintentos (~10s/test) → sleep instantáneo."""
    import asyncio

    async def _instant(*a, **k):
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant)


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
    @pytest.mark.parametrize(
        ("mensaje",),
        [
            ("❌ test_runner: sin rutas que verificar (file_path/file_paths vacíos).",),
            ("❌ Ruta no permitida: ../../../../pyproject.toml",),
        ],
    )
    async def test_denegaciones_deterministas_no_reintentan(self, mensaje):
        """Las denegaciones propias (sin rutas, containment) son
        deterministas: reintentar args idénticos produce el mismo fallo y
        quema llamadas LLM."""
        from tools.orchestrator import ToolOrchestrator

        tool_func = AsyncMock()
        tool_func.return_value = {"success": False, "output": mensaje}

        with patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func):
            result = await ToolOrchestrator.execute_tool(
                tool_name="file_manager",
                parameters={"path": "x"},
                workspace="test_ws",
            )

        assert result["success"] is False
        assert tool_func.call_count == 1

    @pytest.mark.asyncio
    async def test_otra_tool_mantiene_head_de_300(self):
        """Las tools que no son test_runner conservan el wrap con head de
        300: la salida completa es solo para el consumidor determinista de
        conteos."""
        from tools.orchestrator import ToolOrchestrator

        tool_func = AsyncMock()
        tool_func.return_value = {
            "success": False,
            "output": "E" * 600,
        }

        with patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func):
            result = await ToolOrchestrator.execute_tool(
                tool_name="code_search",
                parameters={"query": "x"},
                workspace="test_ws",
            )

        assert result["success"] is False
        esperado = "❌ La herramienta 'code_search' reportó fallo: " + "E" * 300
        assert result["output"] == esperado

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


class TestRedCountsPreservation:
    """El fast-fail de test_runner debe preservar los conteos parseados y
    entregar la salida completa: el until consume conteos deterministas y el
    agente necesita ver los fallos, no la cabecera de pytest."""

    SALIDA_LARGA = (
        "============================= test session starts ==============================\n"
        "platform linux -- Python 3.12.15, pytest-8.4.2, pluggy-1.6.0\n"
        "rootdir: /proyecto\n"
        "collected 9 items\n\n"
        "tests/test_validar.py::test_valida PASSED [ 11%]\n"
        "tests/test_validar.py::test_corta FAILED [ 22%]\n"
        "tests/test_validar.py::test_mayuscula FAILED [ 33%]\n"
        "tests/test_validar.py::test_numero FAILED [ 44%]\n"
        "tests/test_validar.py::test_secuencia FAILED [ 55%]\n"
        "tests/test_validar.py::test_otra FAILED [ 66%]\n\n"
        "============================== 5 failed in 0.19s ==============================="
    )

    @pytest.mark.asyncio
    async def test_rojo_de_test_runner_preserva_conteos_y_salida(self):
        from tools.orchestrator import ToolOrchestrator

        tool_func = AsyncMock()
        tool_func.return_value = {
            "success": False,
            "output": self.SALIDA_LARGA,
            "returncode": 1,
            "passed_count": 0,
            "failed_count": 5,
            "error_count": 0,
            "total_count": 5,
        }

        with patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func):
            result = await ToolOrchestrator.execute_tool(
                tool_name="test_runner",
                parameters={"file_path": "tests/test_validar.py"},
                workspace="test_ws",
            )

        assert result["success"] is False
        assert result["error"] == "tests_failed"
        # conteos estructurados intactos (los lee el until/verdict)
        assert result["failed_count"] == 5
        assert result["total_count"] == 5
        assert result["returncode"] == 1
        # salida COMPLETA: el agente ve los fallos, no la cabecera truncada
        assert "5 failed in 0.19s" in result["output"]
        assert len(result["output"]) == len(self.SALIDA_LARGA)
        assert tool_func.call_count == 1  # determinista: sin retry

    @pytest.mark.asyncio
    async def test_otro_fallo_de_test_runner_tambien_preserva_estructura(self):
        """Fast-fail por keyword (p.ej. ruta inexistente): las claves de la
        tool también viajan — aunque no haya conteos, nada se descarta."""
        from tools.orchestrator import ToolOrchestrator

        tool_func = AsyncMock()
        tool_func.return_value = {
            "success": False,
            "output": "❌ Archivo de test no encontrado: tests/nada.py",
            "returncode": 4,
        }

        with patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func):
            result = await ToolOrchestrator.execute_tool(
                tool_name="test_runner",
                parameters={"file_path": "tests/nada.py"},
                workspace="test_ws",
            )

        assert result["success"] is False
        assert result["returncode"] == 4
        # salida íntegra (la tool ya la recorta al tail si hace falta)
        assert "Archivo de test no encontrado" in result["output"]


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
async def test_unrecoverable_failures_skip_retry(failure_output, monkeypatch):
    monkeypatch.setenv("ALLOW_UNATTENDED_DANGEROUS", "true")
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


class TestBudgetRefundOnDefinitiveFailure:
    async def _run_failing(self, tool_func):
        from tools.orchestrator import ToolOrchestrator

        with (
            patch("tools.orchestrator.settings", _mock_budget_settings(max_budget=100_000)),
            patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func),
        ):
            ToolOrchestrator.reset_token_budget()
            result = await ToolOrchestrator.execute_tool(
                tool_name="file_manager",
                parameters={"action": "write", "path": "a.py", "content": "x" * 50},
                workspace="test_ws",
            )
            from tools.orchestrator import _token_budget_ctx

            return result, _token_budget_ctx.get()

    @pytest.mark.asyncio
    async def test_max_retries_exhaustion_refunds_reserve(self):
        """Fallo definitivo tras reintentos devuelve la reserva."""
        tool_func = AsyncMock(side_effect=Exception("boom inesperado"))
        result, state = await self._run_failing(tool_func)
        assert result["error"] == "max_retries_exceeded"
        assert state is not None
        assert (
            state.total == 0
        ), f"Regresión: reserva quemada tras fallo definitivo (total={state.total})"

    @pytest.mark.asyncio
    async def test_fast_fail_refunds_reserve(self):
        """Fast-fail determinista también devuelve la reserva."""
        tool_func = AsyncMock(
            return_value={"success": False, "output": "no hay un repositorio git"}
        )
        result, state = await self._run_failing(tool_func)
        assert result["error"] == "tool_reported_failure"
        assert tool_func.call_count == 1
        assert state.total == 0, f"fast-fail sin refund: {state.total}"


class TestDiffEditorApprovalGate:
    """diff_editor.apply es escritura → exige aprobación; create/read siguen fluidos."""

    @pytest.mark.asyncio
    async def test_diff_editor_apply_requires_approval(self, monkeypatch):
        from tools.orchestrator import ToolOrchestrator

        called = {}

        async def fake_approval(tool, params):
            called["tool"] = tool
            return False

        monkeypatch.setattr(ToolOrchestrator, "on_approval_required", fake_approval)
        tool_func = AsyncMock(return_value={"success": True, "output": "ok"})

        with (
            patch("tools.orchestrator.settings", _mock_budget_settings()),
            patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func),
        ):
            result = await ToolOrchestrator.execute_tool(
                tool_name="diff_editor",
                parameters={
                    "action": "apply",
                    "file_path": "a.txt",
                    "diff_content": "--- a\n+++ b",
                },
                workspace="test_ws",
            )

        assert called.get("tool") == "diff_editor"
        assert result["error"] == "approval_denied"
        assert tool_func.call_count == 0

    @pytest.mark.asyncio
    async def test_diff_editor_without_action_requires_approval(self, monkeypatch):
        """Params SIN 'action' (la tool tenía default='apply', escritura)
        deben pasar por approval — el gate normaliza y falla cerrado."""
        from tools.orchestrator import ToolOrchestrator

        called = {}

        async def fake_approval(tool, params):
            called["tool"] = tool
            return False

        monkeypatch.setattr(ToolOrchestrator, "on_approval_required", fake_approval)
        tool_func = AsyncMock(return_value={"success": True, "output": "ok"})

        with (
            patch("tools.orchestrator.settings", _mock_budget_settings()),
            patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func),
        ):
            result = await ToolOrchestrator.execute_tool(
                tool_name="diff_editor",
                parameters={"file_path": "a.txt"},
                workspace="test_ws",
            )

        assert called.get("tool") == "diff_editor"
        assert result["error"] == "approval_denied"
        assert tool_func.call_count == 0

    @pytest.mark.asyncio
    async def test_diff_editor_create_does_not_require_approval(self, monkeypatch):
        from tools.orchestrator import ToolOrchestrator

        async def boom(tool, params):
            raise AssertionError("no debe pedirse approval para create/read")

        monkeypatch.setattr(ToolOrchestrator, "on_approval_required", boom)
        tool_func = AsyncMock(return_value={"success": True, "output": "ok"})

        with (
            patch("tools.orchestrator.settings", _mock_budget_settings()),
            patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func),
        ):
            result = await ToolOrchestrator.execute_tool(
                tool_name="diff_editor",
                parameters={"action": "create", "file_path": "a.txt"},
                workspace="test_ws",
            )

        assert result["success"] is True
        assert tool_func.call_count == 1


@pytest.mark.asyncio
async def test_overbudget_discard_reports_side_effects():
    """Al descartar por presupuesto el resultado de una tool que YA
    ejecutó efectos, la respuesta debe avisarlo explícitamente."""
    from tools.orchestrator import ToolOrchestrator

    tool_func = AsyncMock(
        return_value={"success": True, "output": "escrito", "tokens_used": 500_000}
    )
    with (
        patch("tools.orchestrator.settings", _mock_budget_settings(max_budget=100)),
        patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func),
    ):
        ToolOrchestrator.reset_token_budget()
        result = await ToolOrchestrator.execute_tool(
            tool_name="file_manager",
            parameters={"action": "write", "path": "x.py", "content": "x"},
            workspace="test_ws",
        )

    assert result["success"] is False
    assert result.get("side_effects_executed") is True
    assert "efecto" in str(result.get("output", "")).lower()


@pytest.mark.asyncio
async def test_tool_without_tokens_used_does_not_double_count_params():
    """Tool sin reporte de tokens_used NO carga la estimación de parámetros:
    el coste real ya se contó como completion del LLM. La estimación solo
    actúa como reserva transitoria contra el overcommit concurrente."""
    from tools.orchestrator import ToolOrchestrator, get_llm_token_usage

    tool_func = AsyncMock(return_value={"success": True, "output": "escrito"})
    with (
        patch("tools.orchestrator.settings", _mock_budget_settings(max_budget=10_000)),
        patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func),
    ):
        ToolOrchestrator.reset_token_budget()
        big_content = "x" * 4_000  # ~1k tokens de parámetros
        result = await ToolOrchestrator.execute_tool(
            tool_name="file_manager",
            parameters={"action": "write", "path": "x.py", "content": big_content},
            workspace="test_ws",
        )
        assert result["success"] is True
        assert get_llm_token_usage() == 0


@pytest.mark.asyncio
async def test_tool_reporting_tokens_used_is_charged():
    """Tool que SÍ reporta tokens_used: su gasto real sustituye la reserva."""
    from tools.orchestrator import ToolOrchestrator, get_llm_token_usage

    tool_func = AsyncMock(return_value={"success": True, "output": "ok", "tokens_used": 300})
    with (
        patch("tools.orchestrator.settings", _mock_budget_settings(max_budget=10_000)),
        patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func),
    ):
        ToolOrchestrator.reset_token_budget()
        await ToolOrchestrator.execute_tool(
            tool_name="memory_saver",
            parameters={"action": "write", "key": "k", "value": "v"},
            workspace="test_ws",
        )
        assert get_llm_token_usage() == 300


@pytest.mark.asyncio
async def test_error_de_argumentos_determinista_no_reintenta(monkeypatch):
    """'requiere un parámetro' es un fallo determinista (mismos args → mismo
    fallo): reintentar 3× quemaba iteraciones sin posibilidad de éxito."""
    from unittest.mock import AsyncMock, patch

    from tools.orchestrator import ToolOrchestrator

    monkeypatch.setattr("tools.orchestrator.settings", _mock_budget_settings())
    tool_func = AsyncMock(
        return_value={"success": False, "output": "❌ file_manager requiere un parámetro 'path'"}
    )
    with patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func):
        result = await ToolOrchestrator.execute_tool(
            "file_manager", {"action": "create"}, workspace="test_ws"
        )

    assert result["success"] is False
    assert tool_func.call_count == 1  # fast-fail: sin reintentos


@pytest.mark.asyncio
async def test_bloqueo_de_bash_determinista_no_reintenta(monkeypatch):
    from unittest.mock import AsyncMock, patch

    from tools.orchestrator import ToolOrchestrator

    monkeypatch.setattr("tools.orchestrator.settings", _mock_budget_settings())
    tool_func = AsyncMock(
        return_value={
            "success": False,
            "output": "❌ 'find' sobre rutas absolutas está bloqueado (escaneo de todo el sistema)",
        }
    )

    async def _approve(tool_name, params):
        return True

    with (
        patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func),
        patch("tools.orchestrator.get_approval_callback", return_value=_approve),
    ):
        result = await ToolOrchestrator.execute_tool(
            "bash_manager", {"command": "find / -name x"}, workspace="test_ws"
        )

    assert result["success"] is False
    assert tool_func.call_count == 1


class TestFastFailRuidoDegradado:
    @pytest.mark.asyncio
    async def test_repeticion_se_degrada_a_info(self, caplog):
        """El primer fast-fail de un error determinista loggea WARNING; las
        repeticiones idénticas del agente bajan a INFO (el ×12 por run era
        ruido sin información nueva)."""
        import logging as _logging

        from tools import orchestrator as orch

        tool_func = AsyncMock(
            return_value={"success": False, "output": "no hay un repositorio git"}
        )
        orch._fast_fail_warned.clear()
        caplog.set_level(_logging.INFO, logger="tools.orchestrator")
        with (
            patch("tools.orchestrator.settings", _mock_budget_settings(max_budget=100_000)),
            patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func),
        ):
            orch.ToolOrchestrator.reset_token_budget()
            await orch.ToolOrchestrator.execute_tool(
                tool_name="git_manager",
                parameters={"action": "log"},
                workspace="test_ws",
            )
            await orch.ToolOrchestrator.execute_tool(
                tool_name="git_manager",
                parameters={"action": "log"},
                workspace="test_ws",
            )
        warnings = [
            r for r in caplog.records if r.levelno == _logging.WARNING and "fast-fail" in r.message
        ]
        infos = [
            r for r in caplog.records if r.levelno == _logging.INFO and "fast-fail" in r.message
        ]
        assert len(warnings) == 1
        assert len(infos) == 1
        orch._fast_fail_warned.clear()


class TestTokenBudgetWarningHonesto:
    """El warning de overshoot evalúa el gasto COMPROMETIDO.

    Durante una llamada LLM la reserva del input vive en `total` hasta el
    refund; evaluar el warning contra `total` anunciaba "exceeded" en runs que
    terminaban bajo el techo (el log de la sesión bdd: 182768/180000 con un
    total final de 177834).
    """

    def _con_estado(self, estado, monkeypatch):
        import tools.orchestrator as orch

        monkeypatch.setattr(orch.ToolOrchestrator, "ENABLE_TOKEN_BUDGET", True)
        token = orch._token_budget_ctx.set(estado)
        # el contador per-tarea es ContextVar: se restaura para no dejar el
        # valor incrementado en el contexto del proceso de tests.
        token_llm = orch._llm_call_tokens.set(0)
        return token, token_llm

    def test_reserva_activa_no_dispara_warning_falso(self, caplog, monkeypatch):
        import logging as _logging

        import tools.orchestrator as orch
        from tools.orchestrator import _TokenBudgetState

        estado = _TokenBudgetState(max_budget=100)
        estado.total = 80
        assert estado.reserve(10) is True  # total 90, reserva viva
        token, token_llm = self._con_estado(estado, monkeypatch)
        try:
            with caplog.at_level(_logging.WARNING, logger="tools.orchestrator"):
                orch.add_llm_token_usage(15)  # total 105; comprometido 95 < 100
        finally:
            orch._token_budget_ctx.reset(token)
            orch._llm_call_tokens.reset(token_llm)

        assert "Token budget exceeded" not in caplog.text
        estado.refund(10)
        assert estado.total == 95

    def test_overshoot_real_sin_reserva_si_avisa(self, caplog, monkeypatch):
        import logging as _logging

        import tools.orchestrator as orch
        from tools.orchestrator import _TokenBudgetState

        estado = _TokenBudgetState(max_budget=100)
        estado.total = 95
        token, token_llm = self._con_estado(estado, monkeypatch)
        try:
            with caplog.at_level(_logging.WARNING, logger="tools.orchestrator"):
                orch.add_llm_token_usage(10)  # comprometido 105 > 100
        finally:
            orch._token_budget_ctx.reset(token)
            orch._llm_call_tokens.reset(token_llm)

        assert "Token budget exceeded" in caplog.text


@pytest.mark.asyncio
async def test_test_runner_timeout_no_reintenta():
    """Un timeout de test_runner es determinista: el retry arranca otro árbol
    de pytest y amplifica la recursión."""
    from tools.orchestrator import ToolOrchestrator

    tool_func = AsyncMock()
    tool_func.return_value = {
        "success": False,
        "output": "⏱️ Timeout: la ejecución de tests excedió 30s.",
    }
    with patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func):
        result = await ToolOrchestrator.execute_tool(
            tool_name="test_runner",
            parameters={"file_path": "tests/x.py"},
            workspace="test_ws",
        )
    assert result["success"] is False
    assert tool_func.call_count == 1


@pytest.mark.asyncio
async def test_bash_manager_timeout_no_reintenta(monkeypatch):
    """Un timeout de bash es determinista: reintentarlo repite el mismo
    comando bloqueado (development1 perdió 90s con tres intentos)."""
    monkeypatch.setenv("ALLOW_UNATTENDED_DANGEROUS", "true")
    from tools.orchestrator import ToolOrchestrator

    tool_func = AsyncMock(
        return_value={"success": False, "output": "⏱️ Timeout: command exceeded 30s."}
    )
    with patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func):
        result = await ToolOrchestrator.execute_tool(
            tool_name="bash_manager",
            parameters={"command": "echo hi"},
            workspace="test_ws",
        )
    assert result["success"] is False
    assert tool_func.call_count == 1


@pytest.mark.asyncio
async def test_code_exec_timeout_no_reintenta(monkeypatch):
    monkeypatch.setenv("ALLOW_UNATTENDED_DANGEROUS", "true")
    from tools.code_execution import _code_exec_tool
    from tools.orchestrator import ToolOrchestrator

    calls = {"n": 0}

    async def fake_execute(code: str) -> dict:
        calls["n"] += 1
        return {
            "success": False,
            "text": "❌ Execution time exceeded (max 30 seconds). Possible infinite loop.",
            "error_kind": "timeout",
        }

    monkeypatch.setattr("tools.code_execution.CodeExecutor.execute", fake_execute)
    with patch("tools.orchestrator.tools_registry.get_tool", return_value=_code_exec_tool):
        result = await ToolOrchestrator.execute_tool(
            tool_name="code_exec",
            parameters={"code": "while True: pass"},
            workspace="test_ws",
        )
    assert result["success"] is False
    assert result.get("error_kind") == "timeout"
    assert calls["n"] == 1
