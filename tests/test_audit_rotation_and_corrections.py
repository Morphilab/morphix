"""Rotación de audit.jsonl y persistencia de correcciones con contenido real."""

import hashlib
from unittest.mock import AsyncMock, patch

import pytest

from agents import audit as audit_mod


def test_log_operation_rotates_over_threshold(tmp_path, monkeypatch):
    """audit.jsonl >5MB se rota a .jsonl.1 antes de append."""
    big = tmp_path / "audit.jsonl"
    big.write_text("x" * (5 * 1024 * 1024 + 10), encoding="utf-8")

    monkeypatch.setattr(audit_mod, "AUDIT_FILE", big)
    audit_mod.log_operation("test_op", "detalles")

    rotated = tmp_path / "audit.jsonl.1"
    assert rotated.exists(), "no hubo rotación"
    assert big.stat().st_size < 5 * 1024 * 1024
    assert "test_op" in big.read_text(encoding="utf-8")


def test_get_recent_operations_bounded_read(tmp_path, monkeypatch):
    """get_recent_operations no carga el archivo completo para retornar 50."""
    big = tmp_path / "audit.jsonl"
    lines = "\n".join(f'{{"operation": "op{i}"}}' for i in range(20_000))
    big.write_text(lines + "\n", encoding="utf-8")
    monkeypatch.setattr(audit_mod, "AUDIT_FILE", big)

    ops = audit_mod.get_recent_operations(limit=50)
    assert len(ops) == 50
    assert ops[-1]["operation"] == "op19999"


@pytest.mark.asyncio
async def test_save_user_correction_deterministic_key_and_real_content(monkeypatch):
    """Clave estable cross-restart (sha1) — no hash() por-proceso."""
    from core.memory.manager import MemoryManager

    mm = MemoryManager.__new__(MemoryManager)
    mm.active_workspace = "ws"
    mm.documents = []
    import faiss

    mm.index = faiss.IndexIDMap2(faiss.IndexFlatL2(1024))
    mm._ids = {}
    mm._id_to_key = {}
    mm._next_id = 0
    mm._access_log = {}

    captured = {}

    async def fake_write(key, value, validated=False, content_hint=None):
        captured["key"] = key
        captured["value"] = value
        return True

    # HIGIENE: patch.object (asignación directa dejaba `write` mockeado en el
    # SINGLETON global filtrando a todos los tests posteriores).
    from unittest.mock import patch as _patch

    task = "corrige el parser"
    with _patch.object(mm, "write", side_effect=fake_write):
        await mm.save_user_correction(task, "usa regex anclada")

    expected_key = f"correction_{hashlib.sha1(task.encode()).hexdigest()[:8]}"
    assert captured["key"] == expected_key
    assert "regex anclada" in str(captured["value"])


@pytest.mark.asyncio
async def test_aggregator_saves_real_correction_content(tmp_path):
    """El aggregator guarda la SALIDA REAL, no el literal."""
    from orchestration.aggregator import ResultAggregator

    results = {
        0: {"node": 0, "status": "completed", "result": "SALIDA-REAL-123", "files_written": []},
    }

    with (
        patch.object(ResultAggregator, "_conformance_violations", new=AsyncMock(return_value=[])),
        patch("core.memory.manager.memory") as mock_mem,
        patch("orchestration.aggregator.settings") as mock_settings,
    ):
        mock_settings.active_workspace = "main"
        mock_mem.save_user_correction = AsyncMock()
        await ResultAggregator.aggregate_results(
            query="corrige el bug",
            results=results,
            G=None,
            task_analysis={},
            files_written=["a.py"],
            project_root=str(tmp_path),
            workspace="main",
        )

    mock_mem.save_user_correction.assert_awaited_once()
    args = mock_mem.save_user_correction.await_args
    assert "SALIDA-REAL-123" in str(
        args
    ), "se guardó el literal 'corrección guardada' en vez del contenido"
