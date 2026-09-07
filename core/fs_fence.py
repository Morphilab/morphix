# core/fs_fence.py
"""FS Fence — contención de mutaciones de archivos bajo raíces escribibles.

Las mutaciones solo se permiten
bajo las raíces escribibles del workspace (``memory/<ws>`` y su ``tmp``
aislado ``memory/<ws>/tmp`` — NO /tmp ni tmpdir del sistema); los
paths se re-canonicalizan antes de escribir y una denegación lleva un hint
estructurado ``[sandbox: …]``.

No es un sandbox de SO: es una capa de contención best-effort que
bash_manager/file_manager aplican sobre los targets que encuentran en los
comandos. Un comando arbitrario con ofuscación podría evadirla; la defensa en
profundidad la completan los FORBIDDEN_PATTERNS y el anclaje del cwd al
workspace.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from core.path_resolver import paths

logger = logging.getLogger(__name__)


def writable_roots(workspace: str) -> list[Path]:
    """Raíces donde el modelo puede escribir: workspace memory + tmp aislado por workspace."""

    workspace_root = paths.memory_dir(workspace).resolve()
    # tmp aislado por workspace bajo memory/<ws>/tmp
    ws_tmp = workspace_root / "tmp"
    ws_tmp.mkdir(parents=True, exist_ok=True)
    roots = [
        workspace_root,
        ws_tmp,
    ]
    # Deduplicar preservando orden (workspace primero)
    seen: set[str] = set()
    unique: list[Path] = []
    for r in roots:
        key = str(r)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def canonicalize(path: str | os.PathLike) -> Path:
    """Canonicaliza el path: expande user, resuelve symlinks y normaliza."""
    p = Path(path).expanduser()
    try:
        return p.resolve(strict=False)
    except OSError:  # pragma: no cover — raro, defensivo
        return p.absolute()


def is_within(path: Path, root: Path) -> bool:
    """True si ``path`` cae dentro de ``root`` (resolución canónica)."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def check_write_target(target: str, workspace: str) -> tuple[bool, str]:
    """Verifica que un target de escritura esté bajo una raíz escribible.

    Returns:
        (True, "") si es permitido, o (False, reason) con el hint estructurado
        ``[sandbox: …]`` para la denegación.
    """
    resolved = canonicalize(target)
    roots = writable_roots(workspace)
    if any(is_within(resolved, r) for r in roots):
        return True, ""
    roots_txt = ", ".join(str(r) for r in roots)
    return False, (f"[sandbox: {resolved} no está bajo una raíz escribible ({roots_txt})]")


def deny_hint(target: str, workspace: str) -> str:
    """Hint estructurado para el bloqueo (mismo formato que check_write_target)."""
    _, reason = check_write_target(target, workspace)
    return reason
