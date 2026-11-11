"""Status honesto del finalizer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_finalize_workflow_persists_real_status(tmp_path):
    """Cancelled → fila Workflow con status='cancelled'."""

    with (
        patch(
            "core.security.undercover_mode.undercover.get_safe_response_async",
            new=AsyncMock(side_effect=lambda t, *a, **k: t),
        ),
        patch("orchestration.finalizer.get_async_session") as mock_ctx,
        patch("orchestration.finalizer.memory_manager") as mock_mem,
        patch.object(
            __import__("core.path_resolver", fromlist=["paths"]).paths,
            "memory_dir",
            lambda ws: tmp_path,
        ),
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


def test_prune_old_snapshots_keeps_recent(tmp_path, monkeypatch):
    """La retención (vía _prune_old_snapshots) deja solo los más recientes."""
    import os
    import time

    from core.path_resolver import paths
    from orchestration.status import _prune_old_snapshots

    monkeypatch.setattr(paths.__class__, "charts_dir", staticmethod(lambda: tmp_path))
    now = time.time()
    for i in range(25):
        p = tmp_path / f"workflow_{i}.html"
        p.write_text(f"<html>{i}</html>", encoding="utf-8")
        os.utime(p, (now - 100 + i, now - 100 + i))

    removed = _prune_old_snapshots(keep=20)
    assert removed == 5, "los 5 más viejos eliminados"
    assert len(list(tmp_path.glob("workflow_*.html"))) == 20
