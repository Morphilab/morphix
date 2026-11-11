# tests/test_dsl_ruta_determinista.py
"""La ruta del artefacto de verificación la declara el PRESET (Fase A).

Antes, tdd/edd/development/sdd dependían de que el modelo emitiera una línea
`TEST_PATH:` para saber qué archivo verificar: cuando el agente no escribía
nada, `pick_line` no tenía ni línea ni nada que inferir, la verificación corría
sin ruta y —en los presets con `until`— el run moría. Los presets guiados por
lista ya demostraban lo contrario: una ruta declarada por el preset (índice de
ciclo) nunca queda sin resolver.

Cubre además la paridad de `until.metric` con `until.tool`: una clave ausente
en el resultado es "no cumplido" (el loop sigue), no un fallo del run.
"""

import pytest
from _dsl_engine_support import FakeRuntime, _agent, _compile

from orchestration.dsl.engine import WorkflowEngine


class _RuntimeMetricaSecuencial(FakeRuntime):
    """Resultados de test_runner en secuencia: la métrica solo es medible
    cuando el archivo de evals ya existe (el caso real del primer ciclo)."""

    def __init__(self, resultados: list[dict], **over):
        super().__init__(**over)
        self._resultados = list(resultados)

    async def call_tool(self, tool: str, args: dict) -> dict:
        self.tool_calls.append((tool, args))
        if tool == "test_runner" and self._resultados:
            return self._resultados.pop(0)
        return {}


def _doc_slug(**params) -> dict:
    base = {"var": "query", "out": "test_path", "prefix": "tests/test_", "suffix": ".py"}
    base.update(params)
    return _compile(
        [
            {
                "id": "ruta",
                "kind": "plugin",
                "plugin": "slug_path",
                "params": base,
                "outputs": ["test_path"],
            }
        ]
    )


class TestSlugPath:
    @pytest.mark.asyncio
    async def test_deriva_ruta_ascii_desde_la_query(self):
        import orchestration.dsl.plugins  # noqa: F401 — registro de primitivas

        cw = _doc_slug(max_len=120)
        res = await WorkflowEngine(FakeRuntime()).run(
            cw, inputs={"query": "Cálculo de cuóta: préstamo (interés simple)"}
        )
        assert res.status == "completed"
        assert res.vars["test_path"] == "tests/test_calculo_de_cuota_prestamo_interes_simple.py"

    @pytest.mark.asyncio
    async def test_trunca_en_separador_no_a_media_palabra(self):
        import orchestration.dsl.plugins  # noqa: F401

        cw = _doc_slug(max_len=20)
        res = await WorkflowEngine(FakeRuntime()).run(
            cw, inputs={"query": "validador de slugs url friendly con tests"}
        )
        ruta = res.vars["test_path"]
        assert ruta.startswith("tests/test_") and ruta.endswith(".py")
        assert len(ruta) <= len("tests/test_") + 20 + len(".py")
        # sin token cortado: el slug no termina en fragmento de palabra
        assert ruta == "tests/test_validador_de_slugs.py"

    @pytest.mark.asyncio
    async def test_determinista_entre_corridas(self):
        import orchestration.dsl.plugins  # noqa: F401

        cw = _doc_slug()
        a = await WorkflowEngine(FakeRuntime()).run(cw, inputs={"query": "misma query"})
        b = await WorkflowEngine(FakeRuntime()).run(cw, inputs={"query": "misma query"})
        assert a.vars["test_path"] == b.vars["test_path"]

    @pytest.mark.asyncio
    async def test_var_inexistente_falla_loud(self):
        import orchestration.dsl.plugins  # noqa: F401

        cw = _doc_slug(var="no_existe")
        res = await WorkflowEngine(FakeRuntime()).run(cw, inputs={"query": "q"})
        assert res.status == "failed"
        assert "no_existe" in str(res.failure)

    @pytest.mark.asyncio
    async def test_params_incompletos_fallan_loud(self):
        import orchestration.dsl.plugins  # noqa: F401

        cw = _compile(
            [{"id": "ruta", "kind": "plugin", "plugin": "slug_path", "params": {"var": "query"}}]
        )
        res = await WorkflowEngine(FakeRuntime()).run(cw, inputs={"query": "q"})
        assert res.status == "failed"
        assert "slug_path" in str(res.failure)

    @pytest.mark.asyncio
    async def test_no_pisa_last_output(self):
        """La ruta derivada vive en su var `out`: el contexto de la
        conversación ($last_output) lo escriben los agentes, no el plugin."""
        import orchestration.dsl.plugins  # noqa: F401

        cw = _compile(
            [
                _agent("previo"),
                {
                    "id": "ruta",
                    "kind": "plugin",
                    "plugin": "slug_path",
                    "params": {
                        "var": "query",
                        "out": "test_path",
                        "prefix": "tests/test_",
                        "suffix": ".py",
                    },
                    "outputs": ["test_path"],
                },
            ]
        )
        res = await WorkflowEngine(FakeRuntime()).run(cw, inputs={"query": "q"})
        assert res.status == "completed"
        assert res.vars["test_path"] == "tests/test_q.py"
        assert res.vars["last_output"] == "salida estandar"


class TestUntilMetricaSinMedicion:
    """Sin medición el loop recibe DOS vueltas de gracia; a la tercera falla.

    Tolerar indefinidamente quema el presupuesto del loop sin poder evaluar su
    meta; morir en la primera medición no le daba oportunidad. La segunda
    gracia cubre un bache pasajero del modelo sin abrir la puerta al loop
    ciego: el corte sigue muy lejos de max_iter.
    """

    def _doc(self) -> dict:
        return _compile(
            [
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "max_iter": 10,
                    "body": [_agent("implementar")],
                    "until": {
                        "type": "metric",
                        "tool": "test_runner",
                        "metric": "passed_count",
                        "op": ">=",
                        "value": 5,
                        "args": {"file_path": "tests/test_x.py"},
                    },
                }
            ]
        )

    @pytest.mark.asyncio
    async def test_una_medicion_imposible_no_mata_el_run(self):
        """El body puede reparar el artefacto que falta: la primera medición
        imposible da una vuelta más."""
        rt = _RuntimeMetricaSecuencial(
            [
                {"output": "ERROR: file or directory not found", "success": False},
                {"passed_count": 5, "failed_count": 0, "tests_all_pass": True},
            ]
        )
        res = await WorkflowEngine(rt).run(self._doc(), inputs={"query": "q"})

        assert res.status == "completed", res.failure
        assert res.errors == [], "un no-medido puntual no es error del run"
        assert len([t for t in rt.tool_calls if t[0] == "test_runner"]) == 2

    @pytest.mark.asyncio
    async def test_recupera_a_la_tercera_tras_dos_imposibles(self):
        """Las dos gracias existen para esto: un bache de dos no-medidos no
        condena el run si la tercera mide."""
        rt = _RuntimeMetricaSecuencial(
            [
                {"output": "sin conteos"},
                {"output": "sin conteos"},
                {"passed_count": 5, "failed_count": 0, "tests_all_pass": True},
            ]
        )
        res = await WorkflowEngine(rt).run(self._doc(), inputs={"query": "q"})
        assert res.status == "completed", res.failure
        assert len([t for t in rt.tool_calls if t[0] == "test_runner"]) == 3

    @pytest.mark.asyncio
    async def test_tres_mediciones_imposibles_seguidas_fallan_honesto(self):
        rt = _RuntimeMetricaSecuencial([{"output": "sin conteos"}])
        res = await WorkflowEngine(rt).run(self._doc(), inputs={"query": "q"})

        assert res.status == "failed"
        assert "sin poder medirse" in str(res.failure)
        # no agotó max_iter: cortó en el tercer intento
        assert len([t for t in rt.tool_calls if t[0] == "test_runner"]) == 3

    @pytest.mark.asyncio
    async def test_medicion_que_vuelve_resetea_la_racha(self):
        """Medir-y-no-cumplir entre dos imposibles no cuenta como racha."""
        rt = _RuntimeMetricaSecuencial(
            [
                {"output": "sin conteos"},  # imposible → gracia
                {"passed_count": 1, "failed_count": 3, "tests_all_pass": False},
                {"passed_count": 5, "failed_count": 0, "tests_all_pass": True},
            ]
        )
        res = await WorkflowEngine(rt).run(self._doc(), inputs={"query": "q"})
        assert res.status == "completed", res.failure
        assert len([t for t in rt.tool_calls if t[0] == "test_runner"]) == 3


class TestUntilToolClaveAusente:
    """Paridad de until.tool: la clave AUSENTE es "no medido", no "en rojo".

    Antes, un resultado sin la clave (verificación que no corrió: ruta
    inexistente, entorno roto) se leía como False y el loop agotaba max_iter
    × presupuesto sobre un artefacto inexistente.
    """

    @pytest.mark.asyncio
    async def test_clave_ausente_agota_gracia_y_falla_rapido(self):
        rt = _RuntimeMetricaSecuencial([{"success": False, "output": "no encontrado"}])
        cw = _compile(
            [
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "max_iter": 10,
                    "body": [_agent("implementar")],
                    "until": {
                        "type": "tool",
                        "tool": "test_runner",
                        "check": "tests_all_pass",
                        "args": {"file_path": "tests/test_x.py"},
                    },
                }
            ]
        )
        res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert res.status == "failed"
        assert "sin poder medirse" in str(res.failure)
        # dos gracias y corta: no agotó max_iter
        assert len([t for t in rt.tool_calls if t[0] == "test_runner"]) == 3

    @pytest.mark.asyncio
    async def test_clave_presente_en_rojo_sigue_siendo_medicion(self):
        """False EXPLÍCITO es una medición válida: el loop sigue hasta
        max_iter (el rojo normal del TDD es su razón de ser)."""
        rt = _RuntimeMetricaSecuencial(
            [{"tests_all_pass": False}, {"tests_all_pass": False}, {"tests_all_pass": False}]
        )
        cw = _compile(
            [
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "max_iter": 3,
                    "body": [_agent("implementar")],
                    "until": {
                        "type": "tool",
                        "tool": "test_runner",
                        "check": "tests_all_pass",
                        "args": {"file_path": "tests/test_x.py"},
                    },
                }
            ]
        )
        res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert res.status == "completed"
        assert len([t for t in rt.tool_calls if t[0] == "test_runner"]) == 3


# ── cap_text: dieta de prompt declarada por el preset ────────────────────


def _doc_cap(**params) -> dict:
    base = {"var": "modelo", "out": "modelo_cap", "max_chars": 40}
    base.update(params)
    return _compile(
        [
            {
                "id": "cap",
                "kind": "plugin",
                "plugin": "cap_text",
                "params": base,
                "outputs": [base["out"]],
            }
        ]
    )


async def _run_cap_doc(doc: dict, inputs: dict | None = None):
    import orchestration.dsl.plugins  # noqa: F401 — registro de primitivas

    rt = FakeRuntime()
    engine = WorkflowEngine(rt)
    modelo = (inputs or {}).get(
        "modelo", "entidad Cliente con invariantes de saldo y " + "palabras " * 30
    )
    return await engine.run(doc, inputs={"modelo": modelo, "query": "x"})


async def test_cap_text_acota_en_frontera_de_palabra():
    res = await _run_cap_doc(_doc_cap(max_chars=40))
    cap = res.vars["modelo_cap"]
    assert len(cap) <= 41  # margen del ellipsis
    assert cap.endswith("…") or len(cap) < 40


async def test_cap_text_conserva_la_var_original():
    largo = "entidad Cliente con invariantes de saldo y " + "palabras " * 30
    res = await _run_cap_doc(_doc_cap(max_chars=40), inputs={"modelo": largo})
    assert res.vars["modelo"] == largo, "la var ORIGINAL debe quedar íntegra"


async def test_cap_text_parametros_obligatorios_fail_loud():
    cw = _doc_cap(var="", out="x")
    res = await _run_cap_doc(cw)
    assert res.status == "failed"
    cw = _doc_cap(max_chars=0)
    res = await _run_cap_doc(cw)
    assert res.status == "failed"


async def test_cap_text_var_sin_valor_fail_loud():
    import orchestration.dsl.plugins  # noqa: F401

    res = await WorkflowEngine(FakeRuntime()).run(_doc_cap(), inputs={"query": "x"})  # sin 'modelo'
    assert res.status == "failed"
    assert "modelo" in str(res.failure)
