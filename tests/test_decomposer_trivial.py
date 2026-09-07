"""Tareas triviales con 1 subtarea del LLM no reciben fantasma 'Verificar'."""

from unittest.mock import AsyncMock, patch

import pytest


def _raw_single(subtask: str) -> str:
    return f'{{"subtasks": ["{subtask}"]}}'


@pytest.mark.asyncio
async def test_trivial_task_keeps_single_subtask():
    """Query corta + proyecto nuevo + 1 subtarea → NO se fuerza división."""
    with (
        patch(
            "orchestration.decomposer.models.call",
            new=AsyncMock(return_value=_raw_single("crea hola.py que imprima hola")),
        ),
        patch(
            "orchestration.decomposer._build_project_context",
            new=AsyncMock(return_value="PROYECTO NUEVO — sin archivos."),
        ),
    ):
        from orchestration.decomposer import decompose_task

        subtasks = await decompose_task("crea hola.py que imprima hola")

    assert len(subtasks) == 1, f"fantasma agregado a tarea trivial: {subtasks}"
    assert "Verificar" not in subtasks[0]


@pytest.mark.asyncio
async def test_existing_project_still_gets_split():
    """Control: proyecto existente con 1 subtarea SÍ fuerza la división."""
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp())
    proj = tmp / "proj"
    proj.mkdir()
    (proj / "main.py").write_text("print(1)\n", encoding="utf-8")

    with (
        patch(
            "orchestration.decomposer.models.call",
            new=AsyncMock(return_value=_raw_single("modifica main.py")),
        ),
        patch("orchestration.decomposer.paths") as mock_paths,
    ):
        mock_paths.project_dir.return_value = proj

        from orchestration.decomposer import decompose_task

        subtasks = await decompose_task("mejora el main", project_root="proj")

    assert len(subtasks) >= 2, f"control roto: {subtasks}"
