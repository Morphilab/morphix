# core/transcript.py
"""Transcript event-sourced ligero del turno del agente.

Adaptación del transcript event-sourced de deepseek-harness: cada iteración
del loop registra eventos en un JSONL append-only (``memory/<ws>/transcripts/``).
El registro da tres garantías:

1. **Reconstrucción fina** — el orden de pasos queda registrado (run abierto =
   crash/cancelación, con la iteración exacta donde quedó).
2. **Observabilidad de estabilidad de prefijo** — cada request guarda el hash
   SHA1 de sus mensajes; si el prefijo del turno muta (compresión agresiva),
   se registra ``cache/prefix_broken`` y se avisa.
3. **Diagnóstico post-crash** — replay del JSONL muestra qué paso explotó.

Fail-soft total: ningún fallo de transcript puede romper el loop.
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

_GLOBAL_LOCK = threading.RLock()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _sha1(obj) -> str:
    try:
        payload = json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = repr(obj)
    return hashlib.sha1(payload.encode("utf-8", errors="replace")).hexdigest()[:16]


class TranscriptLog:
    """Log append-only por run. Instanciable; fail-soft en toda operación."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._seq = 0

    # ── Ciclo de vida ───────────────────────────────────────────────────

    @classmethod
    def start(
        cls,
        workspace: str,
        agent_type: str = "",
        task: str = "",
        provider: str = "",
        model: str = "",
    ) -> TranscriptLog:
        """Crea un nuevo log bajo memory/<ws>/transcripts/ y registra run/start."""
        from core.path_resolver import paths

        ts = time.strftime("%Y%m%d-%H%M%S")
        task_tag = _sha1(task)[:8]
        sid = f"{ts}-{agent_type or 'agent'}-{task_tag}.jsonl"
        tdir = paths.memory_dir(workspace) / "transcripts"
        tdir.mkdir(parents=True, exist_ok=True)
        log = cls(tdir / sid)
        log.append(
            "run/start",
            agent_type=agent_type,
            workspace=workspace,
            task_sha1=_sha1(task),
            provider=provider,
            model=model,
        )
        return log

    # ── Escritura ───────────────────────────────────────────────────────

    def append(self, event_type: str, **data) -> int:
        """Registra un evento; retorna seq (-1 si falló, fail-soft)."""
        with _GLOBAL_LOCK:
            self._seq += 1
            line = json.dumps(
                {
                    "seq": self._seq,
                    "ts": _now_iso(),
                    "type": event_type,
                    **data,
                },
                ensure_ascii=False,
                default=str,
            )
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError as e:  # pragma: no cover — disco lleno/permisos
                logger.warning("transcript: no se pudo registrar %s: %s", event_type, e)
                self._seq -= 1
                return -1
            return self._seq

    # ── Pasos del loop con control de prefijo ──────────────────────────

    def step_request(self, iteration: int, messages: list, prev: dict | None) -> dict:
        """Registra el request del step. Retorna el estado previo actualizado.

        Verifica la estabilidad del prefijo contra el estado anterior: si el
        hash de los primeros ``prev['n']`` mensajes cambió durante el MISMO
        turno, algo mutó el inicio del historial → cache/prefix_broken.
        """
        n = len(messages)
        full_sha = _sha1(messages)

        if prev and prev.get("n"):
            prev_n = int(prev["n"])
            head = messages[:prev_n]
            head_sha = _sha1(head) if head else ""
            if head_sha != prev.get("full") and prev_n <= n:
                logger.warning(
                    "Transcript: prefijo del turno mutado en iteración %d "
                    "(%d msgs previas re-hash distintas) — prompt-cache invalidada",
                    iteration,
                    prev_n,
                )
                self.append(
                    "cache/prefix_broken",
                    iteration=iteration,
                    prev_n=prev_n,
                    prev_full=prev["full"],
                    head_sha=head_sha,
                )

        event = {
            "iteration": iteration,
            "n_messages": n,
            "full_sha": full_sha,
        }
        self.append("step/request", **event)
        return {"n": n, "full": full_sha}

    def step_response(self, iteration: int, has_tool_calls: bool, text_len: int) -> None:
        self.append(
            "step/response",
            iteration=iteration,
            has_tool_calls=has_tool_calls,
            text_len=text_len,
        )

    def note(self, event_type: str, **data) -> None:
        """Atajo para eventos informativos (cancelaciones, límites...)."""
        self.append(event_type, **data)


# ── Lectura / diagnóstico ───────────────────────────────────────────────


def transcripts_dir(workspace: str) -> Path:
    from core.path_resolver import paths

    return paths.memory_dir(workspace) / "transcripts"


def read_run(path: str | Path) -> list[dict]:
    """Lee todos los eventos de un run (línea corrupta al final ⇒ ignorada)."""
    events: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    break  # cola rasgada por crash a mitad de escritura
    except OSError:
        pass
    return events


def find_open_runs(workspace: str) -> list[str]:
    """Runs sin marcador terminal — candidatos a revisión tras un crash.

    Terminales considerados: run/end (grácil), run/cancelled (usuario),
    run/early_exit (stall/límites del loop). Un JSONL sin ninguno quedó a
    mitad de turno: crash del proceso o escritura interrumpida.
    """
    closed_types = {"run/end", "run/cancelled", "run/early_exit"}
    opened: list[str] = []
    d = transcripts_dir(workspace)
    if not d.is_dir():
        return opened
    for p in sorted(d.glob("*.jsonl")):
        events = read_run(p)
        if events and not any(e.get("type") in closed_types for e in events):
            opened.append(p.name)
    return opened


def write_atomic_json(path: Path, payload: dict) -> bool:
    """Escritura atómica auxiliar compartida (testkit)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except OSError:  # pragma: no cover
        return False
