"""Status honesto del finalizer + retención de snapshots."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestration.status import save_status_snapshot


@pytest.mark.asyncio
async def test_finalize_workflow_persists_real_status():
    """Cancelled → fila Workflow con status='cancelled'."""
    import networkx as nx

    with (
        patch(
            "core.security.undercover_mode.undercover.get_safe_response_async",
            new=AsyncMock(side_effect=lambda t, *a, **k: t),
        ),
        patch("orchestration.finalizer.get_async_session") as mock_ctx,
        patch("orchestration.finalizer.memory_manager") as mock_mem,
    ):
        session = MagicMock()
        conv = MagicMock(id=9)
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.refresh = AsyncMock()
        session.get = AsyncMock(return_value=conv)

        async def fake_get(model, oid):
            return conv

        session.get = AsyncMock(return_value=conv)
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_mem.update_user_profile = AsyncMock(return_value=False)
        mock_mem.write = AsyncMock(return_value=True)

        from orchestration.finalizer import finalize_workflow

        await finalize_workflow(
            query="tarea",
            final_output="hecho a medias",
            conversation_history=[],
            scorecard={"tokens": 0, "subtasks": 1},
            subtasks_list=["a"],
            task_analysis={},
            G=nx.DiGraph(),
            events=MagicMock(on_system_message=AsyncMock(), on_assistant_message=AsyncMock()),
            project_root=None,
            workspace="main",
            files_written=[],
            conversation_id=9,
            status="cancelled",
        )

    added_objs = [c.args[0] for c in session.add.call_args_list]
    wfs = [o for o in added_objs if o.__class__.__name__ == "Workflow"]
    assert wfs, f"ninguna fila Workflow añadida: {added_objs}"
    assert wfs[-1].status == "cancelled", f"status no persistido: {wfs[-1].status}"


def test_snapshots_pruned_to_retention(tmp_path, monkeypatch):
    """25 snapshots → quedan los 20 más recientes."""
    from core.path_resolver import paths

    monkeypatch.setattr(paths.__class__, "charts_dir", staticmethod(lambda: tmp_path))
    now = time.time()
    for i in range(25):
        p = tmp_path / f"workflow_{i}.html"
        p.write_text(f"<html>{i}</html>", encoding="utf-8")
        import os

        os.utime(p, (now - 100 + i, now - 100 + i))

    save_status_snapshot("<html>new</html>", filename="workflow_new.html")

    remaining = sorted(tmp_path.glob("workflow_*.html"))
    assert len(remaining) <= 21, f"{len(remaining)} snapshots sin podar"
    names = [p.name for p in remaining]
    assert "workflow_new.html" in names
    assert "workflow_0.html" not in names, "los más viejos deben eliminarse primero"
