"""Audit Log — registro de operaciones sensibles.

Registra: bash commands, file deletions, git force pushes.
Almacenamiento en archivo JSON lines para simplicidad.
"""

import json
import logging
from datetime import UTC, datetime

from core.path_resolver import paths

logger = logging.getLogger(__name__)

AUDIT_FILE = paths.memory_base() / "logs" / "audit.jsonl"


def _ensure_audit_dir() -> None:
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)


def log_operation(
    operation: str,
    details: str = "",
    user: str = "morphix",
    success: bool = True,
) -> None:
    """Registra una operación en el audit log."""
    _ensure_audit_dir()
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "operation": operation,
        "details": details[:500],
        "user": user,
        "success": success,
    }
    try:
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"No se pudo escribir audit log: {e}")


def get_recent_operations(limit: int = 50) -> list[dict]:
    """Lee las últimas N operaciones del audit log."""
    _ensure_audit_dir()
    if not AUDIT_FILE.exists():
        return []
    entries = []
    try:
        with open(AUDIT_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except Exception:
        return []
    return entries[-limit:]
