# tests/test_template_consistency.py
"""Guard tests: every agent/tool referenced by a workflow template must exist.

Prevents regressions like the `architect` agent or `browser` tool being
referenced by a workflow template without a backing profile/registration.
"""

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


def test_architect_profile_loads_correctly():
    """Architect agent must be loadable with type=analysis and model_role=reasoning."""
    agent_dirs = [paths.templates_agents_dir(), paths.workspace_agents_dir("main")]
    architect_yaml = None
    for d in agent_dirs:
        candidate = d / "architect.yaml"
        if candidate.exists():
            architect_yaml = candidate
            break

    assert architect_yaml is not None, "architect.yaml not found in templates or workspaces"

    data = yaml.safe_load(architect_yaml.read_text(encoding="utf-8"))
    assert data["type"] == "analysis", f"Expected type=analysis, got {data.get('type')}"
    assert (
        data["model_role"] == "reasoning"
    ), f"Expected model_role=reasoning, got {data.get('model_role')}"
    assert "file_manager" in (
        data.get("tools") or []
    ), "architect must have file_manager (read-only)"
    assert data.get("name") == "architect", f"Expected name=architect, got {data.get('name')}"


def test_architect_is_allowed_in_coordinated_workflow():
    """Coordinated workflow must list architect in agents.allowed."""
    wf_file = paths.workspace_workflows_dir("main") / "coordinated.yaml"
    if not wf_file.exists():
        wf_file = paths.templates_workflows_dir() / "coordinated.yaml"
    assert wf_file.exists(), "coordinated.yaml not found"

    template = yaml.safe_load(wf_file.read_text(encoding="utf-8"))
    allowed = template.get("agents", {}).get("allowed") or []
    assert "architect" in allowed, f"architect not in coordinated.yaml agents.allowed: {allowed}"


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
    """DSL: la subtarea de development declara retry (retry_max en el step)."""
    from pathlib import Path

    import yaml

    template_path = Path("templates/workflows/development.yaml")
    with open(template_path) as f:
        template = yaml.safe_load(f)
    assert template.get("version") == 1, "development.yaml debe ser DSL (version: 1)"
    body = template["steps"][1]["body"]
    assert body[0]["id"] == "subtarea"
    assert body[0]["retry_max"] == 2


def test_workflow_templates_reference_registered_tools():
    valid = _valid_tools()

    violations: list[str] = []
    for f in _workflow_files():
        template = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        allowed = template.get("tools", {}).get("allowed") or []
        for tool in allowed:
            if not isinstance(tool, str):
                continue
            if tool.startswith("mcp:") or tool.startswith("mcp_"):
                continue
            if tool not in valid:
                violations.append(f"{f.relative_to(paths.templates_dir().parent)} -> tool '{tool}'")

    assert not violations, "Workflow templates reference unregistered tools:\n" + "\n".join(
        violations
    )


def test_workflow_skills_flag_is_bool_when_present():
    """Si un workflow declara `skills`, debe ser bool."""
    for wf in _workflow_files():
        data = yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict) and "skills" in data:
            assert isinstance(data["skills"], bool), wf.name


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
    assert len(pipelines) >= 3, f"esperaba ≥3 pipelines de workspace, hay {len(pipelines)}"
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

    # capacidad: developer lleva la familia goal/todo; architect el planning
    dev = yaml.safe_load(open(Path("templates/agents/developer.yaml")))
    arch = yaml.safe_load(open(Path("templates/agents/architect.yaml")))
    assert new_tools - {"plan_mode", "exit_plan_mode"} <= set(dev["tools"])
    assert {"plan_mode", "exit_plan_mode"} <= set(arch["tools"])

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

    Antecedente: TDD llegó a escribir en prueba2/ sin proyecto seleccionado.
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
