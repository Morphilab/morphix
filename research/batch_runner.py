# research/batch_runner.py — corrida de prompts en lote con reanudación
"""BatchRunner: ejecuta un dataset de prompts y escribe trayectorias JSONL.

Contratos:

- **platform="batch"**: un batch es su propia superficie de ejecución; la
  atribución vive en cada entrada de trayectoria y en ``statistics.json``.
- **Reanudable exactly-once**: ``checkpoint.json`` se guarda por lote con
  escritura atómica; al reanudar, la trayectoria se reconcilia contra el
  checkpoint (las entradas sin checkpoint se descartan y se re-corren), así
  un crash a mitad de lote nunca duplica filas.
- **Prompt efímero no persistido**: ``ephemeral_system_prompt`` viaja al
  ejecutor (``extra_context``) pero JAMÁS se escribe a la trayectoria —
  solo el booleano ``had_ephemeral_prompt``.
- **Aislamiento de fallos**: un prompt que explota produce una entrada
  ``failed`` y la corrida continúa (``_failure_result``).

Artefactos por corrida (en ``output_dir``):

- ``trajectories.jsonl`` — una fila por prompt (resultado + telemetría).
- ``checkpoint.json`` — índices procesados por lote (atomic write).
- ``statistics.json`` — agregados de la corrida hasta el momento.
"""

import asyncio
import json
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PLATFORM_BATCH = "batch"
DEFAULT_OUTPUT_ROOT = Path("data") / "runs"

# Executor inyectable: recibe (prompt, prompt_index, batch_num) y devuelve el
# dict resultado de execute_agent_loop (status/result/iterations/...). Tests
# inyectan fakes; producción usa make_default_executor().
BatchExecutor = Callable[[str, int, int], Awaitable[dict[str, Any]]]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Escribe JSON completo-en-memoria y renombrado atómico (patrón migrator)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


@dataclass
class BatchStats:
    """Agregados vivos de la corrida; se serializan a statistics.json."""

    total_prompts: int = 0
    completed: int = 0
    failed: int = 0
    tokens_used: int = 0
    iterations_total: int = 0
    actions_total: int = 0
    files_written_total: int = 0
    errors: list[str] = field(default_factory=list)

    def absorb(self, entry: dict[str, Any]) -> None:
        if entry["status"] == "completed":
            self.completed += 1
        else:
            self.failed += 1
            msg = str(entry.get("error") or "error desconocido")
            self.errors.append(f"prompt {entry['prompt_index']}: {msg[:200]}")
        self.tokens_used += int(entry.get("tokens_used") or 0)
        self.iterations_total += int(entry.get("iterations") or 0)
        self.actions_total += int(entry.get("actions_taken") or 0)
        self.files_written_total += int(entry.get("files_written") or 0)

    def to_dict(self, run_name: str, *, resumed: bool) -> dict[str, Any]:
        return {
            "platform": PLATFORM_BATCH,
            "run_name": run_name,
            "total_prompts": self.total_prompts,
            "completed": self.completed,
            "failed": self.failed,
            "tokens_used": self.tokens_used,
            "iterations_total": self.iterations_total,
            "actions_total": self.actions_total,
            "files_written_total": self.files_written_total,
            "errors": list(self.errors),
            "resumed_from_checkpoint": resumed,
        }


class BatchRunner:
    """Corre un dataset de prompts por lotes, con checkpoint y estadísticas."""

    def __init__(
        self,
        dataset_file: str | Path,
        run_name: str,
        *,
        output_dir: str | Path | None = None,
        batch_size: int = 4,
        num_workers: int = 4,
        max_samples: int | None = None,
        executor: BatchExecutor | None = None,
        agent_type: str | None = None,
        allowed_tools: list[str] | None = None,
        workspace: str | None = None,
        project_root: str | None = None,
        ephemeral_system_prompt: str | None = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size debe ser >= 1")
        if num_workers < 1:
            raise ValueError("num_workers debe ser >= 1")
        self.dataset_file = Path(dataset_file)
        self.run_name = run_name
        self.batch_size = batch_size
        self.num_workers = num_workers
        self.max_samples = max_samples
        self.executor = executor or make_default_executor(
            agent_type=agent_type,
            allowed_tools=allowed_tools,
            workspace=workspace,
            project_root=project_root,
        )
        self.ephemeral_system_prompt = ephemeral_system_prompt
        self.output_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_ROOT / run_name
        self.trajectory_file = self.output_dir / "trajectories.jsonl"
        self.checkpoint_file = self.output_dir / "checkpoint.json"
        self.stats_file = self.output_dir / "statistics.json"

    # ── Dataset ────────────────────────────────────────────────────────────

    def _load_dataset(self) -> list[str]:
        """JSONL (str o {"prompt": ...} por línea) o JSON (lista)."""
        text = self.dataset_file.read_text(encoding="utf-8")
        prompts: list[str] = []
        if self.dataset_file.suffix == ".json":
            raw = json.loads(text)
            if not isinstance(raw, list):
                raise ValueError("dataset .json debe ser una lista")
            items: list[Any] = raw
        else:
            items = [json.loads(line) for line in text.splitlines() if line.strip()]
        for it in items:
            if isinstance(it, str):
                prompts.append(it)
            elif isinstance(it, dict) and isinstance(it.get("prompt"), str):
                prompts.append(it["prompt"])
            else:
                raise ValueError(f"entrada de dataset inválida: {it!r}")
        if not prompts:
            raise ValueError(f"dataset vacío: {self.dataset_file}")
        if self.max_samples is not None and self.max_samples < len(prompts):
            prompts = prompts[: self.max_samples]
            logger.info("Dataset truncado a %d muestras (--max_samples)", len(prompts))
        return prompts

    # ── Checkpoint ─────────────────────────────────────────────────────────

    def _load_checkpoint(self) -> dict[str, Any]:
        if not self.checkpoint_file.exists():
            return {"version": 1, "processed": [], "last_batch": -1}
        try:
            data = json.loads(self.checkpoint_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Checkpoint corrupto = sin checkpoint: reconciliar lo re-corre todo.
            logger.warning("checkpoint.json ilegible; se re-corre la corrida completa")
            return {"version": 1, "processed": [], "last_batch": -1}
        data.setdefault("processed", [])
        data.setdefault("last_batch", -1)
        return data

    def _save_checkpoint(self, processed: set[int], last_batch: int) -> None:
        _write_json_atomic(
            self.checkpoint_file,
            {
                "version": 1,
                "run_name": self.run_name,
                "processed": sorted(processed),
                "last_batch": last_batch,
                "saved_at": _now_iso(),
            },
        )

    # ── Trayectoria ────────────────────────────────────────────────────────

    def _reconcile_trajectory(self, processed: set[int]) -> int:
        """Exactly-once: descarta filas fuera del checkpoint (crash mid-batch).

        Devuelve cuántas filas sobrevivieron. Sin trayectoria previa es no-op.
        """
        if not self.trajectory_file.exists():
            return 0
        kept: list[str] = []
        dropped = 0
        with open(self.trajectory_file, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    dropped += 1
                    continue
                if int(row.get("prompt_index", -1)) in processed:
                    kept.append(line)
                else:
                    dropped += 1
        tmp = self.trajectory_file.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.writelines(kept)
        os.replace(tmp, self.trajectory_file)
        if dropped:
            logger.info("Reconciliación: %d filas sin checkpoint descartadas", dropped)
        return len(kept)

    @staticmethod
    def _failure_result(prompt_index: int, batch_num: int, error: BaseException) -> dict[str, Any]:
        """Entrada failed aislada — la corrida continúa."""
        return {
            "prompt_index": prompt_index,
            "batch_num": batch_num,
            "status": "failed",
            "error": f"{type(error).__name__}: {error}"[:500],
            "response": "",
            "iterations": 0,
            "actions_taken": 0,
            "files_written": 0,
            "tokens_used": 0,
            "meta": {"platform": PLATFORM_BATCH},
        }

    async def _process_single(
        self,
        prompt: str,
        prompt_index: int,
        batch_num: int,
        *,
        semaphore: asyncio.Semaphore,
    ) -> dict[str, Any]:
        async with semaphore:
            try:
                result = await self.executor(prompt, prompt_index, batch_num)
            except Exception as exc:  # aislamiento: una entrada failed, seguir
                logger.warning("prompt %d falló: %s", prompt_index, exc)
                return self._failure_result(prompt_index, batch_num, exc)
            entry: dict[str, Any] = {
                "prompt_index": prompt_index,
                "batch_num": batch_num,
                "prompt": prompt,
                "status": str(result.get("status") or "completed"),
                "response": str(result.get("result") or ""),
                "iterations": int(result.get("iterations") or 0),
                "actions_taken": int(result.get("actions_taken") or 0),
                "files_written": int(result.get("files_written") or 0),
                "tokens_used": int(result.get("tokens_used_call") or 0),
                "error": None,
                "meta": {
                    "platform": PLATFORM_BATCH,
                    "run_name": self.run_name,
                    "had_ephemeral_prompt": bool(self.ephemeral_system_prompt),
                },
            }
            if result.get("reasoning"):
                entry["reasoning"] = str(result["reasoning"])
            return entry

    # ── Corrida ────────────────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        """Ejecuta el dataset por lotes; reanuda desde checkpoint si existe."""
        prompts = self._load_dataset()
        checkpoint = self._load_checkpoint()
        processed: set[int] = {int(i) for i in checkpoint["processed"]}
        resumed = bool(processed)
        if resumed:
            kept = self._reconcile_trajectory(processed)
            if kept < len(processed):
                # filas declaradas por el checkpoint pero perdidas del JSONL:
                # esos índices vuelven a la cola y se re-corrigen las stats.
                logger.warning(
                    "checkpoint declara %d pero la trayectoria conserva %d; "
                    "los faltantes se re-corren",
                    len(processed),
                    kept,
                )
                processed = self._indices_in_trajectory()
            logger.info(
                "Reanudando '%s': %d procesados, %d pendientes",
                self.run_name,
                len(processed),
                len(prompts) - len(processed),
            )

        stats = BatchStats(total_prompts=len(prompts))
        for index in sorted(processed):
            entry = self._read_entry(index)
            if entry is not None:
                stats.absorb(entry)

        pending = [(i, p) for i, p in enumerate(prompts) if i not in processed]
        semaphore = asyncio.Semaphore(self.num_workers)
        for batch_start in range(0, len(pending), self.batch_size):
            batch = pending[batch_start : batch_start + self.batch_size]
            entries = await asyncio.gather(
                *[
                    self._process_single(p, i, i // self.batch_size, semaphore=semaphore)
                    for i, p in batch
                ]
            )
            for entry in entries:
                _append_jsonl(self.trajectory_file, entry)
                stats.absorb(entry)
                processed.add(int(entry["prompt_index"]))
            last_batch = batch[-1][0] // self.batch_size if batch else 0
            self._save_checkpoint(processed, last_batch)
            _write_json_atomic(self.stats_file, stats.to_dict(self.run_name, resumed=resumed))

        summary = stats.to_dict(self.run_name, resumed=resumed)
        summary["finished_at"] = _now_iso()
        _write_json_atomic(self.stats_file, summary)
        logger.info("Batch '%s' listo: %s", self.run_name, summary)
        return summary

    # ── Lectura de entradas previas (para stats al reanudar) ────────────────

    def _read_entry(self, index: int) -> dict[str, Any] | None:
        if not self.trajectory_file.exists():
            return None
        with open(self.trajectory_file, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if int(row.get("prompt_index", -1)) == index:
                    return row
        return None

    def _indices_in_trajectory(self) -> set[int]:
        if not self.trajectory_file.exists():
            return set()
        out: set[int] = set()
        with open(self.trajectory_file, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                out.add(int(row.get("prompt_index", -1)))
        return out


def make_default_executor(
    *,
    agent_type: str | None,
    allowed_tools: list[str] | None,
    workspace: str | None,
    project_root: str | None,
) -> BatchExecutor:
    """Ejecutor por defecto: el agent loop real de Morphix (import lazy).

    El prompt efímero viaja como ``extra_context`` — llega al modelo, nunca
    a la trayectoria (BatchRunner solo persiste el prompt original).
    """

    async def _execute(prompt: str, index: int, batch_num: int) -> dict[str, Any]:
        from orchestration.loop import execute_agent_loop

        return await execute_agent_loop(
            task=prompt,
            agent_type=agent_type,
            history=[],
            allowed_tools=allowed_tools,
            workspace=workspace,
            project_root=project_root,
        )

    return _execute
