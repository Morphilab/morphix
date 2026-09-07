# desktop/services/git_service.py — lectura/cambio de rama para la GUI
"""Servicio Qt-free del selector de rama (convención desktop/services).

v1: SOLO ramas locales, checkout bloqueado si el árbol está
dirty (sin stash, sin force, sin creación de ramas). Reutiliza los guards
de tools/git_manager (_git_env_guard, get_repo_lock, _scrubbed) — mismas
garantías de entorno/serialización que el tool del agente.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from core.path_resolver import paths
from tools.git_manager import _git_env_guard, get_repo_lock

logger = logging.getLogger(__name__)


def _resolve_repo_path(workspace: str, project_root: str | None) -> Path | None:
    """Misma resolución/contención que tools/git_manager (base memory_dir)."""
    if not project_root:
        return None
    normalized = paths.normalize_project_root(project_root)
    if not normalized:
        return None
    base = paths.memory_dir(workspace)
    repo_path = (base / normalized).resolve()
    try:
        repo_path.relative_to(base.resolve())
    except ValueError:
        return None
    return repo_path


def _open_repo(workspace: str, project_root: str | None) -> Any | None:
    """Repo GitPython o None (sin proyecto/sin repo). Caller debe to_thread."""
    from git import Repo  # noqa: PLC0415 — import tardío como git_manager

    if not project_root:
        return None
    repo_path = _resolve_repo_path(workspace, project_root)
    if repo_path is None or not (repo_path / ".git").exists():
        return None
    with _git_env_guard():
        return Repo(repo_path)


def is_git_repo(workspace: str, project_root: str | None) -> bool:
    """True si el proyecto activo tiene un repositorio Git inicializado."""
    repo_path = _resolve_repo_path(workspace, project_root)
    return bool(repo_path and (repo_path / ".git").exists())


async def list_branches(workspace: str, project_root: str | None) -> dict | None:
    """{current: str, local: [str, ...]} — SOLO ramas locales (heads).

    None si no hay proyecto/repo. El repo lock serializa contra el tool
    del agente (mismo repo).
    """
    repo = await asyncio.to_thread(_open_repo, workspace, project_root)
    if repo is None:
        return None
    async with get_repo_lock(str(repo.working_dir)):

        def _read() -> dict:
            with _git_env_guard():
                current = repo.active_branch.name if repo.head.is_valid() else ""
                return {
                    "current": current,
                    "local": sorted(h.name for h in repo.heads),
                }

        return await asyncio.to_thread(_read)


async def checkout_branch(
    workspace: str, project_root: str | None, branch: str
) -> tuple[bool, str]:
    """Cambia a `branch` (local). RECHAZA si el árbol está dirty — v1 sin
    stash/force. Retorna (ok, mensaje-para-usuario)."""
    repo = await asyncio.to_thread(_open_repo, workspace, project_root)
    if repo is None:
        return False, "❌ Sin repositorio Git en el proyecto activo."

    async with get_repo_lock(str(repo.working_dir)):

        def _do() -> tuple[bool, str]:
            with _git_env_guard():
                if branch not in [h.name for h in repo.heads]:
                    return False, f"❌ La rama '{branch}' no existe (local)."
                if repo.is_dirty(untracked_files=False):
                    return (
                        False,
                        "❌ Hay cambios sin commitear — haz commit antes de " "cambiar de rama.",
                    )
                repo.git.checkout(branch)
                return True, f"✅ Rama: {branch}"

        ok, msg = await asyncio.to_thread(_do)
        if ok:
            logger.info("checkout de rama GUI: %s → %s", project_root, branch)
        return ok, msg
