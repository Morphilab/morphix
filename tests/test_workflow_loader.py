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
async def test_list_workflows_ignores_underscore_files():
    from orchestration.loader import list_workflows

    for workflows in (list_workflows(), list_workflows("main")):
        assert "_FULL_TEMPLATE" not in workflows
