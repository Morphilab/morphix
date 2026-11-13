# tests/test_template_consistency.py
"""Guard tests: every agent/tool referenced by a workflow template must exist.

Prevents regressions like the `architect` agent or `browser` tool being
referenced by a workflow template without a backing profile/registration.
"""

import re

import pytest
import yaml

from core.path_resolver import (
    paths,
)
from tools.specs import TOOL_DEFINITIONS

# Tools that are registered but live outside TOOL_DEFINITIONS (interception-only).
_EXTRA_TOOLS = {"ask_clarification"}


def _agent_names() -> set[str]:
    names: set[str] = set()
    # Task 1.5 (doc flujos): escanear TODOS los workspaces, no solo templates+main.
    agent_dirs = [paths.templates_agents_dir()]
    ws_base = paths.workspaces_base()
    if ws_base.exists():
        for ws_dir in sorted(ws_base.iterdir()):
            agents_dir = ws_dir / "agents"
            if agents_dir.is_dir():
                agent_dirs.append(agents_dir)
    legacy = paths.workspace_agents_dir("main")
    if legacy not in agent_dirs and legacy.exists():
        agent_dirs.append(legacy)
    for d in agent_dirs:
        if not d.exists():
            continue
        for f in d.glob("*.yaml"):
            if f.name.startswith("_"):
                continue
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict) and data.get("name"):
                names.add(data["name"])
    return names


def _valid_tools() -> set[str]:
    return set(TOOL_DEFINITIONS) | _EXTRA_TOOLS


def _workflow_files() -> list:
    files = []
    wf_dirs = [paths.templates_workflows_dir()]
    ws_base = paths.workspaces_base()
    if ws_base.exists():
        for ws_dir in sorted(ws_base.iterdir()):
            wf_dir = ws_dir / "workflows"
            if wf_dir.is_dir():
                wf_dirs.append(wf_dir)
    seen = set()
    for d in wf_dirs:
        if not d.exists():
            continue
        for f in d.glob("*.yaml"):
            if f.name.startswith("_") or f in seen:
                continue
            seen.add(f)
            files.append(f)
    return files


def _template_agents(template: dict) -> set[str]:
    refs: set[str] = set()
    allowed = template.get("agents", {}).get("allowed")
    if isinstance(allowed, list):
        refs.update(a for a in allowed if isinstance(a, str))
    for phase in template.get("default_phases", []) or []:
        if isinstance(phase, dict):
            refs.update(a for a in (phase.get("agents") or []) if isinstance(a, str))
    # Task 1.5: stages de workflows tipo pipeline
    for stage in template.get("stages", []) or []:
        if isinstance(stage, dict) and isinstance(stage.get("agent"), str):
            refs.add(stage["agent"])
    return refs


def test_workflow_templates_reference_existing_agents():
    valid = _agent_names()
    assert valid, "No agent templates discovered"

    violations: list[str] = []
    for f in _workflow_files():
        template = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for agent in sorted(_template_agents(template)):
            if agent not in valid:
                violations.append(
                    f"{f.relative_to(paths.templates_dir().parent)} -> agent '{agent}'"
                )

    assert not violations, "Workflow templates reference non-existent agents:\n" + "\n".join(
        violations
    )


def test_analista_profile_absorbe_el_rol_de_diseno():
    """El roster global es de 4 (analista absorbió a architect en la fusión).

    analista hereda las garantías que el test de architect protegía:
    type=analysis, el rol canónico de model de los agentes, lectura
    (file_manager) y file_view para el diseño con visor. Y architect YA NO
    existe en ningún catálogo: una copia ws-local olvidada lo
    re-registraría."""
    data = yaml.safe_load(
        (paths.templates_agents_dir() / "analista.yaml").read_text(encoding="utf-8")
    )
    assert data["type"] == "analysis", f"Expected type=analysis, got {data.get('type')}"
    assert (
        data["model_role"] == "deepinfra"
    ), f"Expected model_role=deepinfra, got {data.get('model_role')}"
    assert "file_manager" in (data.get("tools") or []), "analista debe leer (read-only)"
    assert "file_view" in (data.get("tools") or []), "analista heredó el visor del diseño"
    assert (
        "design" in data["system_prompt"].lower()
    ), "analista debe entregar diseño cuando se le pide (fusión con architect)"

    # arqueología viva: ninguna copia de architect en ningún catálogo
    for d in [paths.templates_agents_dir()] + [
        p / "agents" for p in paths.workspaces_base().iterdir() if (p / "agents").is_dir()
    ]:
        assert not (d / "architect.yaml").exists(), f"architect.yaml sobrevive en {d}"


def _agentes_despachados(steps: list) -> set[str]:
    """Agentes que un doc DSL realmente despacha (recursivo, incluye moderador).

    Acepta steps crudos (dicts) o el IR expandido (modelos pydantic); las
    branches pueden ser dict (decide) o lista de ramas con .steps (parallel).
    """
    out: set[str] = set()
    for s in steps or []:
        if isinstance(s, dict):
            agent = s.get("agent")
            strategy = s.get("strategy")
            body = s.get("body")
            branches = s.get("branches")
        else:
            agent = getattr(s, "agent", None)
            strategy = getattr(s, "strategy", None)
            body = getattr(s, "body", None)
            branches = getattr(s, "branches", None)
        if isinstance(agent, str):
            out.add(agent)
        if strategy == "moderator" and isinstance(agent, str):
            out.add(agent)
        out |= _agentes_despachados(body)
        if isinstance(branches, dict):
            for rama in branches.values():
                out |= _agentes_despachados(rama)
        elif isinstance(branches, list):
            for rama in branches:
                out |= _agentes_despachados(
                    rama.get("steps") if isinstance(rama, dict) else getattr(rama, "steps", None)
                )
    return out


def test_allowlist_de_agentes_solo_lista_los_despachados():
    """`agents.allowed` es el contrato de qué agentes USA el preset.

    Un agente listado que ningún step despacha es ruido de lectura: sugiere una
    capacidad que el flujo no ejerce (y `agents.allowed` solo se valida — no
    acota el runtime, así que no concede nada). Los presets de producto deben
    declarar exactamente lo que despachan. Los bodies pueden vivir en
    fragmentos (`kind: fragment`) — el conteo se hace sobre el IR EXPANDIDO.
    """
    from orchestration.dsl.compiler import FileSystemCatalog, compile_workflow

    desalineados: list[str] = []
    for wf_file in _workflow_files():
        data = yaml.safe_load(wf_file.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict) or data.get("version") != 1:
            continue  # solo docs DSL
        try:
            cw = compile_workflow(data, catalog=FileSystemCatalog())
        except Exception as exc:
            desalineados.append(f"{wf_file.name}: no compila ({exc})")
            continue
        allowed = set(data.get("agents", {}).get("allowed") or [])
        usados = _agentes_despachados(cw.dsl.steps)
        sobran = sorted(allowed - usados)
        if sobran:
            desalineados.append(f"{wf_file.name}: {sobran}")
    assert (
        not desalineados
    ), "allowlists con agentes que ningún step despacha (ruido de contrato):\n" + "\n".join(
        desalineados
    )


def test_collaborative_has_tools_allowed_with_minimum_count():
    """Collaborative workflow must restrict tool access for debate safety."""
    wf_file = paths.templates_workflows_dir() / "collaborative.yaml"
    assert wf_file.exists(), "collaborative.yaml not found"

    template = yaml.safe_load(wf_file.read_text(encoding="utf-8"))
    allowed = template.get("tools", {}).get("allowed")
    assert allowed is not None, "collaborative.yaml must define tools.allowed"
    assert isinstance(allowed, list), "tools.allowed must be a list"
    assert (
        len(allowed) >= 3
    ), f"collaborative.yaml must have at least 3 allowed tools, got {len(allowed)}: {allowed}"


def test_development_yaml_includes_code_search():
    from pathlib import Path

    import yaml

    template_path = Path("templates/workflows/development.yaml")
    with open(template_path) as f:
        template = yaml.safe_load(f)
    tools = template["tools"]["allowed"]
    assert "code_search" in tools


def test_development_yaml_has_retry_enabled():
    """DSL: la subtarea de development declara retry (retry_max en el step).

    El body vive en el fragmento compartido `_cuerpo_subtareas` — el guard
    COMPARA el IR expandido (también caza un fragmento roto), no el YAML crudo.
    """
    from orchestration.dsl.compiler import FileSystemCatalog, compile_workflow
    from orchestration.loader import load_workflow_document

    raw = load_workflow_document("main", "development")
    assert raw and raw.get("version") == 1, "development.yaml debe ser DSL (version: 1)"
    cw = compile_workflow(raw, catalog=FileSystemCatalog())
    loop = next(s for s in cw.dsl.steps if s.kind == "loop")
    subtarea = next(s for s in loop.body if s.id == "subtarea")
    assert subtarea.retry_max == 2, "la subtarea (fragmento expandido) debe declarar retry_max: 2"


def test_workflow_templates_reference_registered_tools():
    valid = _valid_tools()

    violations: list[str] = []
    for f in _workflow_files():
        template = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        allowed = template.get("tools", {}).get("allowed") or []
        for tool in allowed:
            if not isinstance(tool, str):
                continue
            if tool.startswith("mcp:") or tool.startswith("mcp_") or tool == "mcp":
                continue
            if tool not in valid:
                violations.append(f"{f.relative_to(paths.templates_dir().parent)} -> tool '{tool}'")

    assert not violations, "Workflow templates reference unregistered tools:\n" + "\n".join(
        violations
    )


def test_workflow_skills_flag_is_bool_when_present():
    """Todo doc DSL declara `skills` explícito (bool).

    El default silencioso del schema no distingue un olvido de una
    decisión: el único preset sin flag era collaborative por omisión,
    indistinguible de intención hasta que se declaró `false`."""
    for wf in _workflow_files():
        data = yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict) and data.get("version") == 1:
            assert isinstance(
                data.get("skills"), bool
            ), f"{wf.name}: declara skills: true|false explícito"


def _pasos_agente(steps: list) -> list[dict]:
    """Steps con `agent` en todo el árbol (body/branches incluidos)."""
    out: list[dict] = []
    for s in steps or []:
        if isinstance(s, dict):
            if isinstance(s.get("agent"), str):
                out.append(s)
            out.extend(_pasos_agente(s.get("body")))
            for rama in (s.get("branches") or {}).values():
                out.extend(_pasos_agente(rama))
    return out


# Órdenes de escribir archivos en un goal: las marcas literales (tool syntax)
# son la firma del bug original; las formas naturales ("escribe el archivo X")
# esquivan un matcher de marcas. El objeto-archivo cercano exige intención de
# archivo real: "redacta una spec" (prosa) no matchea; "guarda en spec.md" sí.
_VERBOS_ESCRITURA = (
    r"(?:escrib\w+|edit\w+|guard\w+|salv\w+|persist\w+|modific\w+|actualiz\w+|"
    r"añad\w+|gener\w+|crear|creas|crees|crea)"
)
_OBJETO_ARCHIVO = r"(?:archivos?|ficheros?|disco|\.md|\.py|\.yaml|\.json|\.txt)"
_NEGACIONES = {"no", "sin", "ni", "nunca", "jamás", "jamas"}


def _ordenes_de_escritura(goal: str) -> list[str]:
    """Órdenes de escribir archivos en un goal (literales o naturales).

    Negation-aware: "sin escribir ni editar archivos" o "NO edites" son
    PROHIBICIONES, no órdenes — la ventana mira las 3 palabras previas al
    verbo (cubre "sin escribir ni editar", donde el segundo verbo hereda
    la negación del primero)."""
    hits = [m for m in ("file_manager.write", "action='write'", 'action="write"') if m in goal]
    pat = re.compile(rf"\b{_VERBOS_ESCRITURA}\b[^.]{{0,60}}?{_OBJETO_ARCHIVO}", re.IGNORECASE)
    for m in pat.finditer(goal):
        previas = re.split(r"\s+", goal[: m.start()].strip())[-3:]
        if any(p.strip(".,;:()[]¡!¿?\"'").lower() in _NEGACIONES for p in previas):
            continue
        hits.append(m.group(0))
    return hits


class TestOrdenesDeEscritura:
    def test_detecta_literales_y_naturales(self):
        assert _ordenes_de_escritura("Usa file_manager.write para guardar")
        assert _ordenes_de_escritura("Escribe el archivo spec.md con la spec")
        assert _ordenes_de_escritura("Guarda la spec en disco")
        assert _ordenes_de_escritura("crea el archivo tests/test_x.py")
        assert _ordenes_de_escritura("edita el archivo de configuración")
        assert _ordenes_de_escritura("persiste los resultados en un archivo")
        assert _ordenes_de_escritura("añade un archivo con los datos")

    def test_no_marca_negaciones_ni_prosa(self):
        # la prohibición de escribir es lo CONTRARIO de una orden
        assert not _ordenes_de_escritura("NO edites.")
        assert not _ordenes_de_escritura(
            "responde sin escribir ni editar archivos (ni ADRs, ni documentos)"
        )
        assert not _ordenes_de_escritura("nunca guardes archivos")
        # prosa sin intención de archivo
        assert not _ordenes_de_escritura("Redacta una spec técnica detallada (alcance, contratos)")
        assert not _ordenes_de_escritura("Cierra con la línea ## LISTA seguida de viñetas")


def test_pasos_de_analista_no_piden_escritura_directa():
    """El perfil de analista es read-only (`file_manager.write` prohibido):
    ningún goal de analista puede ordenarle escribir — ni con sintaxis de
    tool ni con lenguaje natural.

    La clase de bug que tumbó edd con el modelo estricto: obedece al SOUL,
    se niega a escribir y el paso stallea hasta agotar el run."""
    violaciones: list[str] = []
    for wf_file in _workflow_files():
        data = yaml.safe_load(wf_file.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict) or data.get("version") != 1:
            continue
        for s in _pasos_agente(data.get("steps")):
            if s.get("agent") != "analista":
                continue
            hits = _ordenes_de_escritura(s.get("goal") or "")
            if hits:
                violaciones.append(f"{wf_file.name}:{s.get('id')} → {hits[0]!r}")
    assert not violaciones, "goals de analista que ordenan escribir:\n" + "\n".join(violaciones)


def test_pasos_agente_declaran_timeout():
    """Todo paso `kind: agent` declara `timeout` positivo.

    El campo es opt-in (default None ⇒ `wait_for(None)` espera infinito): una
    llamada colgada congela el paso — y con él el run — indefinidamente. Los
    timeouts existían solo donde el cuelgue ya se había observado; el guard
    cierra la clase, no el caso.
    (Un `aggregate` con `agent:` — p.ej. la síntesis del moderador — NO es
    un paso agente y no admite timeout.)"""
    sin_timeout: list[str] = []
    for wf_file in _workflow_files():
        data = yaml.safe_load(wf_file.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict) or data.get("version") != 1:
            continue
        for s in _pasos_agente(data.get("steps")):
            if s.get("kind") != "agent":
                continue
            t = s.get("timeout")
            if not isinstance(t, int) or isinstance(t, bool) or t <= 0:
                sin_timeout.append(f"{wf_file.name}:{s.get('id')}")
    assert not sin_timeout, "pasos agente sin timeout declarado:\n" + "\n".join(sin_timeout)


def test_piloted_skills_parse_and_have_description():
    """Las skills globales parsean y tienen descripción útil."""
    from core.skills import discover_skills

    summaries = discover_skills(None)
    assert len(summaries) >= 9, f"solo {len(summaries)} skills descubiertas"
    assert all(s.description.strip() for s in summaries)


def _template_workspace_names() -> set[str]:
    """Nombres declarados en templates/workspaces.yaml (manifest)."""
    import yaml as _yaml

    manifest = paths.templates_dir() / "workspaces.yaml"
    if not manifest.exists():
        return set()
    data = _yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    names = {str(e.get("name", "")) for e in (data.get("workspaces") or []) if isinstance(e, dict)}
    return {n for n in names if n}


def test_manifest_entries_have_template_dirs():
    """Cada entrada del manifest tiene su dir en templates/workspaces/<n>."""
    for name in _template_workspace_names():
        assert (
            paths.templates_dir() / "workspaces" / name
        ).is_dir(), f"manifest declara '{name}' pero falta templates/workspaces/{name}/"


def test_template_workspaces_validate_against_schema():
    """Los workflows de templates/workspaces/ compilan+validan como DSL."""
    from orchestration.dsl.compiler import compile_workflow
    from orchestration.dsl.validator import validate_workflow

    violations: list[str] = []
    base = paths.templates_dir() / "workspaces"
    if not base.is_dir():
        return
    for wf in sorted(base.glob("*/workflows/*.yaml")):
        if wf.name.startswith("_"):
            continue
        data = yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
        try:
            errores = validate_workflow(compile_workflow(data))
            if errores:
                violations.append(f"{wf.name}: {'; '.join(errores)}")
        except Exception as e:  # ValidationError / ValueError
            violations.append(f"{wf.name}: {e}")
    assert not violations, "Workflows de templates/workspaces inválidos:\n" + "\n".join(violations)


def test_template_workspace_stage_agents_exist():
    """Los stage.agent de pipelines existen entre los agents del template-dir."""
    agent_files = paths.templates_dir().glob("workspaces/*/agents/*.yaml")
    known: set[str] = set()
    for f in agent_files:
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict) and data.get("name"):
            known.add(data["name"])

    violations: list[str] = []
    base = paths.templates_dir() / "workspaces"
    if not base.is_dir():
        return
    for wf in sorted(base.glob("*/workflows/*.yaml")):
        data = yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
        allowed = set((data.get("agents") or {}).get("allowed") or [])
        for stage in data.get("stages") or []:
            agent = stage.get("agent") if isinstance(stage, dict) else None
            if not agent:
                continue
            assert agent in allowed, f"{wf.name}: stage '{stage.get('name')}' fuera de allowed"
            if known and agent not in known:
                violations.append(f"{wf.name}: agente '{agent}' sin YAML en agents/")
    assert not violations, "\n".join(violations)


def test_template_workspace_pipelines_require_project():
    """Los workflows de templates/workspaces/ con stages (pipeline) exigen
    proyecto — sin root fijo; el dir lo define el proyecto seleccionado."""
    base = paths.templates_dir() / "workspaces"
    if not base.is_dir():
        return
    violations: list[str] = []
    for wf in sorted(base.glob("*/workflows/*.yaml")):
        data = yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
        if not data.get("stages"):
            continue
        project = data.get("project") or {}
        if not project.get("required"):
            violations.append(f"{wf.name}: pipeline sin project.required: true")
        if project.get("root"):
            violations.append(
                f"{wf.name}: pipeline con project.root fijo — no apto para template "
                "(el directorio lo define el proyecto seleccionado)"
            )
    assert not violations, "\n".join(violations)


def test_workspace_pipelines_declare_explicit_skills_flag():
    """Los pipelines de workspaces producto declaran `skills` explícito
    (bool) — evita depender del default silencioso del schema."""
    from pathlib import Path

    import yaml

    base = Path(__file__).resolve().parent.parent / "templates" / "workspaces"
    pipelines = [
        p
        for p in sorted(base.glob("*/workflows/*.yaml"))
        if (
            yaml.safe_load(p.read_text(encoding="utf-8")).get("type") == "pipeline"
            or yaml.safe_load(p.read_text(encoding="utf-8")).get("version") == 1
        )
    ]
    if not pipelines:
        pytest.skip("sin pipelines de workspace en templates/workspaces/")
    for p in pipelines:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert isinstance(
            data.get("skills"), bool
        ), f"{p.relative_to(base.parent.parent)} debe declarar skills: true|false explícito"


def test_goal_todo_tools_reach_developer_and_workflows():
    """Los 8 tools de goal/todo/plan deben ser
    alcanzables — capacidad en el template del agente + permiso en el
    workflow (dos capas que filtran en loop.py). Sin esto quedaban
    registrados pero inalcanzables para agentes orquestados."""
    from pathlib import Path

    new_tools = {
        "goal_create",
        "goal_get",
        "goal_update",
        "goal_round",
        "todo_write",
        "todo_get",
        "plan_mode",
        "exit_plan_mode",
    }
    # existen en el registro
    assert new_tools <= set(TOOL_DEFINITIONS)

    # capacidad: developer y analista llevan la familia completa goal/todo/plan
    # (analista heredó el diseño+plan de architect; developer puede planificar
    # antes de escribir y salir con exit_plan_mode).
    dev = yaml.safe_load(open(Path("templates/agents/developer.yaml")))
    ana = yaml.safe_load(open(Path("templates/agents/analista.yaml")))
    assert new_tools <= set(dev["tools"])
    assert {"plan_mode", "exit_plan_mode"} <= set(ana["tools"])

    # permiso: los workflows orquestados no filtran lo que el agente tiene
    for wf in ("development", "coordinated"):
        template = yaml.safe_load(open(Path(f"templates/workflows/{wf}.yaml")))
        allowed = set(template["tools"]["allowed"])
        assert new_tools <= allowed, f"{wf} filtra {new_tools - allowed}"


def test_agent_template_tools_exist_in_registry():
    """Ningún template de agente declara tools inexistentes (drift YAML↔specs)."""
    from pathlib import Path

    for yml in Path("templates/agents").glob("*.yaml"):
        if yml.name.startswith("_"):
            continue
        spec = yaml.safe_load(open(yml))
        for tool in spec.get("tools", []):
            assert tool in TOOL_DEFINITIONS, f"{yml.name} declara tool inexistente: {tool}"


def test_presets_que_escriben_exigen_proyecto():
    """Los presets con tools de escritura deben declarar project.required=true.

    Sin proyecto seleccionado, una tool de escritura no debe tener destino.
    Solo collaborative (debate puro) queda false.
    """
    from pathlib import Path

    ESCRIBEN = {"tdd", "bdd", "sdd", "domain_tdd", "edd", "reflexion", "development", "coordinated"}
    for name in sorted(ESCRIBEN):
        path = Path("templates/workflows") / f"{name}.yaml"
        doc = yaml.safe_load(path.read_text())
        assert doc["project"]["required"] is True, f"{name} debe exigir proyecto"
        tools = " ".join(doc["tools"]["allowed"])
        assert any(
            t in tools for t in ("file_manager", "diff_editor", "bash_manager")
        ), f"{name} clasificado como 'escribe' pero su allowlist no tiene tools de escritura"


# ── gemelos: el body compartido no puede divergir en silencio ──────────


def _doc_preset(nombre: str) -> dict:
    return yaml.safe_load(
        (paths.templates_workflows_dir() / f"{nombre}.yaml").read_text(encoding="utf-8")
    )


def _body_de_loop(doc: dict, loop_id: str) -> list[dict]:
    loop = next(s for s in doc["steps"] if s.get("id") == loop_id)
    return loop.get("body") or []


def _canonico(steps: list[dict], con_args: bool = True) -> list[dict]:
    """Estructura de un body SIN prosa: todo menos id y goal.

    Tres cambios de una sola ola hubo que replicarse a mano en los gemelos, y
    la divergencia development/coordinated produjo un bug de `$iter` en
    paralelo que solo la conformance cazó. Este guard lo caza en el diff.
    """
    out = []
    for s in steps:
        item = {k: v for k, v in s.items() if k not in ("id", "goal")}
        if not con_args:
            item.pop("args", None)
        # `produces` lleva el NOMBRE del artefacto (historia vs escenario):
        # se normaliza para comparar presencia/forma, no el nombre.
        if "produces" in item:
            item["produces"] = [
                p.replace("historia", "<x>").replace("escenario", "<x>") for p in item["produces"]
            ]
        out.append(item)
    return out


def test_gemelos_identicos_development_coordinated():
    """El body del loop es compartido: cualquier cambio va en AMBOS o ninguno."""
    a = _canonico(_body_de_loop(_doc_preset("development"), "ejecutar_subtareas"))
    b = _canonico(_body_de_loop(_doc_preset("coordinated"), "ejecutar"))
    assert a == b, (
        "el body del loop divergió entre development y coordinated — un cambio "
        "de uno sin su gemelo es exactamente cómo nació el bug de $iter en "
        "paralelo:\n"
        f"development: {a}\ncoordinated: {b}"
    )


def test_gemelos_de_forma_paralela_bdd_domain_tdd():
    """bdd/domain_tdd difieren en NOMBRES deliberados (historia/escenario) pero
    la secuencia estructural del body debe seguir siendo la misma."""
    a = _canonico(_body_de_loop(_doc_preset("bdd"), "ciclo_bdd"), con_args=False)
    b = _canonico(_body_de_loop(_doc_preset("domain_tdd"), "ciclo_escenario"), con_args=False)
    assert a == b, (
        "la forma del body divergió entre bdd y domain_tdd (kinds/agents/tools "
        f"distintos):\nbdd: {a}\ndomain_tdd: {b}"
    )


def test_gemelos_declaran_su_artefacto_de_entrega():
    """La normalización del gemelo no puede dejar el `produces` sin guard."""
    bdd = _doc_preset("bdd")
    dom = _doc_preset("domain_tdd")
    b = next(s for s in _body_de_loop(bdd, "ciclo_bdd") if s["id"] == "test_fallido")
    d = next(s for s in _body_de_loop(dom, "ciclo_escenario") if s["id"] == "test_escenario")
    assert "tests/test_historia_$iter.py" in b["produces"]
    assert "tests/test_escenario_$iter.py" in d["produces"]


# ── contrato de entrega en presets: evidencia, reserva y replan ────────


def _fragment_doc(nombre: str) -> dict:
    name = str(nombre).lstrip("_")
    return yaml.safe_load(
        (paths.templates_workflows_dir() / f"_{name}.yaml").read_text(encoding="utf-8")
    )


def _steps_con_fragments(steps: list[dict]) -> list[dict]:
    """Steps de primer nivel + fragments expandidos + bodies de loop."""
    out: list[dict] = []
    for step in steps:
        if step.get("kind") == "fragment":
            out.extend(_steps_con_fragments(_fragment_doc(str(step["include"]))["steps"]))
            continue
        out.append(step)
        if isinstance(step.get("body"), list):
            out.extend(_steps_con_fragments(step["body"]))
    return out


def test_presets_de_codigo_declaran_reserva_y_replan():
    """Sin reserva, el loop arranca ciclos que mueren en la cabecera del
    agent-loop; sin replan, un fallo medido solo puede terminar el run."""
    dev = _doc_preset("development")
    assert int(dev.get("budget_reserve_final") or 0) == 40000
    assert dev["budget_reserve_replan"] == 30000
    loop = next(s for s in dev["steps"] if s.get("id") == "ejecutar_subtareas")
    replan = loop.get("replan") or {}
    assert replan.get("max") == 1
    assert replan.get("max_new_items") == 4

    coord = _doc_preset("coordinated")
    assert int(coord.get("budget_reserve_final") or 0) == 40000
    assert int(coord.get("budget_reserve_replan") or 0) == 0
    loop_c = next(s for s in coord["steps"] if s.get("id") == "ejecutar")
    assert "replan" not in loop_c, "loop.parallel no soporta replan (el validador lo rechaza)"


def test_fragment_subtareas_declara_contrato_de_entrega():
    """El body compartido: el agente debe escribir el test de la subtarea y la
    verificación del ciclo es la que decide la entrega."""
    frag = _fragment_doc("cuerpo_subtareas")
    sub = next(s for s in frag["steps"] if s["id"] == "subtarea")
    assert "tests/test_subtarea_$iter.py" in sub["produces"]
    assert sub["verified_by"] == "verificar_subtarea"
    ver = next(s for s in frag["steps"] if s["id"] == "verificar_subtarea")
    assert ver["verdict"] == "pasan"


def test_sdd_mide_la_spec_y_la_entrega():
    sdd = _doc_preset("sdd")
    assert sdd["budget_reserve_final"] == 60000
    assert sdd["budget_reserve_replan"] == 30000
    steps = {s["id"]: s for s in _steps_con_fragments(sdd["steps"])}
    # el revisor sin $spec revisaba la nada: la spec corregida viaja al plan
    assert "$spec" in steps["revisar_spec"]["goal"]
    assert steps["corregir_spec"]["min_output_chars"] >= 1500
    assert (steps["implementar"].get("replan") or {}).get("max") == 1
    assert steps["subtarea"]["produces"]
    assert steps["subtarea"]["verified_by"] == "verificar_subtarea"


def test_bdd_revisa_historias_y_mide_la_entrega():
    bdd = _doc_preset("bdd")
    assert bdd["budget_reserve_final"] == 60000
    steps = {s["id"]: s for s in _steps_con_fragments(bdd["steps"])}
    assert steps["revisar_historias"]["on_blocker"] == "stop"
    assert steps["revisar_historias"]["gate"]
    assert steps["test_fallido"]["produces"]
    assert steps["implementar"]["produces"]
    assert steps["implementar"]["verified_by"] == "tests"
    assert steps["tests"]["verdict"] == "pasan"


def test_tdd_declara_produces_y_verifica_con_until():
    tdd = _doc_preset("tdd")
    steps = {s["id"]: s for s in _steps_con_fragments(tdd["steps"])}
    assert "$test_path" in " ".join(steps["implementar"]["produces"])
    loop = steps["ciclo"]
    assert loop["until"]["tool"] == "test_runner"
    assert "replan" not in loop, "tdd no replanifica: el until ya cubre el ciclo"


# ── capacidades del motor sin uso en presets (inventario vivo) ─────────


def _docs_dsl_de_producto() -> list[dict]:
    """Docs DSL de producto + presets de workspaces aislados (runtime incluido:
    el loader prefiere ws-local, así que el uso real se mide ahí también)."""
    docs = []
    directorios = [paths.templates_workflows_dir()]
    base = paths.workspaces_base()
    if base.exists():
        directorios += sorted(p / "workflows" for p in base.iterdir())
    for d in directorios:
        if not d.exists():
            continue
        for f in sorted(d.glob("*.yaml")):
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            if data.get("version") == 1:
                docs.append(data)
    return docs


def test_capacidades_sin_uso_en_presets_son_las_conocidas():
    """Inventario vivo de capacidades del motor que NINGÚN preset usa.

    No es pecado tenerlas (son la API de extensibilidad para workflows de
    usuario y están testeadas): el pecado es que el conjunto CREZCA EN
    SILENCIO. Toda capacidad nueva nace con un preset que la use — y su caso
    de conformance — o se documenta aquí con motivo explícito.
    """
    import re

    # Sin uso en presets. checkpoint y when:query_contains SÍ se usan (presets
    # de workspace, p. ej. `cli new`) y por eso NO están aquí.
    conocidas = {
        "kind:decide",
        "kind:parallel",  # ramas estáticas (loop.parallel SÍ se usa)
        "kind:workflow",  # include anidado
        "commit_after",
        "decompose:dag",
        "on_error",
    }
    texto = ""
    for doc in _docs_dsl_de_producto():
        texto += yaml.safe_dump(doc)
    uso = {
        "kind:decide": "kind: decide" in texto,
        "kind:parallel": re.search(r"kind:\s*parallel\b", texto) is not None,
        "kind:workflow": "kind: workflow" in texto,
        "commit_after": "commit_after" in texto,
        "decompose:dag": "strategy: dag" in texto,
        "on_error": re.search(r"^\s*on_error:", texto, re.M) is not None,
    }
    realmente_sin_uso = {k for k, usado in uso.items() if not usado}
    assert realmente_sin_uso == conocidas, (
        f"capacidades sin uso en presets: {sorted(realmente_sin_uso)}\n"
        f"inventario conocido:           {sorted(conocidas)}\n"
        "— toda capacidad nueva nace con un preset que la use (y su caso de "
        "conformance) o entra aquí con motivo explícito."
    )


def test_plugins_huerfanas_sin_uso_son_las_conocidas():
    """Inventario vivo de PRIMITIVAS PLUGIN que NINGÚN preset usa.

    El guard de capacidades cubre el MOTOR; las primitivas plugin son la
    otra mitad de la superficie de extensión — nada impedía que el conjunto
    de huérfanas creciera en silencio (misma clase de drift que ese guard
    dice combatir). Toda primitiva nueva nace con un preset que la use —
    y su caso de conformance — o se documenta aquí con motivo explícito.
    """
    # La registry se puebla POR IMPORT de plugins (el efecto lo dispara el
    # runtime_adapter en producción): el guard lo importa explícitamente.
    import orchestration.dsl.plugins  # noqa: F401
    from orchestration.dsl.cli import _SKELETONS
    from orchestration.dsl.registry import known_primitives

    conocidas = {
        "pick_line",  # mecanismo documentado: el modelo nombra el archivo (sin usuarios en presets de producto)
        "dag_to_levels",  # compañero de decompose:dag (capacidad del motor sin uso en presets)
    }

    def _plugins_de(steps):
        for s in steps or []:
            if not isinstance(s, dict):
                continue
            if s.get("kind") == "plugin" and s.get("plugin"):
                yield str(s["plugin"])
            yield from _plugins_de(s.get("body"))
            branches = s.get("branches")
            if isinstance(branches, list):
                for br in branches:
                    yield from _plugins_de((br or {}).get("steps"))
            elif isinstance(branches, dict):
                for sub in branches.values():
                    yield from _plugins_de(sub)

    usadas: set[str] = set()
    # Docs DSL de producto + workspaces (runtime incluido: el loader prefiere
    # ws-local, el uso real se mide ahí) + fragments (`_`-prefijados entran
    # al glob y pueden usar plugins en su body).
    directorios = [paths.templates_workflows_dir()]
    base = paths.workspaces_base()
    if base.exists():
        directorios += sorted(p / "workflows" for p in base.iterdir())
    for d in directorios:
        if not d.exists():
            continue
        for f in sorted(d.glob("*.yaml")):
            raw = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            if raw.get("version") == 1:
                usadas.update(_plugins_de(raw.get("steps")))
    for skeleton in _SKELETONS.values():
        usadas.update(s.get("plugin") for s in skeleton["steps"] if s.get("kind") == "plugin")

    huerfanas = set(known_primitives()) - usadas
    assert huerfanas == conocidas, (
        f"primitivas plugin sin uso: {sorted(huerfanas)}\n"
        f"inventario conocido:       {sorted(conocidas)}\n"
        "— toda primitiva nueva nace con un preset que la use (y su caso de "
        "conformance) o entra aquí con motivo explícito."
    )


# ── curación de presets: el producto elige 5, el resto es experimental ──


def test_curacion_de_presets_es_la_conocida():
    """CORE_PRESETS fija la curación del catálogo global.

    Decisión de producto con fiabilidad medida por preset
    (sesión E2E): los 5 core son los confiables; los 4 experimentales se
    conservan compilando porque son la cobertura viva de mecánicas del motor
    (edd es el único usuario en producción de until.metric). Que esta lista
    cambie en silencio revertiría la decisión de producto.
    """
    from orchestration.loader import CORE_PRESETS, is_core_preset

    assert CORE_PRESETS == {"tdd", "bdd", "development", "collaborative", "sdd"}, (
        "la curación cambió — es una decisión de producto, no un refactor: "
        "actualizar la tabla del plan y el README junto con esta lista"
    )
    # los experimentales siguen existiendo como globales (si alguien los
    # borra del catálogo, edd se lleva con él el único usuario de until.metric)
    for nombre in ("edd", "coordinated", "domain_tdd", "reflexion"):
        assert (
            paths.templates_workflows_dir() / f"{nombre}.yaml"
        ).exists(), f"el preset experimental '{nombre}' desapareció del catálogo"
        assert not is_core_preset(nombre), f"'{nombre}' no debe ser core"


def test_presets_propios_del_workspace_son_siempre_core(tmp_path):
    """Un preset que NO existe como global (`cli new`, workspace propio)
    nació de una necesidad real: la curación es solo para los globales."""
    from orchestration.loader import is_core_preset

    assert is_core_preset("mi_preset")  # no existe como global
    assert is_core_preset("preset_local")  # no existe como global
    assert not is_core_preset("coordinated")  # global experimental


def test_presets_de_codigo_prohiben_meta_tests():
    """El agente ya escribió un test que corre la suite (recursión + OOM).

    El contrato va en el goal: el test prueba COMPORTAMIENTO; ejecutar pytest
    desde un test es una recursión, no una verificación.
    """
    esperados = {
        "development": "subtarea",
        "coordinated": "subtarea",
        "sdd": "escribir_tests",
        "bdd": "test_fallido",
        "tdd": "rojo",
        "domain_tdd": "test_escenario",
    }
    for preset, step_id in esperados.items():
        doc = _doc_preset(preset)
        step = next(s for s in _steps_con_fragments(doc["steps"]) if s.get("id") == step_id)
        goal = str(step.get("goal") or "").lower()
        assert "prohibido ejecutar pytest" in goal, (preset, step_id, goal)


MIN_BUDGET = {
    "bdd": 650000,
    "domain_tdd": 650000,
    "development": 500000,
    "coordinated": 550000,
    "sdd": 800000,
    "tdd": 200000,
    "edd": 200000,
    "reflexion": 200000,
    "collaborative": 150000,
}
MIN_LOOP = {
    "bdd": 200000,
    "domain_tdd": 200000,
    "development": 160000,
    "coordinated": 420000,
    "sdd": 250000,
    "tdd": 150000,
    "edd": 160000,
    "reflexion": 160000,
    "collaborative": 120000,
}
MIN_ITER = {
    "bdd": {"test_fallido": 16, "implementar": 16, "reparar_agregado": 10},
    "domain_tdd": {"test_escenario": 16, "implementar_escenario": 16, "reparar_agregado": 10},
    "development": {"subtarea": 16},
    "coordinated": {"subtarea": 16},
    "sdd": {"subtarea": 18, "reparar_agregado": 10},
    "tdd": {"implementar": 14},
}


def test_presets_con_margen():
    """Techos cortos cierran runs en Parcial: estos mínimos son el contrato
    de margen; el global del .env sigue en 800k."""
    for preset, min_budget in MIN_BUDGET.items():
        doc = _doc_preset(preset)
        steps = _steps_con_fragments(doc["steps"])
        found = {s["id"] for s in steps}
        expected_iter = MIN_ITER.get(preset, {})
        assert not (set(expected_iter) - found), (preset, set(expected_iter) - found)
        if preset in MIN_LOOP:
            assert any(s.get("kind") == "loop" for s in steps), preset
        if int(doc.get("budget_reserve_final") or 0) > 0:
            assert any(s.get("use_reserve_final") for s in steps), (
                preset,
                "reserva final sin step que la declare",
            )
        budget = int(doc.get("budget_tokens") or 0)
        reserves = int(doc.get("budget_reserve_final") or 0) + int(
            doc.get("budget_reserve_replan") or 0
        )
        assert budget >= min_budget, (preset, budget)
        assert budget <= 800000, (preset, budget)
        assert budget - reserves >= int(0.8 * budget), (preset, budget, reserves)
        for step in steps:
            if step.get("kind") == "loop":
                assert int(step.get("budget_tokens") or 0) < budget, (preset, step["id"])
            if step.get("kind") == "loop" and preset in MIN_LOOP:
                assert int(step.get("budget_tokens") or 0) >= MIN_LOOP[preset], (
                    preset,
                    step["id"],
                )
            if step.get("kind") == "agent" and preset in MIN_ITER:
                min_iter = MIN_ITER[preset].get(step["id"])
                if min_iter is not None:
                    assert int(step.get("max_iterations") or 0) >= min_iter, (
                        preset,
                        step["id"],
                    )


def test_contratos_margen_bdd_domain_tdd():
    """El contrato del analista es respetar el máximo pedido; el mensaje final
    debe ser un resumen, no el join crudo del analista/test."""
    bdd = _doc_preset("bdd")
    steps = {s["id"]: s for s in _steps_con_fragments(bdd["steps"])}
    assert (
        "máximo" in steps["historias"]["goal"].lower()
        or "maximo" in steps["historias"]["goal"].lower()
    )
    assert steps["parse_historias"]["params"]["max_items"] == 5
    assert steps["resumen"]["strategy"] == "confidence"
    assert steps["reparar_agregado"]["use_reserve_final"] is True
    assert "verdict" not in steps["verificar_agregado"]
    assert bdd["budget_reserve_final"] == 60000

    dom = _doc_preset("domain_tdd")
    dsteps = {s["id"]: s for s in _steps_con_fragments(dom["steps"])}
    assert (
        "máximo" in dsteps["escenarios_raw"]["goal"].lower()
        or "maximo" in dsteps["escenarios_raw"]["goal"].lower()
    )
    assert dsteps["parse_escenarios"]["params"]["max_items"] == 5
    assert dsteps["agregar"]["strategy"] == "confidence"
    assert dsteps["reparar_agregado"]["use_reserve_final"] is True
    assert "verdict" not in dsteps["verificar_agregado"]
    assert dom["budget_reserve_final"] == 60000


def test_contratos_margen_development_coordinated():
    """El decompose generó subtareas de análisis (leer/resumir) que el agente
    no puede cerrar: cada subtarea debe ser de implementación. Y un agregado
    rojo tiene reparador: el intermedio solo mide, el final decide."""
    for preset, dec_id in (("development", "descomponer"), ("coordinated", "planificar")):
        doc = _doc_preset(preset)
        steps = {s["id"]: s for s in _steps_con_fragments(doc["steps"])}
        assert "no generes subtareas de análisis" in steps[dec_id]["query"].lower(), preset
        assert int(doc.get("budget_reserve_final") or 0) == 40000, preset
        assert "verdict" not in steps["verificar_agregado"], preset
        assert steps["reparar_agregado"]["use_reserve_final"] is True, preset
        assert steps["reparar_agregado"]["max_iterations"] == 10, preset
        assert steps["reparar_agregado"]["when"] == "!$pasan_agregado", preset
        assert steps["verificar_agregado_final"]["verdict"] == "pasan_agregado_final", preset
        assert steps["agregar"]["strategy"] == "confidence", preset
    dev = _doc_preset("development")
    dsteps = {s["id"]: s for s in _steps_con_fragments(dev["steps"])}
    assert (dsteps["ejecutar_subtareas"].get("replan") or {}).get("max") == 1
    coord = _doc_preset("coordinated")
    csteps = {s["id"]: s for s in _steps_con_fragments(coord["steps"])}
    assert "replan" not in csteps["ejecutar"]
    assert csteps["verificar"]["max_iterations"] == 6
    assert csteps["verificar"]["timeout"] == 600
    assert _doc_preset("coordinated")["budget_tokens"] == 550000
    assert csteps["planificar"]["max_items"] == 4
    assert csteps["ejecutar"]["max_iter"] == 4
    assert csteps["ejecutar"]["parallel_max"] == 4
    assert csteps["ejecutar"]["budget_tokens"] == 420000


def test_contratos_margen_sdd():
    """sdd cerraba en Parcial con el agregado en rojo y sin reparador; el
    mensaje final era el join crudo de la spec y los pasos de análisis
    exploraban el proyecto sin necesidad."""
    sdd = _doc_preset("sdd")
    steps = {s["id"]: s for s in _steps_con_fragments(sdd["steps"])}
    assert "verdict" not in steps["verificar_agregado"], "el intermedio solo mide"
    assert steps["reparar_agregado"]["use_reserve_final"] is True
    assert steps["reparar_agregado"]["max_iterations"] == 10
    assert steps["verificar_agregado_final"]["verdict"] == "pasan_agregado_final"
    assert steps["agregar"]["strategy"] == "confidence"
    assert "no generes subtareas de análisis" in steps["plan"]["query"].lower()
    assert "no explores" in steps["revisar_spec"]["goal"].lower()
    traza = steps["trazabilidad"]["goal"].lower()
    assert "podés leer" in traza or "puedes leer" in traza  # trazar exige leer
    assert "no explores" not in traza  # prohibir leer deja la traza sin poder trazar
    for sid in ("redactar_spec", "trazabilidad"):
        assert steps[sid]["max_iterations"] == 10, sid
        assert steps[sid]["timeout"] == 600, sid
    # cerrar_brechas: ampliar el cap no corrige el thrash y cuesta más;
    # se queda en el cap original como limitación conocida del step.
    assert steps["cerrar_brechas"]["max_iterations"] == 10
    assert steps["cerrar_brechas"]["timeout"] == 600
    # la spec se acota (6) para no comerse el budget antes de implementar
    for sid in ("revisar_spec", "corregir_spec"):
        assert steps[sid]["max_iterations"] == 6, sid
        assert steps[sid]["timeout"] == 600, sid
    corregir = steps["corregir_spec"]["goal"].lower()
    assert "texto" in corregir and "spec" in corregir
    assert "no escribas archivos" not in corregir  # inaplicable: el modelo igual escribió


def test_contratos_margen_tdd_experimentales():
    """tdd cerraba con el join crudo del test/implementación en vez de un
    resumen; el resto de experimentales solo necesitaba margen (Task 1)."""
    tdd = _doc_preset("tdd")
    steps = {s["id"]: s for s in _steps_con_fragments(tdd["steps"])}
    assert steps["resumen"]["strategy"] == "confidence"
    assert _doc_preset("edd")["budget_tokens"] == 200000
    assert _doc_preset("reflexion")["budget_tokens"] == 200000
    assert _doc_preset("collaborative")["budget_tokens"] == 150000


def test_presets_con_decompose_exigen_filtro_de_subtareas():
    """La prohibición en el query del decompose es soft: sin filtro
    determinista, subtareas de análisis/docs llegan al loop."""
    for preset in ("development", "coordinated", "sdd"):
        doc = _doc_preset(preset)
        steps = doc["steps"]
        dec = next(i for i, s in enumerate(steps) if s.get("kind") == "decompose")
        loop = next(i for i, s in enumerate(steps) if s.get("kind") == "loop")
        filtros = [s for s in steps[dec + 1 : loop] if s.get("plugin") == "filtrar_items"]
        assert len(filtros) == 1, preset
        assert filtros[0]["params"]["var"] == "subtasks", preset
        assert filtros[0]["params"]["out"] == "subtasks", preset
        assert filtros[0]["params"]["patrones"], preset


def test_collaborative_declara_la_ronda():
    """Sin contexto de ronda, cada opinión re-expresa la postura inicial y el
    debate degenera en copias: el goal debe declarar la ronda."""
    doc = _doc_preset("collaborative")
    steps = {s["id"]: s for s in _steps_con_fragments(doc["steps"])}
    for sid in ("opinion_developer", "opinion_analista"):
        goal = steps[sid]["goal"]
        assert "$iter" in goal, sid
        assert "$max_iter" in goal, sid
        assert "no repitas" in goal.lower(), sid


def test_patrones_del_filtro_por_preset():
    """El set compartido descartó subtareas legítimas (test/README) del
    decompose de development/coordinated; sdd sí las descarta (su body ya
    escribe los tests)."""
    comunes = {
        "leer y analizar",
        "analizar el proyecto",
        "explorar el repo",
        "ejecutar los tests",
        "verificar la spec",
    }
    esperados = {
        "development": comunes,
        "coordinated": comunes,
        "sdd": comunes | {"crear test", "crear spec", "documenta"},
    }
    for preset, expected in esperados.items():
        doc = _doc_preset(preset)
        steps = doc["steps"]
        dec = next(i for i, s in enumerate(steps) if s.get("kind") == "decompose")
        loop = next(i for i, s in enumerate(steps) if s.get("kind") == "loop")
        filtro = next(s for s in steps[dec + 1 : loop] if s.get("plugin") == "filtrar_items")
        patrones = set(filtro["params"]["patrones"])
        assert patrones == expected, (preset, patrones ^ expected)


def test_verificar_de_coordinated_es_read_only_y_las_verificaciones_dan_margen():
    """El analista de `verificar` es read-only: el goal no puede invitarlo a
    ejecutar shell/escrituras (el motor se lo niega y el step stalla). Y las
    verificaciones del agregado declaran timeout propio: el default de 30s
    convierte un cuelgue transitorio en `unmeasured` con la entrega verde."""
    coord = _doc_preset("coordinated")
    csteps = {s["id"]: s for s in _steps_con_fragments(coord["steps"])}
    goal = csteps["verificar"]["goal"].lower()
    assert "verifica la ejecución completa" not in goal
    assert "test_runner" in goal, "debe verificar con la medición que tiene"
    assert "read-only" in goal or "solo en lectura" in goal
    assert "file_manager.read" in goal

    for preset in ("bdd", "sdd", "development", "domain_tdd", "coordinated"):
        steps = {s["id"]: s for s in _steps_con_fragments(_doc_preset(preset)["steps"])}
        for sid in ("verificar_agregado", "verificar_agregado_final"):
            timeout = int((steps[sid].get("args") or {}).get("timeout") or 0)
            assert timeout >= 120, (preset, sid, timeout)


def test_analista_read_only_ve_solo_su_superficie():
    """El perfil read-only declara test_runner (medir sin escribir) y NO bash,
    diff_editor, git_manager ni code_exec: el loop recorta la lista visible a
    esa superficie, así el modelo no gasta iteraciones en tools denegadas."""
    import yaml as _yaml

    from core.path_resolver import paths

    p = paths.templates_agents_dir() / "analista.yaml"
    doc = _yaml.safe_load(p.read_text(encoding="utf-8"))
    tools = set(doc["tools"])
    assert "test_runner" in tools
    for prohibida in ("bash_manager", "diff_editor", "git_manager", "code_exec"):
        assert prohibida not in tools, (prohibida, tools)
    assert doc["read_only"] is True


def test_sdd_acota_la_spec_y_el_plan_para_caber_en_800k():
    """sdd podía agotar el presupuesto antes de implementar: la spec se acota
    a 6 iteraciones y el plan a 4."""
    sdd = _doc_preset("sdd")
    steps = {s["id"]: s for s in _steps_con_fragments(sdd["steps"])}
    assert steps["revisar_spec"]["max_iterations"] == 6
    assert steps["corregir_spec"]["max_iterations"] == 6
    assert steps["plan"]["max_items"] == 4
    # los pasos de spec son TEXTO puro: sin tools no hay exploración ni
    # escritura que compita con el contrato de la spec.
    assert steps["redactar_spec"]["tools"] == []
    assert steps["corregir_spec"]["tools"] == []
    # el gate de la subtarea corre la SUITE COMPLETA: un subtask que toca el
    # módulo compartido no puede romper tests anteriores en silencio.
    body = steps["implementar"]["body"]
    verificar = next(s for s in body if s["id"] == "verificar_subtarea")
    assert verificar["args"]["file_path"] == "tests"
    # el test lo escribe OTRO agente antes del loop: el implementador no puede
    # escribir un test que su implementación no cumple (rojo real de telefonos).
    autor = steps["escribir_tests"]
    assert autor["agent"] == "test_author"
    assert autor["tools"] == ["file_manager"]
    assert autor["produces"] == ["tests/test_subtarea_*.py"]
    assert "no lo edites" in steps["implementar"]["body"][0]["goal"].lower()
    assert "meta-tests" in steps["escribir_tests"]["goal"].lower()
    assert "test_author" in sdd["agents"]["allowed"]
    # precisión de la spec: la definición manda sobre un ejemplo contado a mano
    assert "definición" in steps["redactar_spec"]["goal"].lower()
    assert "no cuentes" in steps["redactar_spec"]["goal"].lower()
    assert "consistencia interna" in steps["revisar_spec"]["goal"].lower()
    assert "ejemplos" in steps["corregir_spec"]["goal"].lower()
    # trazabilidad DEBE ver la spec: sin $spec el analista la infiere leyendo
    # el proyecto y agota el cap.
    assert "$spec" in steps["trazabilidad"]["goal"]
    assert "traza completa" in steps["trazabilidad"]["goal"].lower()
    # cerrar_brechas debe ver la traza que produce trazabilidad
    assert "$last_output" in steps["cerrar_brechas"]["goal"]
    # los descartes del filtro son POR CONTRATO en sdd (el body escribe los
    # tests): el plan los muestra saltados, pero no degradan ni son pendientes.
    filtro = next(s for s in sdd["steps"] if s.get("plugin") == "filtrar_items")
    assert filtro["params"].get("contrato") is True


def test_loops_secuenciales_acotan_el_item_y_no_solo_la_vuelta():
    """Una subtarea cara no puede comerse la vuelta y bloquear al resto:
    development y sdd declaran max_tokens_per_item además del budget de vuelta."""
    for preset, loop_id in (("development", "ejecutar_subtareas"), ("sdd", "implementar")):
        doc = _doc_preset(preset)
        steps = {s["id"]: s for s in _steps_con_fragments(doc["steps"])}
        loop = steps[loop_id]
        cap = int(loop.get("max_tokens_per_item") or 0)
        assert cap >= 50000, (preset, cap)
        assert cap <= int(loop.get("budget_tokens") or 0), (preset, cap)


def test_long_horizon_tiene_techo_e_items_para_tareas_largas():
    """El perfil long-horizon declara 1.5M (bajo el hard cap de 2M), 10 ítems
    y el loop más largo; el gate de la subtarea corre la suite completa."""
    lh = _doc_preset("long_horizon")
    steps = {s["id"]: s for s in _steps_con_fragments(lh["steps"])}
    assert lh["budget_tokens"] == 1_500_000
    assert lh["budget_tokens"] <= 2_000_000  # hard cap de la plataforma
    assert steps["plan"]["max_items"] == 10
    assert steps["implementar"]["max_iter"] == 12
    assert steps["implementar"]["budget_tokens"] == 1_200_000
    assert steps["escribir_tests"]["agent"] == "test_author"
    body = steps["implementar"]["body"]
    verificar = next(s for s in body if s["id"] == "verificar_subtarea")
    assert verificar["args"]["file_path"] == "tests"
    # cerrar_brechas no amplifica el stall con 3 intentos
    assert steps["cerrar_brechas"]["retry_max"] == 1
    assert steps["reparar_agregado"]["use_reserve_final"] is True


def test_sdd_cerrar_brechas_no_reintenta_el_stall_tres_veces():
    sdd = _doc_preset("sdd")
    steps = {s["id"]: s for s in _steps_con_fragments(sdd["steps"])}
    assert steps["cerrar_brechas"]["retry_max"] == 1
