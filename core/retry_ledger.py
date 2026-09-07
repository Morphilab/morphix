# core/retry_ledger.py
"""Retry Ledger — contador de reintentos LLM persistido en disco.

Adaptación del retry durable de deepseek-harness: el contador de intentos
vive en un archivo JSON (no en memoria), así que un crash/restart NO reinicia
el presupuesto — un fallo determinista no puede volver a intentar desde cero
en bucle infinito.

Clave del ledger: ``role + huella SHA1 del request`` (provider-agnóstico, el
fallback a Ollama es una ruta aparte). Toda escritura es atómica y fail-soft:
un problema de disco jamás rompe la llamada LLM.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# TTL de entradas: un reintento "viejo" (día anterior) no debe condenar una
# petición nueva con contenido idéntico tras horas (fail-safe anti falso+).
_DEFAULT_TTL_SECONDS = 24 * 3600


class RetryLedger:
    """Ledger duradero de reintentos. Instanciable; path inyectable para tests."""

    def __init__(self, path: Path, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
        self.path = Path(path)
        self.ttl_seconds = ttl_seconds
        self._lock = threading.RLock()

    # ── Helpers de clase ────────────────────────────────────────────────

    @staticmethod
    def key_for(role: str, messages: list) -> str:
        """Huella estable del request: rol + SHA1 del transcript serializado."""
        try:
            payload = json.dumps(messages, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            payload = repr(messages)
        digest = hashlib.sha1(payload.encode("utf-8", errors="replace")).hexdigest()[:16]
        return f"{role}:{digest}"

    @classmethod
    def default(cls) -> RetryLedger:
        """Instancia sobre la ruta estándar global (memory/llm_retry_ledger.json)."""
        from core.path_resolver import paths

        return cls(paths.memory_base() / "llm_retry_ledger.json")

    @classmethod
    def for_workspace(cls, workspace: str) -> RetryLedger:
        """Instancia sobre la ruta del workspace (memory/<ws>/llm_retry_ledger.json)."""
        from core.path_resolver import paths

        return cls(paths.memory_dir(workspace) / "llm_retry_ledger.json")

    # ── Operaciones ─────────────────────────────────────────────────────

    def _load(self) -> dict:
        try:
            if not self.path.is_file():
                return {}
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("retry_ledger ilegible (%s): %s — tratado como vacío", self.path, e)
            return {}

    def _save(self, data: dict) -> None:
        """Escritura atómica fail-soft: nunca lanza hacia el caller."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except OSError as e:  # pragma: no cover — disco lleno/permisos
            logger.warning("No se pudo persistir retry_ledger: %s", e)

    def used(self, key: str) -> int:
        """Intentos ya gastados para esta clave (0 si ninguno/expirado)."""
        with self._lock:
            entry = self._load().get(key)
            if not isinstance(entry, dict):
                return 0
            ts = float(entry.get("last_ts", 0))
            if time.time() - ts > self.ttl_seconds:
                return 0  # expirado: cuenta fresca
            return max(0, int(entry.get("count", 0)))

    def bump(self, key: str) -> int:
        """Registra un intento fallido; retorna el total acumulado."""
        with self._lock:
            data = self._load()
            now = time.time()
            entry = data.get(key) or {}
            count = int(entry.get("count", 0)) + 1
            first_ts = float(entry.get("first_ts", now))
            if now - first_ts > self.ttl_seconds:
                first_ts = now
                count = 1
            data[key] = {"count": count, "first_ts": first_ts, "last_ts": now}
            self._save(data)
            logger.info("retry_ledger: %s → %d intentos gastados (persistido)", key, count)
            return count

    def clear(self, key: str) -> None:
        """Éxito: borra la entrada (presupuesto limpio para esta petición)."""
        with self._lock:
            data = self._load()
            if key in data:
                del data[key]
                self._save(data)

    def gc(self) -> int:
        """Purga entradas expiradas; retorna cuántas eliminó."""
        with self._lock:
            data = self._load()
            cutoff = time.time() - self.ttl_seconds
            stale = [
                k
                for k, v in data.items()
                if isinstance(v, dict) and float(v.get("last_ts", 0)) < cutoff
            ]
            for k in stale:
                del data[k]
            if stale:
                self._save(data)
            return len(stale)
