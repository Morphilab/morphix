# tests/test_trajectory_compressor.py — muestreo, dry-run y presupuestos
"""Contratos del compresor:
- muestreo determinista con seed;
- dry-run no escribe nada;
- el prompt NUNCA se trunca; response/reasoning sí, con marcador;
- target_max_tokens respeta el presupuesto con tokenizador inyectable.
"""

import json
from pathlib import Path

import pytest

from research.trajectory_compressor import (
    TRUNCATED_MARKER,
    CompressionConfig,
    compress_entry,
    compress_file,
    load_jsonl,
    sample_entries,
    write_jsonl,
)


def _entry(i: int, response: str = "ok", reasoning: str = "") -> dict:
    return {
        "prompt_index": i,
        "prompt": f"prompt {i}",
        "status": "completed",
        "response": response,
        "iterations": 1,
        "meta": {"platform": "batch", "run_name": "t"},
        **({"reasoning": reasoning} if reasoning else {}),
    }


def _write(tmp_path: Path, entries: list[dict]) -> Path:
    p = tmp_path / "traj.jsonl"
    write_jsonl(p, entries)
    return p


def test_sampling_deterministic_with_seed():
    entries = [_entry(i) for i in range(100)]
    a = sample_entries(entries, 10.0, seed=7)
    b = sample_entries(entries, 10.0, seed=7)
    c = sample_entries(entries, 10.0, seed=8)
    assert [e["prompt_index"] for e in a] == [e["prompt_index"] for e in b]
    assert len(a) == 10
    assert [e["prompt_index"] for e in c] != [e["prompt_index"] for e in a]


def test_dry_run_writes_nothing(tmp_path):
    src = _write(tmp_path, [_entry(i) for i in range(5)])
    out = tmp_path / "out.jsonl"
    summary = compress_file(src, out, CompressionConfig(dry_run=True, sample_percent=50.0))
    assert summary["dry_run"] is True and summary["compressed"] > 0
    assert not out.exists()


def test_prompt_never_truncated_response_capped_with_marker():
    huge = "x" * 10_000
    e = _entry(0, response=huge, reasoning=huge)
    out = compress_entry(e, CompressionConfig(max_chars_response=100, max_chars_reasoning=50))
    assert out["prompt"] == e["prompt"]
    assert len(out["response"]) <= 100 + len(TRUNCATED_MARKER)
    assert out["response"].endswith(TRUNCATED_MARKER)
    assert len(out["reasoning"]) <= 50 + len(TRUNCATED_MARKER)
    assert out["compressed"] is True


def test_target_max_tokens_respects_budget_with_tokenizer():
    def tok(text: str) -> int:  # tokenizador exacto inyectable: 1 token = 2 chars
        return max(1, len(text) // 2)

    prompt = "p" * 200  # 100 tokens
    response = "r" * 2000  # 1000 tokens
    e = _entry(0, response=response)
    e["prompt"] = prompt
    cfg = CompressionConfig(target_max_tokens=200, tokenizer=tok)
    out = compress_entry(e, cfg)
    total = tok(out["prompt"]) + tok(out["response"])
    assert total <= 200, "el presupuesto target_max_tokens debe respetarse"


def test_compress_file_round_trip_and_default_output(tmp_path):
    src = _write(tmp_path, [_entry(i) for i in range(4)])
    summary = compress_file(src, config=CompressionConfig())
    out = Path(summary["output"])
    assert out.name == "traj.compressed.jsonl"
    rows = load_jsonl(out)
    assert [r["prompt_index"] for r in rows] == [0, 1, 2, 3]
    assert all(r["compressed"] is True for r in rows)
    # source intacto
    assert len(load_jsonl(src)) == 4


def test_load_jsonl_skips_corrupt_lines(tmp_path):
    p = tmp_path / "mixed.jsonl"
    p.write_text(
        json.dumps(_entry(0)) + "\nno-es-json\n\n" + json.dumps(_entry(1)) + "\n",
        encoding="utf-8",
    )
    rows = load_jsonl(p)
    assert [r["prompt_index"] for r in rows] == [0, 1]


def test_config_rejects_bad_values():
    with pytest.raises(ValueError):
        CompressionConfig(sample_percent=0)
    with pytest.raises(ValueError):
        CompressionConfig(sample_percent=150)
    with pytest.raises(ValueError):
        CompressionConfig(target_max_tokens=0)
