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
# familias completas GitHub (ghp/gho/ghu/ghs/ghr/github_pat) y Slack
# (xoxb/p/a/s/r/e); cuerpo ≥14 (frontera testada, tokens reales son más largos).
_CREDENTIAL_RAW_TOKEN = re.compile(
    r"\b(?:sk|gh[A-Za-z]|github_pat|xox[a-z]{1,2})[-_][A-Za-z0-9_-]{14,}\b"
)
# familias sin prefijo de proveedor reconocible.
# JWT (3 segmentos base64url separados por punto), access key de AWS,
# API key de Google y bearer "desnudo" (sin header Authorization: delante).
_CREDENTIAL_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_CREDENTIAL_AWS_ACCESS_KEY = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_CREDENTIAL_GOOGLE_API_KEY = re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")
_CREDENTIAL_BARE_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}\b")


def _redact_key_value(match: re.Match) -> str:
    s = match.group(0)
    sep = "=" if "=" in s else ":"
    key = s.split(sep)[0]
    return f"{key}{sep}***"


def redact_credentials(text: str) -> str:
    """Reemplaza credenciales embebidas por ***."""
    text = _CREDENTIAL_RAW_TOKEN.sub("***", text)
    text = _CREDENTIAL_JWT.sub("***", text)
    text = _CREDENTIAL_AWS_ACCESS_KEY.sub("***", text)
    text = _CREDENTIAL_GOOGLE_API_KEY.sub("***", text)
    text = _CREDENTIAL_BARE_BEARER.sub("Bearer ***", text)
    text = _CREDENTIAL_KEY_VALUE.sub(_redact_key_value, text)
    text = _CREDENTIAL_URL_USERINFO.sub(r"\1***@", text)
    return text


class RedactingFormatter(logging.Formatter):
    """Formatter de logging general que redacta credenciales en cada línea.

    audit.jsonl redacta vía redact_credentials(); logs/morphix.log pasa por
    este formatter. Los patrones son re.compile a nivel de módulo — un patrón
    inválido rompe el import (flag-al-import), nunca falla en silencio en
    runtime."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_credentials(super().format(record))


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
        # rotación best-effort — >5MB → .jsonl.1 (precedente purga
        try:
            if AUDIT_FILE.exists() and AUDIT_FILE.stat().st_size > 5 * 1024 * 1024:
                AUDIT_FILE.replace(AUDIT_FILE.with_suffix(AUDIT_FILE.suffix + ".1"))
        except Exception:
            logger.debug("Rotación de audit.jsonl falló", exc_info=True)

        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"No se pudo escribir audit log: {e}")


def get_recent_operations(limit: int = 50) -> list[dict]:
    """Lee las últimas N operaciones del audit log.

    Lectura de cola acotada — lee por bloques desde el final en vez
    de cargar el archivo completo.
    """
    _ensure_audit_dir()
    if not AUDIT_FILE.exists():
        return []
    try:

        size = AUDIT_FILE.stat().st_size
        chunk_size = max(64 * 1024, limit * 256)
        pos = max(0, size - chunk_size)
        with open(AUDIT_FILE, "rb") as f:
            f.seek(pos)
            tail = f.read().decode("utf-8", errors="replace")
        lines = [ln for ln in tail.splitlines() if ln.strip()]
        if pos > 0 and len(lines) <= limit:
            # Cola insuficiente — ampliar hacia atrás una vez más
            pos2 = max(0, pos - chunk_size * 4)
            with open(AUDIT_FILE, "rb") as f:
                f.seek(pos2)
                tail = f.read().decode("utf-8", errors="replace")
            lines = [ln for ln in tail.splitlines() if ln.strip()]
        entries: list[dict] = []
        for ln in lines[-limit:]:
            try:
                entries.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        return entries
    except Exception:
        return []
