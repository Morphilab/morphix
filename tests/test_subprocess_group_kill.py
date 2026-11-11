# tests/test_subprocess_group_kill.py
"""Contención del árbol de procesos de test_runner.

Un test que lanza un nieto y se cuelga: al timeout, el nieto debe morir con el
grupo del pytest hijo. Sin start_new_session + killpg, el nieto queda huérfano
y puede sobrevivir al proceso que lo lanzó.
"""

from __future__ import annotations

import asyncio
import os
import signal
import uuid
from pathlib import Path

import pytest

from tools.test_runner import _test_runner_tool


def _pids_with_marker(marker: str) -> list[int]:
    pids: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == os.getpid():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ")
        except OSError:
            continue
        if marker.encode() in cmdline:
            pids.append(pid)
    return pids


async def _wait_for_marker(marker: str, timeout: float = 3.0) -> list[int]:
    """Espera a que el nieto exista (import de pytest + spawn del módulo)."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        pids = _pids_with_marker(marker)
        if pids:
            return pids
        await asyncio.sleep(0.05)
    return []


@pytest.mark.asyncio
async def test_timeout_mata_el_arbol_de_pytest(tmp_path, monkeypatch):
    marker = f"MORPHIX_REPRO_{uuid.uuid4().hex}"
    (tmp_path / "test_spawn.py").write_text(
        f"""
import subprocess, sys, time

MARKER = "{marker}"
subprocess.Popen([sys.executable, "-c", f"import time; time.sleep(60)  # {{MARKER}}"])

def test_sleep():
    time.sleep(60)
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.test_runner.paths.memory_dir", lambda workspace: tmp_path)
    result = await _test_runner_tool(file_path="test_spawn.py", workspace="main", timeout=4)
    assert result["success"] is False
    assert "timeout" in result["output"].lower()

    await asyncio.sleep(0.5)
    survivors = _pids_with_marker(marker)
    try:
        assert survivors == [], f"el árbol de pytest sobrevivió al timeout: {survivors}"
    finally:
        for pid in survivors:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.asyncio
async def test_cancelacion_mata_el_arbol(tmp_path, monkeypatch):
    marker = f"MORPHIX_REPRO_{uuid.uuid4().hex}"
    (tmp_path / "test_spawn.py").write_text(
        f"""
import subprocess, sys, time

MARKER = "{marker}"
subprocess.Popen([sys.executable, "-c", f"import time; time.sleep(60)  # {{MARKER}}"])

def test_sleep():
    time.sleep(60)
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.test_runner.paths.memory_dir", lambda workspace: tmp_path)
    task = asyncio.create_task(
        _test_runner_tool(file_path="test_spawn.py", workspace="main", timeout=30)
    )
    spawned = await _wait_for_marker(marker, timeout=3.0)
    assert spawned, "el nieto no llegó a lanzarse"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.5)
    survivors = _pids_with_marker(marker)
    try:
        assert survivors == [], f"la cancelación dejó huérfanos: {survivors}"
    finally:
        for pid in survivors:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_kill_process_tree_nunca_senala_pgid_1(monkeypatch):
    """Un mock sin pid real resuelve getpgid→1; killpg(1) = kill(-1).

    Regresión: el helper no debe señalar nunca un
    grupo ≤ 1 (mataría toda la sesión del usuario). El test mockea killpg,
    así que ni siquiera un guard roto puede dañar la sesión.
    """
    from unittest.mock import MagicMock

    from tools._subprocess import _kill_process_tree

    proc = MagicMock()
    proc.pid = 1
    killpg = MagicMock()
    monkeypatch.setattr("tools._subprocess.os.getpgid", lambda pid: 1)
    monkeypatch.setattr("tools._subprocess.os.killpg", killpg)

    _kill_process_tree(proc)

    killpg.assert_not_called()
    proc.kill.assert_called_once()
