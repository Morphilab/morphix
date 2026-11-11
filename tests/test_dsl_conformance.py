# tests/test_dsl_conformance.py
"""Conformance suite EJECUTABLE del DSL.

El harness vive en PRODUCTO (`orchestration/dsl/conformance.py`) para que el
CLI (`validate --conformance`) y los tests usen EL MISMO simulador. Este
módulo descubre TODOS los presets (producto + workspaces + skeletons CLI) y
afirma que corren a completed con las firmas reales de las tools.

Si rompes el contrato (p.ej. quitas `args` de un until con test_runner), esta
suite ES el test de regresión: el fake con firma real lanza TypeError → el
until jamás se cumple → el loop agota max_iter → falla el assert de
completed/file_path.
"""

from pathlib import Path

import pytest
import yaml

from orchestration.dsl.compiler import FileSystemCatalog, compile_workflow
from orchestration.dsl.conformance import simulate_preset, structural_feedback_errors

REPO = Path(__file__).resolve().parent.parent


# ── Descubrimiento de presets ──────────────────────────────────────────────


def _product_presets() -> list[tuple[str, str, dict]]:
    """(nombre, workspace, raw) de los presets de producto."""
    out = []
    for yml in sorted((REPO / "templates" / "workflows").glob("*.yaml")):
        if yml.name.startswith("_"):
            continue  # _FULL_TEMPLATE es referencia documental
        raw = yaml.safe_load(yml.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("version") == 1:
            out.append((raw.get("name") or yml.stem, "main", raw))
    return out


def _workspace_presets() -> list[tuple[str, str, dict]]:
    out = []
    ws_base = REPO / "templates" / "workspaces"
    if not ws_base.is_dir():
        return out
    for ws_dir in sorted(p for p in ws_base.iterdir() if p.is_dir()):
        wf_dir = ws_dir / "workflows"
        if not wf_dir.is_dir():
            continue
        for yml in sorted(wf_dir.glob("*.yaml")):
            raw = yaml.safe_load(yml.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("version") == 1:
                out.append((raw.get("name") or yml.stem, ws_dir.name, raw))
    return out


def _skeleton_presets() -> list[tuple[str, str, dict]]:
    """Los esqueletos que `cli new` sirve a usuarios — deben correr."""
    from orchestration.dsl.cli import _DEFAULT_TOOLS, _SKELETONS

    out = []
    for stype, skeleton in _SKELETONS.items():
        raw = {
            "version": 1,
            "name": f"skeleton_{stype}",
            "description": skeleton["description"],
            "agents": {"allowed": skeleton["agents"]},
            "tools": {"allowed": _DEFAULT_TOOLS[stype]},
            "project": {"required": False},
            "skills": False,
            "steps": skeleton["steps"],
        }
        out.append((stype, "main", raw))
    return out


ALL_PRESETS = _product_presets() + _workspace_presets() + _skeleton_presets()


@pytest.mark.asyncio
async def test_bdd_conformance_ejecuta_los_ciclos_de_historias():
    """El fake del harness debe emitir '## LISTA' cuando el goal lo pide
    (paridad con TEST_PATH): sin ella, bdd/domain_tdd solo ejercitaban el
    camino DEGRADADO (loop de 0 ciclos) y el ✔ verde escondía el contrato
    roto."""
    raw = yaml.safe_load(
        (REPO / "templates" / "workflows" / "bdd.yaml").read_text(encoding="utf-8")
    )
    report = await simulate_preset(raw, workspace="main")
    assert report.status == "completed"
    assert (
        len(report.agent_tasks) >= 2
    ), "el ciclo de historias debe correr (test_fallido + implementar)"


@pytest.mark.asyncio
async def test_conformance_report_expone_degradadas():
    """Un contrato incumplido o recortado es VISIBLE en el report — el ✔
    con degradadas es información, no silencio (truncación por max_items:
    el fake emite 2 ítems y el preset declara cap 1)."""
    doc = {
        "version": 1,
        "name": "demo_truncada",
        "steps": [
            {
                "id": "texto",
                "kind": "agent",
                "agent": "developer",
                "goal": "entrega la lista con ## LISTA",
                "outputs": ["texto"],
            },
            {
                "id": "parsear",
                "kind": "plugin",
                "plugin": "text_to_list",
                "params": {
                    "var": "texto",
                    "out": "lista",
                    "marker": "## LISTA",
                    "max_items": 1,
                },
                "outputs": ["lista"],
            },
        ],
        "agents": {"allowed": ["developer"]},
        "tools": {"allowed": []},
    }
    report = await simulate_preset(doc, workspace="main")
    assert report.status == "completed"
    assert report.degradadas, "la truncación por cap debe figurar como degradada"
    assert any("truncada" in d for d in report.degradadas)


def test_conformance_descubre_presets():
    """Guard de cobertura: si el descubrimiento cae, hay un gap silencioso."""
    productos = len(_product_presets())
    assert productos >= 8, f"solo {productos} presets de producto descubiertos"
    assert ALL_PRESETS, "la conformance no descubrió ningún preset"


# ── La conformance ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "nombre,workspace,raw", ALL_PRESETS, ids=[f"{p[1]}:{p[0]}" for p in ALL_PRESETS]
)
async def test_preset_corre_a_completed_con_firmas_reales(nombre: str, workspace: str, raw: dict):
    report = await simulate_preset(raw, workspace=workspace)
    assert report.semantic_errors == [], f"'{nombre}': {report.semantic_errors}"
    assert report.ok, (
        f"preset '{nombre}' NO completa: status={report.status} "
        f"failure={report.failure} errors={report.errors} "
        f"rc1={report.test_runner_sin_file_path}"
    )
    assert (
        not report.test_runner_sin_file_path
    ), f"preset '{nombre}': test_runner llamado sin file_path"


# ── Guard estructural: loops ciegos CON feedback ───────────────────────


@pytest.mark.parametrize(
    "nombre,workspace,raw", ALL_PRESETS, ids=[f"{p[1]}:{p[0]}" for p in ALL_PRESETS]
)
def test_loops_ciegos_consumen_feedback(nombre, workspace, raw):
    cw = compile_workflow(raw, catalog=FileSystemCatalog(workspace))
    errores = structural_feedback_errors(cw.dsl)
    assert not errores, f"preset '{nombre}':\n" + "\n".join(f"• {e}" for e in errores)


# ── el goal del TDD consume contexto de iteración ─────────────────────


def test_tdd_goal_referencia_contexto_iteracion():
    raw = yaml.safe_load(
        (REPO / "templates" / "workflows" / "tdd.yaml").read_text(encoding="utf-8")
    )
    loop = next(s for s in raw["steps"] if s.get("kind") == "loop")
    goal = loop["body"][0]["goal"]
    assert (
        "$iter" in goal and "$last_output" in goal
    ), "el goal del TDD debe consumir $iter/$max_iter/$last_output"
    # el until verifica SOLO el archivo de la feature (ruta de runtime):
    # sobre "." heredaba los fallos de tests ajenos y agotaba max_iter
    assert loop["until"]["args"]["file_path"] == "$test_path"


class TestRamasAlternas:
    """El harness por defecto solo ejercita el happy path (options[0],
    sin bloqueantes). Estas pruebas scriptean las ramas alternas contra
    la pila real — un decide-fallback o un gate bloqueante son rutas de
    ejecución que deben correr, no solo compilar."""

    @staticmethod
    def _doc_decide() -> dict:
        return {
            "version": 1,
            "name": "conformance-decide-alt",
            "agents": {"allowed": ["developer", "analista"]},
            "tools": {"allowed": ["file_manager"]},
            "steps": [
                {
                    "id": "eleccion",
                    "kind": "decide",
                    "question": "¿refactor?",
                    "options": ["SÍ", "NO"],
                    "fallback": "NO",
                    "branches": {
                        "SÍ": [
                            {
                                "id": "refactor",
                                "kind": "agent",
                                "agent": "developer",
                                "goal": "ejecuto_refactor",
                            }
                        ],
                        "NO": [
                            {
                                "id": "posponer",
                                "kind": "agent",
                                "agent": "developer",
                                "goal": "posponer_cambio",
                            }
                        ],
                    },
                }
            ],
        }

    @pytest.mark.asyncio
    async def test_decide_rama_no_primera(self):
        """La respuesta scripted elige la SEGUNDA opción: la rama NO corre
        end-to-end (antes el harness jamás la ejercitaba)."""
        report = await simulate_preset(self._doc_decide(), decide_answer="NO")
        assert report.ok, report.errors
        assert any("posponer_cambio" in t for t in report.agent_tasks)

    @pytest.mark.asyncio
    async def test_decide_respuesta_invalida_cae_al_fallback(self):
        """Respuesta fuera del enum → fallback determinista (NO): la ruta
        de degradación también es ejecución real."""
        report = await simulate_preset(self._doc_decide(), decide_answer="quizá")
        assert report.ok, report.errors
        assert any("posponer_cambio" in t for t in report.agent_tasks)

    @pytest.mark.asyncio
    async def test_gate_bloqueante_ejecuta_rama_if_blockers(self):
        """El agente reporta [Bloqueante] y el step con when: if_blockers
        corre su rama (el patrón pipeline de gates sobre el motor DSL)."""
        doc = {
            "version": 1,
            "name": "conformance-gate-bloqueante",
            "agents": {"allowed": ["developer"]},
            "tools": {"allowed": ["file_manager"]},
            "steps": [
                {"id": "analizar", "kind": "agent", "agent": "developer", "gate": True},
                {
                    "id": "mitigar",
                    "kind": "agent",
                    "agent": "developer",
                    "goal": "mitigar_riesgo",
                    "when": "if_blockers",
                },
            ],
        }
        report = await simulate_preset(
            doc, agent_output="hallazgo: [Bloqueante] dependencia vulnerable"
        )
        assert report.ok, report.errors
        assert any("mitigar_riesgo" in t for t in report.agent_tasks)

    @pytest.mark.asyncio
    async def test_gate_sin_bloqueantes_salta_la_rama(self):
        report = await simulate_preset(
            {
                "version": 1,
                "name": "conformance-gate-limpio",
                "agents": {"allowed": ["developer"]},
                "tools": {"allowed": ["file_manager"]},
                "steps": [
                    {"id": "analizar", "kind": "agent", "agent": "developer", "gate": True},
                    {
                        "id": "mitigar",
                        "kind": "agent",
                        "agent": "developer",
                        "goal": "mitigar_riesgo",
                        "when": "if_blockers",
                    },
                ],
            },
            agent_output="todo en orden",
        )
        assert report.ok, report.errors
        assert not any("mitigar" in t for t in report.agent_tasks)


# ── veredicto de verificación en rojo ─────────────────────────────────


class TestVeredictoEnRojo:
    """El camino rojo de la verificación determinista.

    El fake de `test_runner` es SIEMPRE verde: sin `test_runner_verde=False` la
    rama de veredicto reprobado (el caso que dejaba el scorecard reportando
    'completadas' con tests rojos) no tendría cobertura ejecutable.
    """

    def _preset(self) -> dict:
        return {
            "version": 1,
            "name": "conformance-veredicto",
            "agents": {"allowed": ["developer"]},
            "tools": {"allowed": ["test_runner"]},
            "inputs": ["query", "subtasks"],
            "defaults": {"subtasks": ["tarea_a", "tarea_b"]},
            "steps": [
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "max_iter": 2,
                    "over": "subtasks",
                    "body": [
                        {"id": "subtarea", "kind": "agent", "agent": "developer", "goal": "$item"},
                        {
                            "id": "verificar",
                            "kind": "tool",
                            "tool": "test_runner",
                            "args": {"file_path": "tests/test_x.py"},
                            "outputs": {"pasan": "tests_all_pass"},
                            "verdict": "pasan",
                        },
                    ],
                },
                {"id": "agregar", "kind": "aggregate", "strategy": "result"},
            ],
        }

    @pytest.mark.asyncio
    async def test_rojo_se_reporta_como_veredicto_reprobado(self):
        report = await simulate_preset(
            self._preset(),
            agent_output="entrega",
            test_runner_verde=False,
        )
        # el run corre igual (un rojo no tira el programa) pero NO está ok:
        assert report.status == "completed", report.failure
        assert report.veredictos_rojos, "el rojo de verificación debe ser visible"
        assert not report.ok, "un preset con veredictos en rojo no puede salir ok"

    @pytest.mark.asyncio
    async def test_verde_no_reporta_veredictos(self):
        report = await simulate_preset(self._preset(), agent_output="entrega")
        assert report.ok, report.errors
        assert report.veredictos_rojos == []


# ── verificación del AGREGADO (Fase B) ────────────────────────────────


def _steps_recursivo(steps: list) -> list[dict]:
    """Todos los steps del doc, incluidos los anidados en loops/branches."""
    out: list[dict] = []
    for s in steps:
        out.append(s)
        for hijo in s.get("body") or []:
            out.extend(_steps_recursivo([hijo]))
        for rama in (s.get("branches") or {}).values():
            out.extend(_steps_recursivo(rama))
    return out


class TestVerificacionDelAgregado:
    """Los presets que construyen un proyecto verifican el CONJUNTO.

    Los gates por-subtarea no ven que las piezas encajen entre sí: módulos que
    no se importan entre ellos, o el test de un ciclo que rompe el de otro.
    """

    PRESETS_CON_PROYECTO = ["bdd", "domain_tdd", "development", "coordinated", "sdd", "tdd", "edd"]

    @pytest.mark.parametrize("preset", PRESETS_CON_PROYECTO)
    def test_preset_declara_verificacion_del_agregado(self, preset):
        raw = yaml.safe_load(
            (REPO / "templates" / "workflows" / f"{preset}.yaml").read_text(encoding="utf-8")
        )
        pasos = {s.get("id"): s for s in _steps_recursivo(raw["steps"])}
        assert "artefactos_del_run" in pasos, f"{preset}: sin recolector de artefactos"
        assert (
            pasos["artefactos_del_run"]["params"]["glob"] == "tests/**/*.py"
        ), f"{preset}: el recolector debe apuntar a los tests del run"
        assert "verificar_agregado" in pasos, f"{preset}: sin verificación del agregado"
        paso = pasos["verificar_agregado"]
        # acotada: verifica SOLO lo que el run tocó, no la suite preexistente
        assert paso["args"]["file_paths"] == "$tests_del_run", f"{preset}: verifica otra cosa"
        final = pasos.get("verificar_agregado_final")
        if final is not None:
            # presets con reparación: el intermedio mide y el gate es el FINAL
            assert (
                final.get("verdict") == "pasan_agregado_final"
            ), f"{preset}: el gate final no tiene veredicto"
            assert (
                final["args"]["file_paths"] == "$tests_del_run"
            ), f"{preset}: el gate final verifica otra cosa"
            reparar = pasos.get("reparar_agregado", {})
            assert (
                reparar.get("when") == "!$pasan_agregado"
            ), f"{preset}: la reparación no se dispara con el agregado en rojo"
        else:
            assert paso.get("verdict") == "pasan_agregado", f"{preset}: sin veredicto del agregado"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("preset", PRESETS_CON_PROYECTO)
    async def test_agregado_en_rojo_reprueba_la_entrega(self, preset):
        """Con la suite del proyecto en rojo, el run no puede quedar limpio
        aunque cada subtarea haya pasado su propio gate — para TODOS los
        presets con agregado, no solo development (el caso nació ahí y la
        parametrización de tdd/edd se quedó en la declaración estructural)."""
        raw = yaml.safe_load(
            (REPO / "templates" / "workflows" / f"{preset}.yaml").read_text(encoding="utf-8")
        )
        report = await simulate_preset(raw, agent_output="entrega", test_runner_verde=False)
        assert report.status == "completed", report.failure
        assert any(
            "verificar_agregado" in v for v in report.veredictos_rojos
        ), report.veredictos_rojos


@pytest.mark.asyncio
async def test_collaborative_ejecuta_sintesis_del_moderador():
    """La rama `aggregate: moderator` corre de verdad: la síntesis del
    moderador pasa por el loop de agentes (con `suppress_message`, sin
    duplicar el final como mensaje de agente)."""
    raw = yaml.safe_load(
        (REPO / "templates" / "workflows" / "collaborative.yaml").read_text(encoding="utf-8")
    )
    report = await simulate_preset(raw, workspace="main")
    assert report.ok, report.errors
    assert any("Sintetiza" in t for t in report.agent_tasks), report.agent_tasks


@pytest.mark.asyncio
async def test_el_replanner_esta_stubbeado_en_la_conformance():
    """El planner es una llamada LLM: la conformance lo stubea SIEMPRE —
    un gate que paga la API del usuario no es un gate."""
    raw = yaml.safe_load(
        (REPO / "templates" / "workflows" / "development.yaml").read_text(encoding="utf-8")
    )
    report = await simulate_preset(raw, agent_output="entrega", test_runner_verde=False)
    assert report.replan_calls >= 1, "el replan no pasó por el stub de la conformance"
