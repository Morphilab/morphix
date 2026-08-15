"""Audit Log — registro de operaciones sensibles.

Registra: bash commands, file deletions, git force pushes.
Almacenamiento en archivo JSON lines para simplicidad.
"""

import json
import logging
import re
from datetime import UTC, datetime

from core.path_resolver import paths

logger = logging.getLogger(__name__)

AUDIT_FILE = paths.memory_base() / "logs" / "audit.jsonl"

# Redacción de credenciales antes de persistir en disco (los comandos pueden
# contener tokens, Authorization headers, passwords en URLs, etc.)
_CREDENTIAL_KEY_VALUE = re.compile(
    r"(?i)(authorization|bearer|token|api[_-]?key|password|passwd|secret)"
    r"\s*[=:]\s*[^\s\"']+(?:\s+[^\s\"']+)?"
)
_CREDENTIAL_URL_USERINFO = re.compile(r"(?i)(://)[^/@\s]+@")


def _redact_key_value(match: re.Match) -> str:
    s = match.group(0)
    sep = "=" if "=" in s else ":"
    key = s.split(sep)[0]
    return f"{key}{sep}***"


def redact_credentials(text: str) -> str:
    """Reemplaza credenciales embebidas por ***."""
    text = _CREDENTIAL_KEY_VALUE.sub(_redact_key_value, text)
    text = _CREDENTIAL_URL_USERINFO.sub(r"\1***@", text)
    return text


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
        "details": redact_credentials(details)[:500],
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
