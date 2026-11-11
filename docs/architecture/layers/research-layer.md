# Research Layer

The `research/` package is the evaluation harness: it runs batches of prompts against the
system and post-processes the resulting trajectories. It is **standalone by design** — nothing
in the product calls into it; it has no GUI wiring and runs headless. Its only contract is with
its own tests and the datasets you point it at.

## batch_runner.py — `BatchRunner`

Executes a dataset of prompts and writes trajectory JSONL files.

- **`platform="batch"`**: a batch run is its own execution surface — batch results are tagged
  so they can be told apart from interactive runs.
- **Resumable exactly-once**: `checkpoint.json` is saved per batch with the processed prompt
  indices; on resume, `_reconcile_trajectory()` cross-checks the checkpoint against the
  trajectory file so a crashed batch never duplicates or drops entries.
- **Ephemeral prompts**: an `ephemeral_system_prompt` travels to the executor but is never
  persisted — only the boolean `had_ephemeral_prompt` is recorded.
- **Injectable executor**: the executor is a callable `(prompt, prompt_index, batch_num)` —
  tests inject fakes; no network is required.

## trajectory_compressor.py

Compresses and samples JSONL trajectories for training/evaluation:

- **Deterministic sampling**: `sample_percent` with a fixed `seed` always produces the same
  subset (reproducible evals).
- **Budget-aware compression**: entries are truncated to fit a token budget with per-field
  caps, keeping the trajectory readable.

## Interfaces

| Symbol | Purpose |
|--------|---------|
| `BatchRunner(...)` | Drive a dataset batch with checkpoint/resume |
| `BatchStats` / `BatchStats.to_dict()` | Aggregate pass/fail/skip stats per run |
| `make_default_executor(...)` | Default executor wiring |
| `CompressionConfig` | Token budget + truncation caps |
| `sample_entries(...)` / `compress_file(...)` | Sampling and compression entry points |

Tests: `tests/test_batch_runner.py`, `tests/test_trajectory_compressor.py`.
