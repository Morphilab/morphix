"""A-VII: el escaneo de proyecto del decomposer no debe congelar el event loop."""

import asyncio
import threading
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_build_project_context_does_not_block_loop():
    """future.result() síncrono congela el loop (UI incluida); to_thread no."""
    from orchestration.decomposer import _build_project_context

    release = threading.Event()

    def blocking_scan(*_a):
        release.wait(timeout=3)
        return "escaneado"

    with patch("orchestration.decomposer._scan_project_sync", side_effect=blocking_scan):
        task = asyncio.create_task(_build_project_context("code_projects/lab", "main"))

        # Si el loop quedara bloqueado en future.result(), este sleep no
        # completaría hasta que el scan terminara → task estaría done.
        await asyncio.sleep(0.15)
        assert not task.done(), "_build_project_context bloqueó el event loop durante el escaneo"

        release.set()
        result = await asyncio.wait_for(task, timeout=3)
        assert result == "escaneado"
