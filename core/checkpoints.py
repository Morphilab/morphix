# core/checkpoints.py — checkpoints de proyecto: snapshots nombrados con ledger
"""Snapshots versionados del árbol de archivos de un proyecto.

Diseño propio, sin duplicar lo que ya existe:

- ``core/change_tracker.py`` ya cubre undo/redo POR FICHERO (LIFO de
  before-writes). Este módulo añade lo que aquel no puede: restaurar un
  estado NOMBRADO de TODO el proyecto en un punto del tiempo.
- El rewind conversacional ya tiene su
  equivalente no destructivo en ``ConversationRepository.create_branch``
  (copia los mensajes hasta ``branch_point`` en una conversación nueva):
  aquí no se re-implementa.

Contratos:

- **Identidad por proyecto**: cada proyecto se direcciona por el hash de
  su raíz resuelta (``dir_hash``) — dos proyectos del mismo
  workspace jamás comparten ledger.
- **Ledger + manifiesto**: ``ledger.json`` por proyecto (lista ordenada de
  checkpoints) + ``manifest.json`` por checkpoint con sha256 por fichero;
  el restore VERIFICA la integridad antes de escribir y reporta drift.
- **Restore honesto**: sobrescribe los ficheros capturados; los ficheros
  creados DESPUÉS del checkpoint se dejan en su sitio y se reportan
  (``left_in_place``) — jamás se borra nada implícitamente.
- **Presupuestos y exclusiones**: sin .git/__pycache__/.venv/node_modules,
  tope por fichero y por snapshot total; los excedidos se listan, no se
  copian en silencio.
- **Guardas de ruta**: el restore valida cada ruta relativa del manifiesto
  (sin ``..``, sin absolutas) y que el destino caiga DENTRO de la raíz del
  proyecto — un manifiesto manipulado no puede escapar del proyecto.

Almacenamiento (mismo vecindario que el ChangeTracker):
``workspaces/<ws>/checkpoints/<dir_hash>/<checkpoint_id>/{files...,
manifest.json}`` + ``ledger.json`` en ``<dir_hash>/``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from core.errors import MorphixError
from core.path_resolver import PathResolver as Paths

logger = logging.getLogger(__name__)

LEDGER_FILE = "ledger.json"
MANIFEST_FILE = "manifest.json"
FILES_DIR = "files"

# Presupuestos por snapshot (los conteos se calculan antes de capturar;
# aquí son topes duros).
MAX_FILES = 5000
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024

EXCLUDED_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".undo",
    ".redo",
    "checkpoints",
}
EXCLUDED_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".so",
    ".bin",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".woff",
    ".woff2",
    ".lock",
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class CheckpointError(MorphixError, ValueError):
    """Error de dominio del subsistema (mensaje accionable)."""

    code = "checkpoints"


# ── Identidad del proyecto ───────────────────────────────────────────────────


def _dir_hash(project_root: Path) -> str:
    return hashlib.sha256(str(project_root).encode("utf-8")).hexdigest()[:12]


def _store_root(workspace: str, project_root: Path) -> Path:
    return Paths.workspace_dir(workspace) / "checkpoints" / _dir_hash(project_root)


def _safe_relpath(rel: str) -> PurePosixPath:
    """Ruta relativa válida del manifiesto o CheckpointError (anti-traversal)."""
    pure = PurePosixPath(rel)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise CheckpointError(f"ruta de manifiesto inválida: {rel!r}")
    return pure


# ── Ledger ──────────────────────────────────────────────────────────────────


def _load_ledger(store: Path) -> list[dict]:
    path = store / LEDGER_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _save_ledger(store: Path, ledger: list[dict]) -> None:
    store.mkdir(parents=True, exist_ok=True)
    _write_atomic(store / LEDGER_FILE, ledger)


def _write_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _slugify(description: str) -> str:
    slug = _SLUG_RE.sub("-", description.strip().lower()).strip("-")
    return slug[:40] or "checkpoint"


# ── API ─────────────────────────────────────────────────────────────────────


def create_checkpoint(
    workspace: str,
    project_root: str | Path,
    description: str,
    *,
    paths: list[str] | None = None,
) -> dict:
    """Captura el estado del proyecto en un snapshot nombrado.

    ``paths`` restringe la captura a un subconjunto (relativas al
    proyecto); None captura el árbol completo bajo presupuestos.
    Devuelve el resumen con ``checkpoint_id`` y lo que quedó fuera.
    """
    if not description or not description.strip():
        raise CheckpointError("describe el checkpoint (description vacía)")
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise CheckpointError(f"no es un directorio de proyecto: {root}")

    store = _store_root(workspace, root)
    ts = datetime.now(UTC)
    stamp = ts.strftime("%Y%m%d%H%M%S")
    checkpoint_id = f"{stamp}_{_slugify(description)}"
    dest = store / checkpoint_id / FILES_DIR

    captured: list[dict] = []
    skipped_oversize: list[str] = []
    total = 0
    count = 0

    candidates: list[tuple[Path, str]]
    if paths is not None:
        candidates = []
        for rel in paths:
            pure = _safe_relpath(str(rel))
            absolute = root / Path(*pure.parts)
            if not absolute.is_file():
                raise CheckpointError(f"no existe en el proyecto: {rel}")
            candidates.append((absolute, str(pure)))
    else:
        candidates = [
            (p, str(PurePosixPath(*p.relative_to(root).parts))) for p in sorted(root.rglob("*"))
        ]

    for absolute, rel in candidates:
        if count >= MAX_FILES or total >= MAX_TOTAL_BYTES:
            skipped_oversize.append(rel)
            continue
        if paths is None and _excluded(absolute, root):
            continue
        size = absolute.stat().st_size
        if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
            skipped_oversize.append(rel)
            continue
        target = dest / Path(*PurePosixPath(rel).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(absolute, target)
        captured.append({"path": rel, "bytes": size, "sha256": _sha256_file(target)})
        total += size
        count += 1

    manifest = {
        "checkpoint_id": checkpoint_id,
        "created_at": ts.isoformat(),
        "description": description.strip(),
        "project_root": str(root),
        "files": captured,
        "skipped_oversize": skipped_oversize,
        "total_bytes": total,
    }
    _write_atomic(store / checkpoint_id / MANIFEST_FILE, manifest)

    ledger = _load_ledger(store)
    ledger.append(
        {
            "checkpoint_id": checkpoint_id,
            "created_at": manifest["created_at"],
            "description": manifest["description"],
            "files": count,
            "total_bytes": total,
        }
    )
    _save_ledger(store, ledger)
    logger.info("checkpoint %s: %d ficheros (%d B) de %s", checkpoint_id, count, total, root)
    return {
        "checkpoint_id": checkpoint_id,
        "files": count,
        "total_bytes": total,
        "skipped_oversize": skipped_oversize,
        "project": str(root),
    }


def _excluded(absolute: Path, root: Path) -> bool:
    if not absolute.is_file():
        return True
    if absolute.suffix.lower() in EXCLUDED_SUFFIXES:
        return True
    for part in absolute.relative_to(root).parts[:-1]:
        if part in EXCLUDED_DIRS:
            return True
    return False


def list_checkpoints(workspace: str, project_root: str | Path) -> list[dict]:
    """Ledger del proyecto, más reciente primero."""
    store = _store_root(workspace, Path(project_root).resolve())
    return list(reversed(_load_ledger(store)))


def get_checkpoint(workspace: str, project_root: str | Path, checkpoint_id: str) -> dict | None:
    """Manifiesto completo de un checkpoint (None si no existe)."""
    store = _store_root(workspace, Path(project_root).resolve())
    manifest_path = store / checkpoint_id / MANIFEST_FILE
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def restore_checkpoint(workspace: str, project_root: str | Path, checkpoint_id: str) -> dict:
    """Restaura los ficheros capturados — verificación sha256 previa.

    Los ficheros creados después del checkpoint NO se borran: se reportan
    en ``left_in_place`` (restore honesto, jamás destructivo implícito).
    """
    manifest = get_checkpoint(workspace, project_root, checkpoint_id)
    if manifest is None:
        raise CheckpointError(f"checkpoint no encontrado: {checkpoint_id}")
    root = Path(manifest["project_root"]).resolve()
    store = _store_root(workspace, root) / checkpoint_id / FILES_DIR

    # 0) validación ESTRUCTURAL del manifiesto: rutas relativas sanas antes
    # de cualquier verificación de contenido (un ../ es peor señal que drift)
    for entry in manifest["files"]:
        _safe_relpath(entry["path"])

    # 1) verificación de integridad ANTES de escribir nada
    drifted: list[str] = []
    for entry in manifest["files"]:
        snap = store / Path(*PurePosixPath(entry["path"]).parts)
        if not snap.is_file() or _sha256_file(snap) != entry["sha256"]:
            drifted.append(entry["path"])
    if drifted:
        raise CheckpointError(
            f"integridad comprometida en {checkpoint_id} (sha256 drift): " + ", ".join(drifted[:5])
        )

    restored: list[str] = []
    for entry in manifest["files"]:
        pure = _safe_relpath(entry["path"])
        target = root / Path(*pure.parts)
        resolved = target.resolve()
        if root not in resolved.parents and resolved != root:
            raise CheckpointError(f"el restore escaparía del proyecto: {entry['path']}")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(store / Path(*pure.parts), resolved)
        restored.append(entry["path"])

    current = _walk_project(root)
    captured_set = {e["path"] for e in manifest["files"]}
    left_in_place = sorted(set(current) - captured_set)
    logger.info(
        "restore %s: %d restaurados, %d intactos post-checkpoint",
        checkpoint_id,
        len(restored),
        len(left_in_place),
    )
    return {
        "checkpoint_id": checkpoint_id,
        "restored": restored,
        "left_in_place": left_in_place,
    }


def _walk_project(root: Path) -> set[str]:
    out: set[str] = set()
    if not root.is_dir():
        return out
    for path in root.rglob("*"):
        if path.is_file() and not _excluded(path, root):
            out.add(str(PurePosixPath(*path.relative_to(root).parts)))
    return out


def delete_checkpoint(workspace: str, project_root: str | Path, checkpoint_id: str) -> bool:
    """Borra UN checkpoint (snapshot + entrada de ledger). False si no existe."""
    store = _store_root(workspace, Path(project_root).resolve())
    snap_dir = store / checkpoint_id
    if not snap_dir.is_dir():
        return False
    shutil.rmtree(snap_dir)
    ledger = [e for e in _load_ledger(store) if e["checkpoint_id"] != checkpoint_id]
    _save_ledger(store, ledger)
    return True


def prune_checkpoints(workspace: str, project_root: str | Path, *, keep_last: int = 10) -> int:
    """Conserva los ``keep_last`` más recientes; borra el resto.

    Devuelve cuántos eliminó.
    """
    if keep_last < 1:
        raise CheckpointError("keep_last debe ser >= 1")
    store = _store_root(workspace, Path(project_root).resolve())
    ledger = _load_ledger(store)  # orden temporal: viejo → nuevo
    survivors = ledger[-keep_last:]
    doomed = {e["checkpoint_id"] for e in ledger[:-keep_last] if len(ledger) > keep_last}
    removed = 0
    for checkpoint_id in sorted(doomed):
        snap_dir = store / checkpoint_id
        if snap_dir.is_dir():
            shutil.rmtree(snap_dir)
            removed += 1
    _save_ledger(store, survivors)
    return removed
