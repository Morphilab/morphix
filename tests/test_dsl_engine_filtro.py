"""`filtrar_items` descarta subtareas prohibidas ANTES del loop.

La prohibición en el query del decompose es soft: el modelo genera
"Leer y analizar X" o "Crear Y.md" como subtareas, con implementaciones
paralelas del mismo dominio. El filtro es determinista: no depende de la
buena voluntad del modelo.
"""

import pytest
from _dsl_engine_support import FakeRuntime, _agent, _compile

from orchestration.dsl.engine import WorkflowEngine

PATRONES = ["leer y analizar", "crear .*\\.md", "crear test", "ejecutar los tests"]


class _PlanRT(FakeRuntime):
    async def decompose(self, query: str, strategy: str):
        return [
            "Leer y analizar verify2.py para entender split()",
            "Crear spec.md con la especificación del contador",
            "Crear word_counter.py implementando count_words",
            "Crear test_word_counter.py con tests unitarios",
            "Ejecutar los tests con pytest para verificar",
        ]


def _filtro(preset_id: str = "filtro") -> dict:
    return {
        "id": preset_id,
        "kind": "plugin",
        "plugin": "filtrar_items",
        "params": {"var": "subtasks", "out": "subtasks", "patrones": PATRONES},
    }


@pytest.mark.asyncio
async def test_descarta_analisis_doc_y_tests():
    import orchestration.dsl.plugins  # noqa: F401

    rt = _PlanRT()
    cw = _compile(
        [
            {"id": "plan", "kind": "decompose", "strategy": "flat", "output": "subtasks"},
            _filtro(),
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert res.status == "completed"
    assert res.vars["subtasks"] == ["Crear word_counter.py implementando count_words"]
    assert rt._degraded_steps == ["plugin:filtrar_items:subtasks"]


@pytest.mark.asyncio
async def test_sin_descartes_no_degrada_y_conserva_el_orden():
    import orchestration.dsl.plugins  # noqa: F401

    class _Limpio(FakeRuntime):
        async def decompose(self, query, strategy):
            return ["Crear modulo.py", "Extender modulo.py con api"]

    rt = _Limpio()
    cw = _compile(
        [
            {"id": "plan", "kind": "decompose", "strategy": "flat", "output": "subtasks"},
            _filtro(),
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert res.vars["subtasks"] == ["Crear modulo.py", "Extender modulo.py con api"]
    assert rt._degraded_steps == []


@pytest.mark.asyncio
async def test_var_ausente_falla_loud():
    import orchestration.dsl.plugins  # noqa: F401

    rt = FakeRuntime()
    cw = _compile([_filtro()])
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert res.status == "failed"


@pytest.mark.asyncio
async def test_patrones_vacios_fallan_loud():
    import orchestration.dsl.plugins  # noqa: F401

    rt = FakeRuntime()
    cw = _compile(
        [
            {"id": "plan", "kind": "decompose", "strategy": "flat", "output": "subtasks"},
            {
                "id": "filtro",
                "kind": "plugin",
                "plugin": "filtrar_items",
                "params": {"var": "subtasks", "out": "subtasks", "patrones": []},
            },
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert res.status == "failed"


@pytest.mark.asyncio
async def test_regex_invalido_falla_loud():
    import orchestration.dsl.plugins  # noqa: F401

    rt = FakeRuntime()
    cw = _compile(
        [
            {"id": "plan", "kind": "decompose", "strategy": "flat", "output": "subtasks"},
            {
                "id": "filtro",
                "kind": "plugin",
                "plugin": "filtrar_items",
                "params": {"var": "subtasks", "out": "subtasks", "patrones": ["("]},
            },
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert res.status == "failed"


@pytest.mark.asyncio
async def test_var_original_intacta_con_out_distinto():
    import orchestration.dsl.plugins  # noqa: F401

    rt = _PlanRT()
    cw = _compile(
        [
            {"id": "plan", "kind": "decompose", "strategy": "flat", "output": "subtasks"},
            {
                "id": "filtro",
                "kind": "plugin",
                "plugin": "filtrar_items",
                "params": {
                    "var": "subtasks",
                    "out": "subtasks_impl",
                    "patrones": PATRONES,
                },
            },
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert res.vars["subtasks_impl"] == ["Crear word_counter.py implementando count_words"]
    assert len(res.vars["subtasks"]) == 5  # la original queda intacta


class TestDescartadosNoQuedanPendientes:
    """Un ítem descartado por el filtro no puede quedar `pending` en el plan:
    el scorecard diría trabajo pendiente que el motor mismo descartó."""

    @pytest.mark.asyncio
    async def test_items_filtrados_quedan_skipped(self):
        import orchestration.dsl.plugins  # noqa: F401

        class _Plan3(FakeRuntime):
            async def decompose(self, query, strategy):
                return [
                    "Leer y analizar verify2.py",
                    "Crear modulo.py con la API",
                    "Crear test_modulo.py con tests",
                ]

        rt = _Plan3()
        cw = _compile(
            [
                {"id": "plan", "kind": "decompose", "strategy": "flat", "output": "subtasks"},
                {
                    "id": "filtro",
                    "kind": "plugin",
                    "plugin": "filtrar_items",
                    "params": {
                        "var": "subtasks",
                        "out": "subtasks",
                        "patrones": ["leer y analizar"],
                    },
                },
                {
                    "id": "ejecutar",
                    "kind": "loop",
                    "max_iter": 5,
                    "over": "subtasks",
                    "body": [_agent("impl")],
                },
            ]
        )
        res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        plan = {p["id"]: p for p in res.run_state["plan"] if p["kind"] == "subtask"}
        assert plan["ejecutar#1"]["status"] == "delivered"
        assert plan["ejecutar#2"]["status"] == "delivered"
        assert plan["ejecutar#3"]["status"] == "skipped"
        assert any("descartado" in n for n in plan["ejecutar#3"]["notes"])


DEV_PATRONES = [
    "leer y analizar",
    "analizar el proyecto",
    "explorar el repo",
    "ejecutar los tests",
    "verificar la spec",
]
SDD_PATRONES = DEV_PATRONES + ["crear test", "crear spec", "documenta"]


@pytest.mark.asyncio
async def test_set_development_conserva_test_y_readme():
    """En development/coordinated las subtareas de test/README SON entregables
    del decompose: el filtro solo saca análisis y meta-verificación."""
    import orchestration.dsl.plugins  # noqa: F401

    rt = _PlanRT()
    cw = _compile(
        [
            {"id": "plan", "kind": "decompose", "strategy": "flat", "output": "subtasks"},
            {
                "id": "filtro",
                "kind": "plugin",
                "plugin": "filtrar_items",
                "params": {"var": "subtasks", "out": "subtasks", "patrones": DEV_PATRONES},
            },
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert res.vars["subtasks"] == [
        "Crear spec.md con la especificación del contador",
        "Crear word_counter.py implementando count_words",
        "Crear test_word_counter.py con tests unitarios",
    ]


@pytest.mark.asyncio
async def test_set_sdd_descarta_test_docs_y_verificacion():
    import orchestration.dsl.plugins  # noqa: F401

    rt = _PlanRT()
    cw = _compile(
        [
            {"id": "plan", "kind": "decompose", "strategy": "flat", "output": "subtasks"},
            {
                "id": "filtro",
                "kind": "plugin",
                "plugin": "filtrar_items",
                "params": {"var": "subtasks", "out": "subtasks", "patrones": SDD_PATRONES},
            },
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert res.vars["subtasks"] == ["Crear word_counter.py implementando count_words"]


@pytest.mark.asyncio
async def test_reconciliacion_no_pisa_estados_finales():
    """Un recorte POSTERIOR al loop (o un resume) no puede convertir un
    delivered/failed en skipped: solo pending/running se reconcilian."""
    import orchestration.dsl.plugins  # noqa: F401

    class _Dos(FakeRuntime):
        async def decompose(self, query, strategy):
            return ["Crear modulo.py con api", "Extender modulo.py"]

    rt = _Dos()
    cw = _compile(
        [
            {"id": "plan", "kind": "decompose", "strategy": "flat", "output": "subtasks"},
            {
                "id": "ejecutar",
                "kind": "loop",
                "max_iter": 5,
                "over": "subtasks",
                "body": [_agent("impl")],
            },
            {
                "id": "recorte",
                "kind": "plugin",
                "plugin": "filtrar_items",
                "params": {"var": "subtasks", "out": "subtasks", "patrones": ["Crear modulo"]},
            },
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    plan = {p["id"]: p for p in res.run_state["plan"] if p["kind"] == "subtask"}
    assert plan["ejecutar#1"]["status"] == "delivered"  # NO se pisa a skipped
    assert plan["ejecutar#2"]["status"] == "delivered"


@pytest.mark.asyncio
async def test_el_patron_de_docs_de_sdd_matchea_documentando():
    """`documentar` no es substring de 'documentando' (r vs n): el README
    sobrevivía al filtro, se bloqueaba por budget y el run cerraba Parcial
    con el README ya escrito. El stem `documenta` cubre las variantes."""
    import orchestration.dsl.plugins  # noqa: F401

    class _Docs(FakeRuntime):
        async def decompose(self, query, strategy):
            return [
                "Crear word_counter.py implementando count_words",
                "Completar README.md documentando uso y decisiones",
                "Escribir documentación de la API en docs/api.md",
            ]

    rt = _Docs()
    cw = _compile(
        [
            {"id": "plan", "kind": "decompose", "strategy": "flat", "output": "subtasks"},
            {
                "id": "filtro",
                "kind": "plugin",
                "plugin": "filtrar_items",
                "params": {"var": "subtasks", "out": "subtasks", "patrones": SDD_PATRONES},
            },
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert res.vars["subtasks"] == ["Crear word_counter.py implementando count_words"]


@pytest.mark.asyncio
async def test_filtro_que_descarta_el_medio_no_etiqueta_mal_el_plan():
    """El plan mapea por índice: si el filtro saca un ítem del MEDIO, el ítem
    entregado conservaba el goal del descartado y el saltado mostraba uno
    implementado. Los goals del plan deben seguir a la lista vigente y los
    saltados mostrar los descartados reales."""
    import orchestration.dsl.plugins  # noqa: F401

    class _Tres(FakeRuntime):
        async def decompose(self, query, strategy):
            return [
                "Implementar validate_email en email_validator.py",
                "Crear test_email_validator.py con casos",
                "Exponer mensajes de error descriptivos",
            ]

    rt = _Tres()
    cw = _compile(
        [
            {"id": "plan", "kind": "decompose", "strategy": "flat", "output": "subtasks"},
            {
                "id": "filtro",
                "kind": "plugin",
                "plugin": "filtrar_items",
                "params": {"var": "subtasks", "out": "subtasks", "patrones": SDD_PATRONES},
            },
            {
                "id": "implementar",
                "kind": "loop",
                "max_iter": 3,
                "over": "subtasks",
                "body": [
                    {"id": "subtarea", "kind": "agent", "agent": "developer", "goal": "$item"}
                ],
            },
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    subs = [p for p in res.run_state["plan"] if p["kind"] == "subtask"]
    assert [s["goal"] for s in subs] == [
        "Implementar validate_email en email_validator.py",
        "Exponer mensajes de error descriptivos",
        "Crear test_email_validator.py con casos",
    ]
    assert [s["status"] for s in subs] == ["delivered", "delivered", "skipped"]
    assert "descartado" in " ".join(subs[2]["notes"])


@pytest.mark.asyncio
async def test_descarte_por_contrato_no_degrada_ni_cuenta_como_pendiente():
    """sdd descarta tests/docs POR CONTRATO (el body los escribe): el plan los
    muestra saltados, pero el proceso no degrada y no cuentan como entrega
    pendiente. Un descarte NO declarado sigue degradando."""
    import orchestration.dsl.plugins  # noqa: F401

    class _Dos(FakeRuntime):
        async def decompose(self, query, strategy):
            return ["Implementar validator.py", "Crear test_validator.py con casos"]

    def _cw(contrato: bool):
        params = {"var": "subtasks", "out": "subtasks", "patrones": ["crear test"]}
        if contrato:
            params["contrato"] = True
        return _compile(
            [
                {"id": "plan", "kind": "decompose", "strategy": "flat", "output": "subtasks"},
                {"id": "filtro", "kind": "plugin", "plugin": "filtrar_items", "params": params},
                {
                    "id": "implementar",
                    "kind": "loop",
                    "max_iter": 2,
                    "over": "subtasks",
                    "body": [
                        {"id": "subtarea", "kind": "agent", "agent": "developer", "goal": "$item"}
                    ],
                },
            ]
        )

    rt = _Dos()
    res = await WorkflowEngine(rt).run(_cw(True), inputs={"query": "q"})
    assert rt._degraded_steps == [], rt._degraded_steps
    subs = [p for p in res.run_state["plan"] if p["kind"] == "subtask"]
    assert [s["status"] for s in subs] == ["delivered", "skipped"]
    assert "contrato" in " ".join(subs[1]["notes"])

    rt2 = _Dos()
    await WorkflowEngine(rt2).run(_cw(False), inputs={"query": "q"})
    assert rt2._degraded_steps, "un descarte no declarado debe degradar"


@pytest.mark.asyncio
async def test_descartado_cuyo_artefacto_ya_existe_queda_satisfecho():
    """Un 'Completar README.md…' descartado cuyo README ya escribió otra
    subtarea no es trabajo pendiente: skip con satisfied_by (evidencia)."""
    import orchestration.dsl.plugins  # noqa: F401

    class _Readme(FakeRuntime):
        def __init__(self):
            super().__init__(agent_output="ok", files_written=["README.md"])

        async def decompose(self, query, strategy):
            return ["Implementar modulo.py", "Completar README.md documentando uso"]

    rt = _Readme()
    cw = _compile(
        [
            {"id": "plan", "kind": "decompose", "strategy": "flat", "output": "subtasks"},
            {
                "id": "filtro",
                "kind": "plugin",
                "plugin": "filtrar_items",
                "params": {
                    "var": "subtasks",
                    "out": "subtasks",
                    "patrones": ["documenta"],
                    "contrato": True,
                },
            },
            {
                "id": "implementar",
                "kind": "loop",
                "max_iter": 2,
                "over": "subtasks",
                "body": [
                    {"id": "subtarea", "kind": "agent", "agent": "developer", "goal": "$item"}
                ],
            },
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    subs = [p for p in res.run_state["plan"] if p["kind"] == "subtask"]
    assert subs[1]["status"] == "skipped"
    assert subs[1]["satisfied_by"] == "README.md"
    counts = {p["id"]: p for p in res.run_state["plan"]}
    assert "README" in counts["implementar#2"]["goal"]
