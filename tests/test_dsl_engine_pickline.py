# tests/test_dsl_engine_pickline.py — split de test_dsl_engine (pick_line y loops)
"""Contratos de plugins de lista: pick_line (rutas, tolerancia, no-repetición),
convergencia por ítem y truncamiento de loops."""


import pytest
from _dsl_engine_support import FakeRuntime, _agent, _compile

from orchestration.dsl.engine import WorkflowEngine


class TestConvergenciaPorItem:
    """Bdd/domain_tdd: lista limpia del agente y verificación POR ítem."""

    @pytest.mark.asyncio
    async def test_text_to_list_con_marker_ignora_la_prosa(self):
        import orchestration.dsl.plugins  # noqa: F401 — registro de primitivas

        rt = FakeRuntime(
            agent_output="Prosa del analista.\n# Título\nPárrafo.\n\n## LISTA\n- historia uno\n- historia dos"
        )
        cw = _compile(
            [
                _agent("extraer", outputs=["historias"]),
                {
                    "id": "parse",
                    "kind": "plugin",
                    "plugin": "text_to_list",
                    "params": {"var": "historias", "out": "lista", "marker": "## LISTA"},
                },
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        # sin el marker, cada línea del documento era un ítem y el loop
        # quemaba su tope iterando sobre títulos y prosa
        assert result.vars["lista"] == ["historia uno", "historia dos"]

    @pytest.mark.asyncio
    async def test_tool_step_args_interpola_iter_del_loop(self):
        rt = FakeRuntime()
        cw = _compile(
            [
                {"id": "items", "kind": "decompose", "strategy": "flat", "output": "tareas"},
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "over": "tareas",
                    "max_iter": 5,
                    "body": [
                        {
                            "id": "tests",
                            "kind": "tool",
                            "tool": "test_runner",
                            "args": {"file_path": "tests/test_historia_$iter.py"},
                        }
                    ],
                },
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        paths = [args["file_path"] for _tool, args in rt.tool_calls]
        # cada iteración verifica SU archivo: la verificación nunca hereda
        # los fallos de otras historias
        assert paths == ["tests/test_historia_1.py", "tests/test_historia_2.py"]


class TestPickLine:
    """La ruta del test como variable de runtime (until acotado por archivo)."""

    @pytest.mark.asyncio
    async def test_pick_line_extrae_prefijo_a_var(self):
        import orchestration.dsl.plugins  # noqa: F401 — registro de primitivas

        rt = FakeRuntime(agent_output="Prosa del agente.\nTEST_PATH: tests/test_login.py")
        cw = _compile(
            [
                _agent("paso", outputs=["raw"]),
                {
                    "id": "p",
                    "kind": "plugin",
                    "plugin": "pick_line",
                    "params": {"var": "raw", "out": "ruta", "prefix": "TEST_PATH:"},
                },
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert result.vars["ruta"] == "tests/test_login.py"

    @pytest.mark.asyncio
    async def test_pick_line_sin_linea_deja_vacio(self):
        import orchestration.dsl.plugins  # noqa: F401

        rt = FakeRuntime(agent_output="sin la línea")
        cw = _compile(
            [
                _agent("paso", outputs=["raw"]),
                {
                    "id": "p",
                    "kind": "plugin",
                    "plugin": "pick_line",
                    "params": {"var": "raw", "out": "ruta", "prefix": "TEST_PATH:"},
                },
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert result.vars["ruta"] == ""


class TestTruncamientoDeLoop:
    """Un tope que trunca una lista `over` no puede ser silencioso."""

    @pytest.mark.asyncio
    async def test_loop_over_truncado_degrada(self):
        rt = FakeRuntime()
        cw = _compile(
            [
                {"id": "items", "kind": "decompose", "strategy": "flat", "output": "tareas"},
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "over": "tareas",
                    "max_iter": 1,
                    "body": [_agent("subtarea")],
                },
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert rt._degraded_steps == ["loop:ciclo"]

    @pytest.mark.asyncio
    async def test_loop_completo_no_degrada(self):
        rt = FakeRuntime()
        cw = _compile(
            [
                {"id": "items", "kind": "decompose", "strategy": "flat", "output": "tareas"},
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "over": "tareas",
                    "max_iter": 5,
                    "body": [_agent("subtarea")],
                },
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert rt._degraded_steps == []


class TestListaVaciaConMarker:
    """text_to_list con marker y 0 ítems: el loop que no corre no es 'Alta'."""

    @pytest.mark.asyncio
    async def test_cero_items_tras_marker_degrada(self):
        import orchestration.dsl.plugins  # noqa: F401

        rt = FakeRuntime(agent_output="El analista no siguió el formato.")
        cw = _compile(
            [
                _agent("extraer", outputs=["historias"]),
                {
                    "id": "parse",
                    "kind": "plugin",
                    "plugin": "text_to_list",
                    "params": {"var": "historias", "out": "lista", "marker": "## LISTA"},
                },
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert result.vars["lista"] == []
        assert rt._degraded_steps == ["plugin:text_to_list:lista"]


@pytest.mark.asyncio
async def test_marker_ausente_degrada_y_no_convierte_prosa_en_items():
    """Sin '## LISTA' el texto completo NO se divide (la prosa no vuelve a
    ser ítems): contrato violado ⇒ lista vacía + degradada."""
    import orchestration.dsl.plugins  # noqa: F401

    rt = FakeRuntime(agent_output="Prosa.\n- esto parece item\n- y esto otro")
    cw = _compile(
        [
            _agent("extraer", outputs=["historias"]),
            {
                "id": "parse",
                "kind": "plugin",
                "plugin": "text_to_list",
                "params": {"var": "historias", "out": "lista", "marker": "## LISTA"},
            },
        ]
    )
    result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert result.status == "completed"
    assert result.vars["lista"] == []
    assert rt._degraded_steps == ["plugin:text_to_list:lista"]


class TestPickLineTolerante:
    """El modelo escribe el marcador con decoración markdown: el match
    sobrevive a backticks, negritas, viñetas y blockquote."""

    @pytest.mark.asyncio
    async def test_backticks_alrededor_de_la_linea(self):
        import orchestration.dsl.plugins  # noqa: F401

        rt = FakeRuntime(agent_output="prosa\n`TEST_PATH: tests/test_login.py`")
        cw = _compile(
            [
                _agent("paso", outputs=["raw"]),
                {
                    "id": "p",
                    "kind": "plugin",
                    "plugin": "pick_line",
                    "params": {"var": "raw", "out": "ruta", "prefix": "TEST_PATH:"},
                },
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.vars["ruta"] == "tests/test_login.py"

    @pytest.mark.asyncio
    async def test_negritas_y_vineta(self):
        import orchestration.dsl.plugins  # noqa: F401

        rt = FakeRuntime(agent_output="- **TEST_PATH:** `tests/test_x.py`")
        cw = _compile(
            [
                _agent("paso", outputs=["raw"]),
                {
                    "id": "p",
                    "kind": "plugin",
                    "plugin": "pick_line",
                    "params": {"var": "raw", "out": "ruta", "prefix": "TEST_PATH:"},
                },
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.vars["ruta"] == "tests/test_x.py"

    @pytest.mark.asyncio
    async def test_fallo_loguea_el_head_para_diagnostico(self, caplog):
        import logging

        import orchestration.dsl.plugins  # noqa: F401

        rt = FakeRuntime(agent_output="escribí el archivo en otra parte")
        cw = _compile(
            [
                _agent("paso", outputs=["raw"]),
                {
                    "id": "p",
                    "kind": "plugin",
                    "plugin": "pick_line",
                    "params": {"var": "raw", "out": "ruta", "prefix": "TEST_PATH:"},
                },
            ]
        )
        with caplog.at_level(logging.WARNING, logger="orchestration.dsl.plugins"):
            await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert "ninguna línea con prefijo" in caplog.text
        assert "head:" in caplog.text
        assert "otra parte" in caplog.text


class TestCapYFallback:
    """Cap declarado en la lista (antes de quemar presupuesto) y fallback
    determinista de ruta cuando el contrato TEST_PATH se viola."""

    @pytest.mark.asyncio
    async def test_max_items_trunca_antes_de_ejecutar(self):
        import orchestration.dsl.plugins  # noqa: F401

        rt = FakeRuntime(agent_output="## LISTA\n- e1\n- e2\n- e3\n- e4\n- e5\n- e6\n- e7")
        cw = _compile(
            [
                _agent("paso", outputs=["raw"]),
                {
                    "id": "p",
                    "kind": "plugin",
                    "plugin": "text_to_list",
                    "params": {
                        "var": "raw",
                        "out": "lista",
                        "marker": "## LISTA",
                        "max_items": 5,
                    },
                },
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert len(result.vars["lista"]) == 5
        # el descarte de trabajo cuenta como degradación (Parcial honesto)
        assert rt._degraded_steps == ["plugin:text_to_list:lista:truncada"]

    @pytest.mark.asyncio
    async def test_max_items_sin_exceso_no_degrada(self):
        import orchestration.dsl.plugins  # noqa: F401

        rt = FakeRuntime(agent_output="## LISTA\n- e1\n- e2")
        cw = _compile(
            [
                _agent("paso", outputs=["raw"]),
                {
                    "id": "p",
                    "kind": "plugin",
                    "plugin": "text_to_list",
                    "params": {"var": "raw", "out": "lista", "marker": "## LISTA", "max_items": 5},
                },
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.vars["lista"] == ["e1", "e2"]
        assert rt._degraded_steps == []

    @pytest.mark.asyncio
    async def test_fallback_infiere_tests_mas_reciente(self, tmp_path):
        import time

        import orchestration.dsl.plugins  # noqa: F401

        (tmp_path / "tests").mkdir()
        viejo = tmp_path / "tests" / "test_viejo.py"
        nuevo = tmp_path / "tests" / "test_evals.py"
        viejo.write_text("x")
        time.sleep(0.02)
        nuevo.write_text("y")  # el más reciente: lo que el paso acaba de escribir

        rt = FakeRuntime(agent_output="⚠️ Límite de 8 iteraciones...")  # contrato violado
        rt.project_root = str(tmp_path)
        cw = _compile(
            [
                _agent("paso", outputs=["raw"]),
                {
                    "id": "p",
                    "kind": "plugin",
                    "plugin": "pick_line",
                    "params": {
                        "var": "raw",
                        "out": "ruta",
                        "prefix": "TEST_PATH:",
                        "fallback_glob": "tests/*.py",
                    },
                },
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.vars["ruta"] == "tests/test_evals.py"
        assert rt._degraded_steps == ["pick_line:ruta:ruta-inferida"]

    @pytest.mark.asyncio
    async def test_fallback_sin_matches_sin_proyecto_deja_vacio(self, tmp_path):
        import orchestration.dsl.plugins  # noqa: F401

        # proyecto real pero sin tests/: sin candidatos, valor ""
        rt = FakeRuntime(agent_output="sin línea")
        rt.project_root = str(tmp_path)
        cw = _compile(
            [
                _agent("paso", outputs=["raw"]),
                {
                    "id": "p",
                    "kind": "plugin",
                    "plugin": "pick_line",
                    "params": {
                        "var": "raw",
                        "out": "ruta",
                        "prefix": "TEST_PATH:",
                        "fallback_glob": "tests/*.py",
                    },
                },
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.vars["ruta"] == ""
        assert rt._degraded_steps == []

        # sin proyecto real el fallback NO toca el filesystem
        rt2 = FakeRuntime(agent_output="sin línea")
        cw2 = _compile(
            [
                _agent("paso", outputs=["raw"]),
                {
                    "id": "p",
                    "kind": "plugin",
                    "plugin": "pick_line",
                    "params": {
                        "var": "raw",
                        "out": "ruta",
                        "prefix": "TEST_PATH:",
                        "fallback_glob": "tests/*.py",
                    },
                },
            ]
        )
        result2 = await WorkflowEngine(rt2).run(cw2, inputs={"query": "q"})
        assert result2.vars["ruta"] == ""


@pytest.mark.asyncio
async def test_fallback_resuelve_root_relativo_contra_base_workspace(monkeypatch, tmp_path):
    """Forma de PRODUCCIÓN: project_root llega relativo ('code_projects/edd')
    y resuelve contra memory/<ws>/ — el glob contra el CWD no encontraba
    nada y el fallback jamás disparaba pese a existir los tests."""
    import time

    import orchestration.dsl.plugins  # noqa: F401
    from core.path_resolver import paths

    proyecto = tmp_path / "code_projects" / "edd"
    (proyecto / "tests").mkdir(parents=True)
    (proyecto / "tests" / "test_evals.py").write_text("x")
    time.sleep(0.02)

    monkeypatch.setattr(paths, "memory_dir", lambda ws: tmp_path, raising=True)

    rt = FakeRuntime(agent_output="⚠️ Límite de 8 iteraciones...")
    rt.project_root = "code_projects/edd"
    rt.workspace = "main"
    cw = _compile(
        [
            _agent("paso", outputs=["raw"]),
            {
                "id": "p",
                "kind": "plugin",
                "plugin": "pick_line",
                "params": {
                    "var": "raw",
                    "out": "ruta",
                    "prefix": "TEST_PATH:",
                    "fallback_glob": "tests/*.py",
                },
            },
        ]
    )
    result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert result.vars["ruta"] == "tests/test_evals.py"
    assert rt._degraded_steps == ["pick_line:ruta:ruta-inferida"]


class TestRetryPorSalidaVacia:
    """Un paso mudo (contrato incumplido) se reintenta dentro de retry_max
    ANTES de degradar — el reintento es barato frente al run perdido."""

    @pytest.mark.asyncio
    async def test_salida_vacia_reintenta_y_recupera(self):
        import orchestration.dsl.plugins  # noqa: F401
        from orchestration.dsl.engine import WorkflowEngine

        class _Flaky:
            def __init__(self):
                self.calls = 0

            async def call_agent(self, agent, prompt, **kw):
                self.calls += 1
                return "" if self.calls == 1 else "HISTORIAS: - a"

            def emit(self, **payload):
                pass

        rt = _Flaky()
        cw = _compile([_agent("extraer", outputs=["historias"], retry_max=1)])
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert rt.calls == 2
        assert result.vars["historias"] == "HISTORIAS: - a"

    @pytest.mark.asyncio
    async def test_salida_vacia_sin_reintentos_tambien_falla(self):
        """Con retry_max=0 el paso mudo igualmente falla el run (el
        soft-continue con "" no existe)."""
        from orchestration.dsl.engine import WorkflowEngine

        class _Mudo:
            def __init__(self):
                self.calls = 0

            async def call_agent(self, agent, prompt, **kw):
                self.calls += 1
                return ""

            def emit(self, **payload):
                pass

        rt = _Mudo()
        cw = _compile([_agent("paso")])  # retry_max default: 0
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "failed"
        assert "salida vacía tras 1 intento" in (result.failure or "")
        assert rt.calls == 1


class TestSubtareaDeterminista:
    """El patrón tdd generalizado a loops de subtareas: cada ítem declara
    su test (TEST_PATH) y se verifica SOLO contra él."""

    @pytest.mark.asyncio
    async def test_cuerpo_con_parse_y_verificacion_por_item(self):
        import orchestration.dsl.plugins  # noqa: F401

        outputs = ["prosa\nTEST_PATH: tests/test_a.py", "prosa\nTEST_PATH: tests/test_b.py"]

        class _Rt:
            def __init__(self):
                self.tool_calls = []
                self._i = 0

            async def decompose(self, query, strategy):
                return ["tarea_a", "tarea_b"]

            async def call_agent(self, agent, prompt, **kw):
                out = outputs[self._i % 2]
                self._i += 1
                return out

            async def call_tool(self, tool, args):
                self.tool_calls.append((tool, dict(args)))
                return {"success": True, "output": "3 passed"}

            def emit(self, **payload):
                pass

        rt = _Rt()
        cw = _compile(
            [
                {"id": "items", "kind": "decompose", "strategy": "flat", "output": "tareas"},
                {
                    "id": "ejecutar",
                    "kind": "loop",
                    "over": "tareas",
                    "max_iter": 5,
                    "body": [
                        _agent("subtarea", outputs=["subtarea_out"]),
                        {
                            "id": "parse_test",
                            "kind": "plugin",
                            "plugin": "pick_line",
                            "params": {
                                "var": "subtarea_out",
                                "out": "test_path",
                                "prefix": "TEST_PATH:",
                            },
                        },
                        {
                            "id": "verificar",
                            "kind": "tool",
                            "tool": "test_runner",
                            "args": {"file_path": "$test_path"},
                        },
                    ],
                },
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        paths = [a["file_path"] for _t, a in rt.tool_calls]
        assert paths == ["tests/test_a.py", "tests/test_b.py"]

    @pytest.mark.asyncio
    async def test_cuerpo_paralelo_mismo_patron(self):
        import orchestration.dsl.plugins  # noqa: F401

        class _Rt:
            def __init__(self):
                self.tool_calls = []

            async def decompose(self, query, strategy):
                return ["tarea_a", "tarea_b"]

            async def call_agent(self, agent, prompt, **kw):
                return "TEST_PATH: tests/test_x.py"

            async def call_tool(self, tool, args):
                self.tool_calls.append((tool, dict(args)))
                return {"success": True, "output": "1 passed"}

            def emit(self, **payload):
                pass

        rt = _Rt()
        cw = _compile(
            [
                {"id": "items", "kind": "decompose", "strategy": "flat", "output": "tareas"},
                {
                    "id": "ejecutar",
                    "kind": "loop",
                    "over": "tareas",
                    "max_iter": 5,
                    "parallel": True,
                    "body": [
                        _agent("subtarea", outputs=["subtarea_out"]),
                        {
                            "id": "parse_test",
                            "kind": "plugin",
                            "plugin": "pick_line",
                            "params": {
                                "var": "subtarea_out",
                                "out": "test_path",
                                "prefix": "TEST_PATH:",
                            },
                        },
                        {
                            "id": "verificar",
                            "kind": "tool",
                            "tool": "test_runner",
                            "args": {"file_path": "$test_path"},
                        },
                    ],
                },
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert len(rt.tool_calls) == 2  # cada ítem verificó SU archivo
        assert all(a["file_path"] == "tests/test_x.py" for _t, a in rt.tool_calls)


class TestPasoMudoAgotadoFalla:
    """Salida vacía tras agotar retry_max = fallo del PASO (no soft-continue
    que quema 2 steps más hasta que el until descubre el hueco)."""

    @pytest.mark.asyncio
    async def test_mudo_agotado_falla_el_run_temprano(self):
        """El paso agotado-sin-salida falla el run EN SU LUGAR (no soft-
        continue que quema 2 steps más hasta que el until descubre el
        hueco) — el orquestador persiste ese fallo."""
        from orchestration.dsl.engine import WorkflowEngine

        class _Mudo:
            def __init__(self):
                self.calls = 0

            async def call_agent(self, agent, prompt, **kw):
                self.calls += 1
                return ""

            def emit(self, **payload):
                pass

        rt = _Mudo()
        cw = _compile([_agent("paso", retry_max=1)])
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "failed"
        assert "salida vacía tras 2 intento" in (result.failure or "")
        assert rt.calls == 2


class TestPickLineNoRepiteRutas:
    """La inferencia no repite rutas ya consumidas por subtareas previas
    (todas las subtareas verificando el MISMO archivo = verificación débil)."""

    @pytest.mark.asyncio
    async def test_segunda_inferencia_salta_la_ya_usada(self, tmp_path, monkeypatch):
        import time

        import orchestration.dsl.plugins as plugins_mod  # noqa: F401
        from core.path_resolver import paths

        proyecto = tmp_path / "code_projects" / "coord"
        (proyecto / "tests").mkdir(parents=True)
        (proyecto / "tests" / "test_tokenizer.py").write_text("x")
        time.sleep(0.02)
        (proyecto / "tests" / "test_evaluator.py").write_text("y")

        monkeypatch.setattr(paths, "memory_dir", lambda ws: tmp_path, raising=True)

        # dos pasos SIN TEST_PATH: ambos caen al fallback
        rt = FakeRuntime(agent_output="sin línea")
        rt.project_root = "code_projects/coord"
        rt.workspace = "main"
        cw = _compile(
            [
                _agent("paso1", outputs=["o1"]),
                {
                    "id": "p1",
                    "kind": "plugin",
                    "plugin": "pick_line",
                    "params": {
                        "var": "o1",
                        "out": "ruta1",
                        "prefix": "TEST_PATH:",
                        "fallback_glob": "tests/*.py",
                    },
                },
                _agent("paso2", outputs=["o2"]),
                {
                    "id": "p2",
                    "kind": "plugin",
                    "plugin": "pick_line",
                    "params": {
                        "var": "o2",
                        "out": "ruta2",
                        "prefix": "TEST_PATH:",
                        "fallback_glob": "tests/*.py",
                    },
                },
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        # la primera toma el más reciente (test_evaluator), la SEGUNDA
        # salta la usada y toma la otra
        assert result.vars["ruta1"] == "tests/test_evaluator.py"
        assert result.vars["ruta2"] == "tests/test_tokenizer.py"
