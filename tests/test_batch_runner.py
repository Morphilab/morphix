# tests/test_batch_runner.py — contratos del BatchRunner
"""Sin red y sin BD: el ejecutor es un fake inyectado (doctrina del repo).

Contratos:
- 2 batches con corte a mitad y reanudación → trayectoria sin duplicados
  y stats idénticas a la corrida limpia.
- platform="batch" atribuido en cada entrada y en statistics.json.
- Prompt efímero llega al ejecutor, JAMÁS a la trayectoria.
- Aislamiento de fallos: un prompt roto produce entrada failed y sigue.
"""

import json
from pathlib import Path

import pytest

from research.batch_runner import PLATFORM_BATCH, BatchRunner


def _write_dataset(tmp_path: Path, prompts: list[str]) -> Path:
    ds = tmp_path / "dataset.jsonl"
    ds.write_text("\n".join(json.dumps(p) for p in prompts) + "\n", encoding="utf-8")
    return ds


def _fake_executor(results: dict[int, dict] | None = None, calls: list[tuple] | None = None):
    """Executor determinista: r{index} + tokens index+1 (stats verificables)."""
    results = results or {}

    async def _execute(prompt: str, index: int, batch_num: int) -> dict:
        if calls is not None:
            calls.append((prompt, index, batch_num))
        if index in results:
            return results[index]
        return {
            "status": "completed",
            "result": f"r{index}",
            "iterations": 1,
            "actions_taken": index % 3,
            "files_written": 0,
            "tokens_used_call": index + 1,
        }

    return _execute


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


async def test_two_batches_full_run_stats_and_platform(tmp_path):
    ds = _write_dataset(tmp_path, [f"prompt {i}" for i in range(6)])
    out = tmp_path / "run"
    runner = BatchRunner(
        ds,
        "run-x",
        output_dir=out,
        batch_size=3,
        num_workers=2,
        executor=_fake_executor(),
    )
    summary = await runner.run()

    rows = _read_jsonl(out / "trajectories.jsonl")
    assert len(rows) == 6
    assert {r["prompt_index"] for r in rows} == set(range(6))
    assert all(r["meta"]["platform"] == PLATFORM_BATCH for r in rows)
    assert summary["platform"] == PLATFORM_BATCH
    assert summary["completed"] == 6 and summary["failed"] == 0
    assert summary["tokens_used"] == sum(range(1, 7))
    assert summary["actions_total"] == sum(i % 3 for i in range(6))
    assert summary["resumed_from_checkpoint"] is False


async def test_resume_after_crash_yields_identical_stats(tmp_path):
    """Corte tras el batch 0 (checkpoint parcial) → reanudar → stats idénticas."""
    ds = _write_dataset(tmp_path, [f"prompt {i}" for i in range(6)])
    out = tmp_path / "run"

    # Corrida 1: crash al guardar el checkpoint del SEGUNDO lote — las filas
    # del batch 1 ya están en el JSONL pero el checkpoint solo conoce el 0.
    crashed = BatchRunner(
        ds,
        "run-x",
        output_dir=out,
        batch_size=3,
        num_workers=2,
        executor=_fake_executor(),
    )
    original_save = crashed._save_checkpoint
    state = {"calls": 0}

    def flaky_save(processed, last_batch):
        state["calls"] += 1
        if state["calls"] >= 2:
            raise RuntimeError("crash simulado tras el segundo lote")
        return original_save(processed, last_batch)

    crashed._save_checkpoint = flaky_save  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await crashed.run()

    rows = _read_jsonl(out / "trajectories.jsonl")
    assert len(rows) == 6  # todo escrito, pero el checkpoint solo tiene 3
    ckpt = json.loads((out / "checkpoint.json").read_text(encoding="utf-8"))
    assert len(ckpt["processed"]) == 3

    # Corrida 2: reanudación — reconcilia el JSONL contra el checkpoint,
    # re-corrige los 3 no confirmados y termina exact-once.
    resumed = BatchRunner(
        ds,
        "run-x",
        output_dir=out,
        batch_size=3,
        num_workers=2,
        executor=_fake_executor(),
    )
    summary = await resumed.run()

    rows = _read_jsonl(out / "trajectories.jsonl")
    assert len(rows) == 6, "la reanudación no debe duplicar filas"
    assert {r["prompt_index"] for r in rows} == set(range(6))
    clean = BatchRunner(
        ds,
        "run-clean",
        output_dir=tmp_path / "clean",
        batch_size=3,
        num_workers=2,
        executor=_fake_executor(),
    )
    clean_summary = await clean.run()
    for key in ("completed", "failed", "tokens_used", "iterations_total", "actions_total"):
        assert summary[key] == clean_summary[key], key
    assert summary["resumed_from_checkpoint"] is True


async def test_ephemeral_prompt_reaches_executor_never_trajectory(tmp_path):
    ds = _write_dataset(tmp_path, ["p1", "p2"])
    out = tmp_path / "run"
    calls: list[tuple] = []
    secret = "RUBRICA-SECRETA-1234"

    async def executor(prompt: str, index: int, batch_num: int) -> dict:
        # El efímero NO llega por el prompt (viaja como extra_context del
        # ejecutor real); aquí registramos la tupla para verificar la llamada.
        calls.append((prompt, index))
        return {
            "status": "completed",
            "result": f"r{index}",
            "iterations": 1,
            "actions_taken": 0,
            "files_written": 0,
            "tokens_used_call": 1,
        }

    runner = BatchRunner(
        ds,
        "run-eph",
        output_dir=out,
        batch_size=2,
        num_workers=1,
        executor=executor,
        ephemeral_system_prompt=secret,
    )
    await runner.run()

    rows = _read_jsonl(out / "trajectories.jsonl")
    raw = (out / "trajectories.jsonl").read_text(encoding="utf-8")
    assert secret not in raw, "el prompt efímero jamás se persiste"
    assert all(r["meta"]["had_ephemeral_prompt"] is True for r in rows)
    assert {c[1] for c in calls} == {0, 1}


async def test_failure_isolation_continues_run(tmp_path):
    ds = _write_dataset(tmp_path, [f"p{i}" for i in range(4)])

    async def executor(prompt: str, index: int, batch_num: int) -> dict:
        if index == 2:
            raise ValueError("boom")
        return {
            "status": "completed",
            "result": f"r{index}",
            "iterations": 1,
            "actions_taken": 0,
            "files_written": 0,
            "tokens_used_call": 1,
        }

    out = tmp_path / "run"
    summary = await BatchRunner(
        ds,
        "run-fail",
        output_dir=out,
        batch_size=2,
        num_workers=2,
        executor=executor,
    ).run()

    rows = _read_jsonl(out / "trajectories.jsonl")
    assert len(rows) == 4
    failed = [r for r in rows if r["status"] == "failed"]
    assert len(failed) == 1 and failed[0]["prompt_index"] == 2
    assert "boom" in failed[0]["error"]
    assert summary["completed"] == 3 and summary["failed"] == 1
    assert summary["errors"] and "prompt 2" in summary["errors"][0]


async def test_invalid_inputs_rejected(tmp_path):
    with pytest.raises(ValueError):
        BatchRunner(_write_dataset(tmp_path, ["a"]), "x", batch_size=0)
    with pytest.raises(ValueError):
        BatchRunner(_write_dataset(tmp_path, ["a"]), "x", num_workers=0)
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        BatchRunner(empty, "x")._load_dataset()
