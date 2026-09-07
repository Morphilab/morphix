# tests/test_dsl_registry.py
"""Tests del registro de primitivas plugin (escape hatch de extensibilidad).

Un plugin registrado se ejecuta vía ``kind: plugin``; uno no registrado
falla loud. El motor no cambia.
"""

import pytest

from orchestration.dsl.engine import WorkflowEngine
from orchestration.dsl.registry import (
    _PRIMITIVES,
    get_primitive,
    known_primitives,
    register_primitive,
)
from tests.test_dsl_engine import FakeRuntime, _compile


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_PRIMITIVES)
    yield
    _PRIMITIVES.clear()
    _PRIMITIVES.update(saved)


class TestRegistry:
    def test_registro_y_get(self):
        @register_primitive("eco_test")
        async def eco(step, state, runtime, qpath):
            state.vars["eco"] = "ok"

        assert get_primitive("eco_test") is eco
        assert "eco_test" in known_primitives()

    def test_duplicado_rechazado(self):
        @register_primitive("duplicado_test")
        async def a(step, state, runtime, qpath):
            return None

        with pytest.raises(ValueError, match="duplicado"):

            @register_primitive("duplicado_test")
            async def b(step, state, runtime, qpath):
                return None


class TestPluginStepEnMotor:
    @pytest.mark.asyncio
    async def test_plugin_ejecuta_y_muta_vars(self):
        @register_primitive("esperar_evento_test")
        async def esperar(step, state, runtime, qpath):
            state.vars[step.params.get("var", "evento")] = step.params.get("valor", "listo")

        rt = FakeRuntime()
        cw = _compile(
            [
                {
                    "id": "esp",
                    "kind": "plugin",
                    "plugin": "esperar_evento_test",
                    "params": {"var": "evento", "valor": "recibido"},
                },
                {"id": "usa", "kind": "agent", "agent": "developer", "goal": "procesa $evento"},
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert rt.agent_calls[0][1] == "procesa recibido"

    @pytest.mark.asyncio
    async def test_plugin_no_registrado_falla_loud(self):
        rt = FakeRuntime()
        cw = _compile([{"id": "esp", "kind": "plugin", "plugin": "no_existe_nunca"}])
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "failed"
        assert "no_existe_nunca" in (result.failure or "")

    @pytest.mark.asyncio
    async def test_plugin_error_respeta_on_error(self):
        @register_primitive("explota_test")
        async def explota(step, state, runtime, qpath):
            raise RuntimeError("boom")

        rt = FakeRuntime()
        cw = _compile(
            [
                {"id": "malo", "kind": "plugin", "plugin": "explota_test", "on_error": "continue"},
                {"id": "sigue", "kind": "agent", "agent": "developer"},
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert result.errors
