"""Switch/delete de workspaces atómicos."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_switch_restores_previous_schema_on_failure():
    """Fallo post-set_async_schema (p.ej. load_workspace_agents) → schema restaurado."""
    import core.workspaces as ws_mod
    from core.workspaces import Workspaces

    mgr = Workspaces()

    restored = []

    async def fake_set(name):
        restored.append(name)
        if name == "nuevo_ws" and len(restored) == 1:
            raise RuntimeError("fallo cargando agentes")  # tras cambiar schema
        # llamada de restauración y fallbacks OK

    with (
        patch.object(ws_mod, "create_schema", new=AsyncMock()),
        patch.object(ws_mod, "create_tables_in_schema", new=AsyncMock()),
        patch.object(ws_mod, "set_async_schema", side_effect=fake_set),
        patch("core.workspaces.memory") as mock_mem,
        patch.object(ws_mod.Workspaces, "_bootstrap_workspace_agents", lambda *a, **k: None),
        patch.object(ws_mod.Workspaces, "_bootstrap_workspace_workflows", lambda *a, **k: None),
        patch.object(ws_mod.Workspaces, "_bootstrap_workspace_hooks", lambda *a, **k: None),
    ):
        mock_mem.switch_workspace = AsyncMock(return_value=None)
        ok = await mgr.switch_workspace("nuevo_ws", retries=1)

    assert "main" in restored, f"schema previo no restaurado; llamadas a set: {restored}"


@pytest.mark.asyncio
async def test_delete_aborts_when_escape_switch_fails(monkeypatch):
    """Si el escape-switch falla, NO se hace drop del schema."""
    import core.workspaces as ws_mod
    from core.workspaces import Workspaces

    mgr = Workspaces()
    mgr.current = "objetivo"

    dropped = {"v": False}

    async def fake_drop(name):
        dropped["v"] = True
        return True

    with (
        patch.object(mgr, "switch_workspace", new=AsyncMock(return_value=False)),
        patch.object(ws_mod, "drop_schema", side_effect=fake_drop),
    ):
        ok = await mgr.delete_workspace("objetivo")

    assert ok is False
    assert dropped["v"] is False, "drop ejecutado pese al fallo de escape"


# ═══════════ switch_guard ═══════════


@pytest.mark.asyncio
async def test_switch_guard_blocks_switch():
    """Guard que retorna False bloquea el switch sin tocar BD."""
    import core.workspaces as ws_mod
    from core.workspaces import Workspaces

    mgr = Workspaces()
    calls = []

    def veto(name):
        calls.append(name)
        return False

    mgr.set_switch_guard(veto)

    with (
        patch.object(ws_mod, "create_schema", new=AsyncMock()) as mock_schema,
        patch.object(mgr, "_do_switch_workspace", new=AsyncMock()),
    ):
        ok = await mgr.switch_workspace("cualquier")

    assert ok is False
    assert calls == ["cualquier"]
    mock_schema.assert_not_awaited(), "el veto debe cortar antes del schema"


@pytest.mark.asyncio
async def test_switch_guard_allows_when_permits():
    """Guard que retorna True deja pasar al flujo normal."""
    from core.workspaces import Workspaces

    mgr = Workspaces()
    mgr.set_switch_guard(lambda name: True)

    async def fake_do(name, retries):
        mgr.current = name
        return True

    with patch.object(mgr, "_do_switch_workspace", side_effect=fake_do):
        ok = await mgr.switch_workspace("destino")

    assert ok is True
    assert mgr.current == "destino"


def test_set_switch_guard_none_disables():
    """set_switch_guard(None) desactiva el veto (limpia todos los guards)."""
    from core.workspaces import Workspaces

    mgr = Workspaces()
    mgr.set_switch_guard(lambda _n: False)
    mgr.set_switch_guard(None)
    assert mgr._switch_guards == {}


@pytest.mark.asyncio
async def test_list_workspaces_includes_disk_only_workspaces(tmp_path, monkeypatch):
    """Parte A: workspaces dados de alta en disco (sin schema DB) aparecen."""
    import core.workspaces as ws_mod
    from core.workspaces import Workspaces

    async def fake_schemas():
        return ["main"]

    base = tmp_path / "workspaces"
    (base / "novuscv" / "agents").mkdir(parents=True)
    (base / "novuscv" / "workflows").mkdir()
    (base / "contentflow" / "workflows").mkdir(parents=True)
    (base / "Nombre-Invalido").mkdir()  # regex [a-z][a-z0-9_]* falla
    (base / "vacio").mkdir()  # sin subdirectorios marcadores
    (base / "sueltos.txt").write_text("x", encoding="utf-8")  # no es dir

    from core.path_resolver import paths

    monkeypatch.setattr(paths, "workspaces_base", lambda: base)

    with patch.object(ws_mod, "list_schemas", new=fake_schemas):
        out = await Workspaces().list_workspaces()

    assert "main" in out
    assert "novuscv" in out, f"workspace solo-disco no listado: {out}"
    assert "contentflow" in out, f"workspace solo-disco no listado: {out}"
    assert "Nombre-Invalido" not in out
    assert "vacio" not in out
    assert "sueltos.txt" not in out
    assert out == sorted(out), "listado no ordenado"


@pytest.mark.asyncio
async def test_list_workspaces_dedupes_db_and_disk(tmp_path, monkeypatch):
    """Un workspace presente en DB y disco aparece UNA sola vez."""
    import core.workspaces as ws_mod
    from core.workspaces import Workspaces

    async def fake_schemas():
        return ["main", "novuscv"]

    base = tmp_path / "workspaces"
    (base / "novuscv" / "agents").mkdir(parents=True)

    from core.path_resolver import paths

    monkeypatch.setattr(paths, "workspaces_base", lambda: base)

    with patch.object(ws_mod, "list_schemas", new=fake_schemas):
        out = await Workspaces().list_workspaces()

    assert out.count("novuscv") == 1
    assert out == ["main", "novuscv"]


def test_disk_only_names_pass_switch_validation():
    """Los nombres descubiertos por disco son válidos para switch_workspace."""
    from core.workspaces import Workspaces

    for name in ("novuscv", "contentflow"):
        assert Workspaces._validate_workspace_name(name) == name


@pytest.mark.asyncio
async def test_list_workspaces_survives_db_error_with_disk(tmp_path, monkeypatch):
    """Si la DB falla, los workspaces de disco siguen visibles (degradación)."""
    import core.workspaces as ws_mod
    from core.workspaces import Workspaces

    async def broken_schemas():
        raise RuntimeError("DB caida")

    base = tmp_path / "workspaces"
    (base / "novuscv" / "workflows").mkdir(parents=True)

    from core.path_resolver import paths

    monkeypatch.setattr(paths, "workspaces_base", lambda: base)

    with patch.object(ws_mod, "list_schemas", new=broken_schemas):
        out = await Workspaces().list_workspaces()

    assert out == ["novuscv"]


# ── Aislamiento de workspaces producto (manifest isolate:true) ──


def _build_iso_env(tmp_path):
    """templates/ + base fake: globales mínimos + novuscv aislado con lo propio."""
    from pathlib import Path

    templates = tmp_path / "templates"
    base = tmp_path / "workspaces"

    (templates / "agents").mkdir(parents=True)
    for agent in ("conversacional", "developer"):
        (templates / "agents" / f"{agent}.yaml").write_text(f"name: {agent}\n", encoding="utf-8")
    (templates / "agents" / "_FULL_TEMPLATE.yaml").write_text("# doc\n", encoding="utf-8")
    (templates / "workflows").mkdir()
    (templates / "workflows" / "development.yaml").write_text(
        "name: development\ntype: development\n", encoding="utf-8"
    )
    (templates / "hooks").mkdir()
    (templates / "skills" / "demo").mkdir(parents=True)
    (templates / "skills" / "demo" / "SKILL.md").write_text("x\n", encoding="utf-8")

    nov = templates / "workspaces" / "novuscv"
    (nov / "agents").mkdir(parents=True)
    (nov / "workflows").mkdir()
    (nov / "agents" / "asesor.yaml").write_text("name: asesor\n", encoding="utf-8")
    (nov / "agents" / "revisor.yaml").write_text("name: revisor\n", encoding="utf-8")
    (nov / "workflows" / "postulacion.yaml").write_text(
        "name: postulacion\ntype: pipeline\n", encoding="utf-8"
    )
    (templates / "workspaces.yaml").write_text(
        "workspaces:\n  - {name: novuscv, isolate: true}\n  - {name: otro}\n",
        encoding="utf-8",
    )
    del Path
    return templates, base


def _install_switch_stubs(monkeypatch, templates, base):
    """Redirige paths/DB/loaders para ejecutar el switch real sin efectos."""
    from unittest.mock import AsyncMock

    import core.workspaces as ws_mod
    from core.path_resolver import PathResolver

    monkeypatch.setattr(PathResolver, "templates_dir", staticmethod(lambda: templates))
    monkeypatch.setattr(
        PathResolver, "templates_agents_dir", staticmethod(lambda: templates / "agents")
    )
    monkeypatch.setattr(
        PathResolver,
        "templates_workflows_dir",
        staticmethod(lambda: templates / "workflows"),
    )
    monkeypatch.setattr(
        PathResolver, "templates_hooks_dir", staticmethod(lambda: templates / "hooks")
    )
    monkeypatch.setattr(
        PathResolver, "templates_skills_dir", staticmethod(lambda: templates / "skills")
    )
    monkeypatch.setattr(
        PathResolver,
        "workspace_workflows_dir",
        staticmethod(lambda n: base / n / "workflows"),
    )
    monkeypatch.setattr(PathResolver, "workspace_dir", staticmethod(lambda n: base / n))
    monkeypatch.setattr(
        PathResolver, "workspace_agents_dir", staticmethod(lambda n: base / n / "agents")
    )
    monkeypatch.setattr(
        PathResolver, "workspace_hooks_dir", staticmethod(lambda n: base / n / "hooks")
    )
    monkeypatch.setattr(
        PathResolver, "workspace_skills_dir", staticmethod(lambda n: base / n / "skills")
    )
    monkeypatch.setattr(ws_mod, "create_schema", AsyncMock())
    monkeypatch.setattr(ws_mod, "create_tables_in_schema", AsyncMock())
    monkeypatch.setattr(ws_mod, "set_async_schema", AsyncMock())
    # El sync de bots toca la tabla real de la BD; este test cubre assets de
    # templates, no BD — sin stub, el switch depende de que exista `bots` en
    # el schema resuelto por el search_path del entorno.
    monkeypatch.setattr("core.bot_templates.bootstrap_workspace_bots", AsyncMock())
    monkeypatch.setattr(ws_mod, "memory", type("M", (), {"switch_workspace": AsyncMock()})())
    monkeypatch.setattr(ws_mod, "switch_workflow_state", lambda name: None)
    monkeypatch.setattr("agents.loader.load_workspace_agents", lambda n: None)
    monkeypatch.setattr("agents.loader.unload_workspace_agents", lambda: None)
    monkeypatch.setattr("tools.loader.load_workspace_tools", lambda n: None)
    monkeypatch.setattr("tools.loader.unload_workspace_tools", lambda: None)
    monkeypatch.setattr("core.hook_loader.load_workspace_hooks", lambda n: None)
    monkeypatch.setattr("core.hook_loader.unload_workspace_hooks", lambda: None)
    monkeypatch.setattr("core.mcp.client.connect_mcp_servers", AsyncMock())
    monkeypatch.setattr("core.mcp.client.disconnect_mcp_servers", AsyncMock())


@pytest.mark.asyncio
async def test_isolated_switch_does_not_inherit_globals(tmp_path, monkeypatch):
    """Switch a workspace isolate:true: monta SOLO lo propio + núcleo
    conversacional; no hereda agents/workflows/skills globales; hace prune."""
    from core.workspaces import Workspaces

    templates, base = _build_iso_env(tmp_path)
    # Pre-poblar heredados de una instalación previa:
    (base / "novuscv" / "agents").mkdir(parents=True)
    (base / "novuscv" / "agents" / "developer.yaml").write_text("name: developer\n")
    (base / "novuscv" / "agents" / "_FULL_TEMPLATE.yaml").write_text("#\n")
    (base / "novuscv" / "workflows").mkdir()
    (base / "novuscv" / "workflows" / "development.yaml").write_text("type: development\n")

    _install_switch_stubs(monkeypatch, templates, base)
    ok = await Workspaces().switch_workspace("novuscv")

    assert ok is True
    agents = sorted(p.name for p in (base / "novuscv" / "agents").glob("*.yaml"))
    workflows = sorted(p.name for p in (base / "novuscv" / "workflows").glob("*.yaml"))
    assert agents == ["asesor.yaml", "conversacional.yaml", "revisor.yaml"], agents
    assert workflows == ["postulacion.yaml"], workflows
    assert not (base / "novuscv" / "skills").exists(), "skills heredadas en aislado"


@pytest.mark.asyncio
async def test_non_isolated_workspace_keeps_inheriting(tmp_path, monkeypatch):
    """Regression: workspace NO declarado aislado sigue heredando globales."""
    from core.workspaces import Workspaces

    templates, base = _build_iso_env(tmp_path)
    _install_switch_stubs(monkeypatch, templates, base)
    ok = await Workspaces().switch_workspace("otro")

    assert ok is True
    agents = {p.name for p in (base / "otro" / "agents").glob("*.yaml")}
    assert {"conversacional.yaml", "developer.yaml"} <= agents
    assert "_FULL_TEMPLATE.yaml" not in agents  # underscore nunca se copia
    assert (base / "otro" / "workflows" / "development.yaml").exists()


def test_prune_inherited_templates_idempotent(tmp_path, monkeypatch):
    """Prune borra duplicados de templates globales y conserva los propios."""
    from core.path_resolver import PathResolver
    from core.workspaces import Workspaces

    templates, base = _build_iso_env(tmp_path)
    monkeypatch.setattr(PathResolver, "templates_dir", staticmethod(lambda: templates))
    monkeypatch.setattr(
        PathResolver, "templates_agents_dir", staticmethod(lambda: templates / "agents")
    )
    monkeypatch.setattr(
        PathResolver,
        "templates_workflows_dir",
        staticmethod(lambda: templates / "workflows"),
    )
    monkeypatch.setattr(PathResolver, "workspace_dir", staticmethod(lambda n: base / n))

    ws_agents = base / "novuscv" / "agents"
    ws_agents.mkdir(parents=True)
    (ws_agents / "developer.yaml").write_text("name: developer\n")  # heredado
    (ws_agents / "_FULL_TEMPLATE.yaml").write_text("#\n")  # heredado
    (ws_agents / "asesor.yaml").write_text("name: asesor\n")  # propio
    (ws_agents / "custom_usuario.yaml").write_text("name: custom\n")  # propio extra
    ws_wf = base / "novuscv" / "workflows"
    ws_wf.mkdir(parents=True)
    (ws_wf / "development.yaml").write_text("x\n")  # heredado
    (ws_wf / "postulacion.yaml").write_text("x\n")  # propio

    removed = Workspaces.prune_inherited_templates("novuscv")
    assert sorted(removed) == [
        str(base / "novuscv" / "agents" / "_FULL_TEMPLATE.yaml"),
        str(base / "novuscv" / "agents" / "developer.yaml"),
        str(base / "novuscv" / "workflows" / "development.yaml"),
    ]
    assert (ws_agents / "asesor.yaml").exists()
    assert (ws_agents / "custom_usuario.yaml").exists()
    assert (ws_wf / "postulacion.yaml").exists()

    # Idempotente
    assert Workspaces.prune_inherited_templates("novuscv") == []


def test_bootstrap_agents_skips_underscore(tmp_path, monkeypatch):
    """_bootstrap_workspace_agents nunca copia plantillas `_`-prefijadas."""
    from core.workspaces import Workspaces

    src = tmp_path / "tpl_agents"
    dst = tmp_path / "ws_agents"
    src.mkdir()
    dst.mkdir()
    (src / "developer.yaml").write_text("name: developer\n", encoding="utf-8")
    (src / "_FULL_TEMPLATE.yaml").write_text("# doc\n", encoding="utf-8")

    Workspaces._bootstrap_workspace_agents(dst, src)

    copied = {p.name for p in dst.glob("*.yaml")}
    assert copied == {"developer.yaml"}
