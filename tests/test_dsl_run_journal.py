"""Journal durable: cierre con scorecard y snapshot parcial crash-safe."""

from __future__ import annotations

import json
from unittest.mock import patch

from orchestration.dsl.run_journal import RunJournal
from orchestration.dsl.run_state import BudgetState, RunState


def _state() -> RunState:
    return RunState(run_id="abc", goal="g", plan=[], budget=BudgetState(1000, 0, 0, 0))


def test_journal_parcial_es_crash_safe_y_el_cierre_lo_reemplaza(tmp_path):
    with patch("orchestration.dsl.run_journal.paths.memory_dir", return_value=tmp_path):
        journal = RunJournal("main")
        target = journal.write(_state(), None, partial=True, step=3)
        data = json.loads(target.read_text(encoding="utf-8"))
        assert data["partial"] is True
        assert data["step"] == 3
        assert data["scorecard"] is None

        journal.write(_state(), {"calidad": "Alta"})
        data = json.loads(target.read_text(encoding="utf-8"))
        assert "partial" not in data
        assert data["scorecard"]["calidad"] == "Alta"


def test_journal_sin_run_id_no_escribe(tmp_path):
    with patch("orchestration.dsl.run_journal.paths.memory_dir", return_value=tmp_path):
        assert RunJournal("main").write(RunState(run_id="", goal="g"), {}) is None
