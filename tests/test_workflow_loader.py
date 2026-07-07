# tests/test_workflow_loader.py

import pytest


@pytest.mark.asyncio
async def test_list_workflows_includes_default():
    from orchestration.loader import list_workflows

    workflows = list_workflows()
    assert isinstance(workflows, list)
    assert "development" in workflows


@pytest.mark.asyncio
async def test_list_workflows_with_workspace():
    from orchestration.loader import list_workflows

    workflows = list_workflows("main")
    assert isinstance(workflows, list)
    assert "development" in workflows
    assert "collaborative" in workflows


@pytest.mark.asyncio
async def test_list_workflows_moi_workspace():
    """Workspaces with no local files get global fallback."""
    from orchestration.loader import list_workflows

    workflows = list_workflows("nonexistent_ws")
    assert isinstance(workflows, list)
    assert "development" in workflows
    assert "collaborative" in workflows


@pytest.mark.asyncio
async def test_load_workflow_template_default_global():
    from orchestration.loader import load_workflow_template

    template = load_workflow_template(None, "default")
    assert isinstance(template, dict)


@pytest.mark.asyncio
async def test_load_workflow_template_not_found_returns_empty():
    from orchestration.loader import load_workflow_template

    template = load_workflow_template(None, "nonexistent_workflow_12345")
    assert template == {}


@pytest.mark.asyncio
async def test_load_workflow_template_from_workspace():
    from orchestration.loader import load_workflow_template

    template = load_workflow_template("main", "development")
    assert isinstance(template, dict)
    assert template.get("name") == "development"
    assert "agents" in template
    assert "project" in template
