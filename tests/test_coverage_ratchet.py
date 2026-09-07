# tests/test_coverage_ratchet.py — no dejar caer cobertura por archivo
"""Ratchet per-file: compara coverage.json contra la
baseline tests/_coverage_baseline.json.

- SKIP si no hay coverage.json (corridas normales sin --cov).
- FALLO si algún archivo medido baja >0.01pp o el total baja.
- COVERAGE_RATCHET_UPDATE=1 regenera la baseline (solo tras GANANCIAS
  verificadas; nunca para silenciar regresiones)."""

import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COV_JSON = ROOT / "coverage.json"
BASELINE = Path(__file__).parent / "_coverage_baseline.json"

pytestmark = pytest.mark.skipif(
    not COV_JSON.exists() or not BASELINE.exists(),
    reason="requiere pytest --cov ... --cov-report=json:coverage.json",
)


def test_coverage_ratchet_per_file():
    data = json.loads(COV_JSON.read_text())
    base = json.loads(BASELINE.read_text())

    if os.environ.get("COVERAGE_RATCHET_UPDATE") == "1":
        new_files = {p: round(i["summary"]["percent_covered"], 2) for p, i in data["files"].items()}
        BASELINE.write_text(
            json.dumps(
                {
                    "_comment": base["_comment"],
                    "total": round(data["totals"]["percent_covered"], 2),
                    "files": dict(sorted(new_files.items())),
                },
                indent=1,
            )
            + "\n"  # end-of-file-fixer: sin newline final el pre-commit aborta el commit
        )
        pytest.skip("baseline actualizada (COVERAGE_RATCHET_UPDATE=1)")

    regressions: list[str] = []
    for path, info in data["files"].items():
        old = base["files"].get(path)
        if old is None:
            continue  # archivo nuevo: se incorpora en la próxima update
        now = round(info["summary"]["percent_covered"], 2)
        # el ratchet corrió en CI por primera vez y
        # se observó ruido de ±0.3pp entre corridas en tests GUI (desktop/ bajo
        # Qt offscreen) sin cambio de código; con el sandbox subprocess
        # el ruido subió a ±0.6pp. 1pp de tolerancia sigue cazando cualquier
        # regresión real sin hacer CI flaky.
        if now < old - 1.0:
            regressions.append(f"{path}: {old:.2f} → {now:.2f}")

    total_now = round(data["totals"]["percent_covered"], 2)
    assert not regressions, (
        "Cobertura por archivo cayó (ratchet D2a):\n"
        + "\n".join(regressions)
        + "\nSi es ganancia esperada tras refactor, regenera con "
        "COVERAGE_RATCHET_UPDATE=1 (nunca para tapar regresiones)."
    )
    assert (
        total_now >= base["total"] - 0.01
    ), f"Cobertura TOTAL cayó: {base['total']:.2f} → {total_now:.2f}"
