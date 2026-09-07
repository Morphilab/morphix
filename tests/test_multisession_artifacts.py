# tests/test_multisession_artifacts.py
"""Multi-sesión: git serializado por proyecto y warning de colisión de archivos.

Dos sesiones sobre el MISMO proyecto no deben intercalar commits de git ni
escribir el mismo archivo sin aviso. Keying por project root evita falsos
positivos entre proyectos distintos. La colisión SOLO se detecta entre runs
concurrentes (un run que terminó no dispara falsas alarmas).
"""

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _cleanup_write_registry():
    yield
    from tools.file_manager import clear_file_write_registry

    clear_file_write_registry()


# ── Colisión de archivos ──


def test_same_run_repeated_write_no_collision():
    from core.workspaces import begin_workflow_run, end_workflow_run
    from tools.file_manager import record_file_write
    from tools.orchestrator import set_file_write_run

    token = begin_workflow_run()
    set_file_write_run(token)
    try:
        assert record_file_write("proj1", "a.py") is False
        assert record_file_write("proj1", "a.py") is False
    finally:
        end_workflow_run(token)


@pytest.mark.asyncio
async def test_cross_run_concurrent_write_same_file_warns():
    from core.workspaces import begin_workflow_run, end_workflow_run
    from tools.file_manager import record_file_write
    from tools.orchestrator import set_file_write_run

    ta = begin_workflow_run()
    tb = begin_workflow_run()
    results: dict[str, bool] = {}

    async def writer(tag: str, token: int):
        set_file_write_run(token)
        results[tag] = record_file_write("proj1", "shared.py")

    try:
        await asyncio.gather(writer("a", ta), writer("b", tb))
    finally:
        end_workflow_run(ta)
        end_workflow_run(tb)

    assert list(results.values()).count(True) == 1


def test_different_project_no_false_collision():
    from core.workspaces import begin_workflow_run, end_workflow_run
    from tools.file_manager import record_file_write
    from tools.orchestrator import set_file_write_run

    t1 = begin_workflow_run()
    t2 = begin_workflow_run()
    try:
        set_file_write_run(t1)
        assert record_file_write("proj1", "a.py") is False
        set_file_write_run(t2)
        assert record_file_write("proj2", "a.py") is False  # otro root
    finally:
        end_workflow_run(t1)
        end_workflow_run(t2)


@pytest.mark.asyncio
async def test_sequential_runs_no_collision():
    """Un run que TERMINÓ no dispara colisión para el siguiente."""
    from core.workspaces import begin_workflow_run, end_workflow_run
    from tools.file_manager import record_file_write
    from tools.orchestrator import set_file_write_run

    ta = begin_workflow_run()
    set_file_write_run(ta)
    assert record_file_write("proj1", "a.py") is False
    end_workflow_run(ta)

    tb = begin_workflow_run()
    set_file_write_run(tb)
    try:
        assert record_file_write("proj1", "a.py") is False
    finally:
        end_workflow_run(tb)


# ── Git lock por project root ──


def test_git_lock_shared_per_path():
    from tools.git_manager import get_repo_lock

    assert get_repo_lock("proj1") is get_repo_lock("proj1")


def test_git_lock_isolated_per_path():
    from tools.git_manager import get_repo_lock

    assert get_repo_lock("proj1") is not get_repo_lock("proj2")


@pytest.mark.asyncio
async def test_git_lock_serializes_concurrent_mutations():
    from tools.git_manager import get_repo_lock

    active = 0
    max_active = 0
    lock = get_repo_lock("proj1")

    async def critical():
        nonlocal active, max_active
        async with lock:
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(critical(), critical(), critical())

    assert max_active == 1
