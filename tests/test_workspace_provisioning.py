"""Provisioning idempotente de workspaces desde templates/workspaces/."""

from unittest.mock import patch

import pytest


@pytest.fixture
def template_env(tmp_path, monkeypatch):
    """templates/ fake con manifest + un workspace provisionable."""
    from core.path_resolver import PathResolver

    templates = tmp_path / "templates"
    ws_base = tmp_path / "workspaces"

    demo_dir = templates / "workspaces" / "demo"
    (demo_dir / "agents").mkdir(parents=True)
    (demo_dir / "workflows").mkdir(parents=True)
    (demo_dir / "agents" / "agent_a.yaml").write_text("name: agent_a\n", encoding="utf-8")
    (demo_dir / "workflows" / "pipeline_demo.yaml").write_text(
        "name: pipeline_demo\ntype: pipeline\ndecomposition: pipeline\n"
        "execution: sequential_pipeline\nagents:\n  allowed: [agent_a]\nstages:\n"
        "  - {name: s1, agent: agent_a}\n"
        "  - {name: s2, agent: agent_a}\n",
        encoding="utf-8",
    )
    (templates / "workspaces.yaml").write_text(
        "workspaces:\n  - {name: demo, description: test}\n", encoding="utf-8"
    )

    monkeypatch.setattr(PathResolver, "templates_dir", staticmethod(lambda: templates))
    monkeypatch.setattr(PathResolver, "workspace_dir", staticmethod(lambda name: ws_base / name))
    return templates, ws_base, demo_dir


def test_provision_disabled_by_default(template_env):
    """OFF por defecto: sin AUTO_PROVISION_WORKSPACES no se copia nada."""
    from core.workspaces import Workspaces

    templates, ws_base, _demo = template_env
    with patch("core.config.settings.auto_provision_workspaces", False):
        out = Workspaces.provision_template_workspaces()
    assert out == []
    assert not (ws_base / "demo").exists()


def test_provision_copies_additively_and_idempotent(template_env):
    """Con flag ON: copia agentes/workflows; segunda corrida no sobreescribe."""
    from core.workspaces import Workspaces

    templates, ws_base, demo_dir = template_env
    with patch("core.config.settings.auto_provision_workspaces", True):
        first = Workspaces.provision_template_workspaces()
    assert "demo" in first
    dst_agent = ws_base / "demo" / "agents" / "agent_a.yaml"
    assert dst_agent.read_text(encoding="utf-8") == "name: agent_a\n"

    # Modificar el origen NO re-escribe lo ya provisionado (idempotente)
    (demo_dir / "agents" / "agent_a.yaml").write_text("name: MODIFICADO\n", encoding="utf-8")
    with patch("core.config.settings.auto_provision_workspaces", True):
        second = Workspaces.provision_template_workspaces()
    assert "demo" in second
    assert dst_agent.read_text(encoding="utf-8") == "name: agent_a\n"


def test_provision_skips_invalid_names(tmp_path, monkeypatch):
    """Entradas del manifest con nombre inválido se saltan con warning."""
    from core.path_resolver import PathResolver
    from core.workspaces import Workspaces

    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "workspaces.yaml").write_text(
        "workspaces:\n  - {name: 'Nombre-Malo'}\n  - {name: ''}\n", encoding="utf-8"
    )
    monkeypatch.setattr(PathResolver, "templates_dir", staticmethod(lambda: templates))
    monkeypatch.setattr(
        PathResolver,
        "workspace_dir",
        staticmethod(lambda name: tmp_path / "workspaces" / name),
    )

    with patch("core.config.settings.auto_provision_workspaces", True):
        out = Workspaces.provision_template_workspaces()

    assert out == []


def test_provision_tolerates_missing_manifest(tmp_path, monkeypatch):
    """Sin templates/workspaces.yaml → no-op silencioso (no crash)."""
    from core.path_resolver import PathResolver
    from core.workspaces import Workspaces

    templates = tmp_path / "templates"
    templates.mkdir()
    monkeypatch.setattr(PathResolver, "templates_dir", staticmethod(lambda: templates))

    with patch("core.config.settings.auto_provision_workspaces", True):
        out = Workspaces.provision_template_workspaces()

    assert out == []


@pytest.mark.asyncio
async def test_init_backend_provisions_when_enabled(template_env, monkeypatch):
    """Hook en init_backend: corre provisioning tras activar el workspace."""
    from unittest.mock import AsyncMock, patch

    import core.bootstrap as bootstrap_mod

    templates, ws_base, _demo = template_env

    with (
        patch("core.database.startup_db", new_callable=AsyncMock),
        patch("core.workspaces.get_global_workspaces") as gw,
        patch("core.hook_loader.load_global_hooks"),
        patch("core.workspaces.Workspaces.provision_template_workspaces") as mock_prov,
    ):
        gw.return_value.switch_workspace = AsyncMock()
        mock_prov.return_value = ["demo"]
        ok = await bootstrap_mod.init_backend(workspace="main")
        assert ok is True
        mock_prov.assert_called_once()
