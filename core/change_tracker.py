"""Change Tracker — undo/redo de cambios de archivos.

Antes de cada file_manager.write, guarda una copia de respaldo.
El usuario puede revertir cambios con el comando 'undo'.
"""

import logging
import time
from pathlib import Path
from urllib.parse import quote, unquote

from core.path_resolver import paths

logger = logging.getLogger(__name__)


def _encode_path(file_path: str) -> str:
    """Codifica una ruta de archivo para usarla como nombre de backup.
    Usa URL encoding para preservar caracteres especiales sin ambigüedad."""
    return quote(file_path, safe="")


def _decode_path(encoded: str) -> str:
    """Decodifica el nombre de backup a la ruta original."""
    return unquote(encoded)


class ChangeTracker:
    """Registra cambios de archivos para permitir undo."""

    def __init__(self, workspace: str = "main", project_root: str | None = None):
        self.workspace = workspace
        self.project_root = project_root
        self._undo_dir = paths.memory_dir(workspace) / ".undo"
        self._redo_dir = paths.memory_dir(workspace) / ".redo"
        self._undo_dir.mkdir(parents=True, exist_ok=True)
        self._redo_dir.mkdir(parents=True, exist_ok=True)

    def save_before_write(self, file_path: str) -> str | None:
        """Guarda el contenido actual antes de sobrescribir. Retorna el key de undo."""
        full_path = self._resolve(file_path)
        if not full_path.exists():
            return None

        timestamp = int(time.time() * 1000)
        safe_name = _encode_path(file_path)
        backup_path = self._undo_dir / f"{timestamp}_{safe_name}"

        try:
            content = full_path.read_text(encoding="utf-8")
            backup_path.write_text(content, encoding="utf-8")
            logger.info(f"Backup guardado: {backup_path.name}")

            # Clean up old backups (more than 100)
            backups = sorted(self._undo_dir.glob("*"))
            for old in backups[:-100]:
                old.unlink()

            return backup_path.name
        except Exception as e:
            logger.error(f"Error guardando backup: {e}")
            return None

    def undo_last(self) -> str | None:
        """Undo the last change. Returns the restored file path."""
        backups = sorted(self._undo_dir.glob("*"))
        if not backups:
            return None

        last_backup = backups[-1]
        name = last_backup.name
        if "_" not in name:
            logger.warning("Backup con nombre inválido (sin '_'): %s", name)
            return None
        original_path = _decode_path(name.split("_", 1)[1])

        try:
            # Mover archivo actual a redo
            current = self._resolve(original_path)
            if current.exists() and "_" in name:
                parts = name.split("_", 1)
                if len(parts) >= 2:
                    redo_backup = self._redo_dir / f"{int(time.time() * 1000)}_{parts[1]}"
                    redo_backup.write_text(current.read_text(encoding="utf-8"))

            # Restore backup
            current.write_text(last_backup.read_text(encoding="utf-8"))
            last_backup.unlink()
            logger.info(f"Undo aplicado: {original_path}")
            return original_path
        except Exception as e:
            logger.error(f"Error en undo: {e}")
            return None

    def redo_last(self) -> str | None:
        """Re-apply the last undone change."""
        redos = sorted(self._redo_dir.glob("*"))
        if not redos:
            return None

        last_redo = redos[-1]
        name = last_redo.name
        if "_" not in name:
            logger.warning("Redo con nombre inválido (sin '_'): %s", name)
            return None
        original_path = _decode_path(name.split("_", 1)[1])

        try:
            current = self._resolve(original_path)
            current.write_text(last_redo.read_text(encoding="utf-8"))
            last_redo.unlink()
            logger.info(f"Redo aplicado: {original_path}")
            return original_path
        except Exception as e:
            logger.error(f"Error en redo: {e}")
            return None

    def list_undo_stack(self) -> list[str]:
        """Lista los backups disponibles para undo."""
        backups = sorted(self._undo_dir.glob("*"))
        result = []
        for b in backups:
            name = b.name
            original = _decode_path(name.split("_", 1)[1]) if "_" in name else name
            ts = int(name.split("_")[0]) / 1000 if name[0].isdigit() else 0
            result.append(f"{original} ({time.ctime(ts)})")
        return result

    def _resolve(self, file_path: str) -> Path:
        base = paths.memory_dir(self.workspace)
        if self.project_root:
            base = base / self.project_root
        return base / file_path


# Lazy global instance — created per workspace
import threading as _threading

_trackers: dict[str, ChangeTracker] = {}
_trackers_lock = _threading.Lock()


def get_tracker(workspace: str = "main", project_root: str | None = None) -> ChangeTracker:
    key = f"{workspace}:{project_root or ''}"
    with _trackers_lock:
        if key not in _trackers:
            _trackers[key] = ChangeTracker(workspace, project_root)
        return _trackers[key]
