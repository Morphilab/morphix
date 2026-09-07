"""Paridad de Message.embedding en schemas legacy de workspace.

create_tables_in_schema usa create_all (sin ALTER sobre tablas preexistentes);
los schemas creados sin la columna quedaban en UndefinedColumn al leerla.
"""

import pytest

from core import database as db


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    # Bot Mode: los probes nuevos de create_tables_in_schema leen conjuntos
    def fetchall(self):
        return self._rows


def _make_fake_engine(sqls: list[str], *, table_exists: bool, column_exists: bool):
    class FakeConn:
        async def execute(self, sql, params=None):
            s = str(sql)
            sqls.append(s)
            if "information_schema.tables" in s:
                return _FakeResult([("1",)] if table_exists else [])
            if "information_schema.columns" in s:
                if not column_exists:
                    return _FakeResult([])
                # Paridad conversation: el módulo espera NOMBRES de columna
                if "column_name" in s and "'conversation'" in s:
                    return _FakeResult(
                        [(n,) for n in ("bot_id", "is_canonical", "is_hidden", "capability_epoch")]
                    )
                return _FakeResult([("1",)])
            return _FakeResult([])

        async def run_sync(self, fn, *a, **k):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class FakeEngine:
        def begin(self):
            return FakeConn()

    return FakeEngine()


@pytest.mark.asyncio
async def test_create_tables_adds_embedding_to_legacy(monkeypatch):
    """Tabla message preexistente sin columna → ALTER idempotente."""
    sqls: list[str] = []
    engine = _make_fake_engine(sqls, table_exists=True, column_exists=False)
    monkeypatch.setattr(db, "_get_async_engine", lambda: engine)

    await db.create_tables_in_schema("legacy_ws")

    assert any("ALTER TABLE message ADD COLUMN embedding" in s for s in sqls)


@pytest.mark.asyncio
async def test_create_tables_skips_alter_when_column_present(monkeypatch):
    """Columna ya presente → ningún ALTER."""
    sqls: list[str] = []
    engine = _make_fake_engine(sqls, table_exists=True, column_exists=True)
    monkeypatch.setattr(db, "_get_async_engine", lambda: engine)

    await db.create_tables_in_schema("modern_ws")

    assert not any("ALTER TABLE" in s for s in sqls)


@pytest.mark.asyncio
async def test_create_tables_skips_alter_when_table_absent(monkeypatch):
    """Tabla ausente → create_all la creó completa; sin ALTER."""
    sqls: list[str] = []
    engine = _make_fake_engine(sqls, table_exists=False, column_exists=False)
    monkeypatch.setattr(db, "_get_async_engine", lambda: engine)

    await db.create_tables_in_schema("fresh_ws")

    assert not any("ALTER TABLE" in s for s in sqls)


@pytest.mark.asyncio
async def test_create_tables_adds_embedding_fp_to_legacy(monkeypatch):
    """Paridad también para la columna fingerprint."""
    sqls: list[str] = []
    engine = _make_fake_engine(sqls, table_exists=True, column_exists=False)
    monkeypatch.setattr(db, "_get_async_engine", lambda: engine)

    await db.create_tables_in_schema("legacy_ws")

    assert any("ALTER TABLE message ADD COLUMN embedding_fp" in s for s in sqls)


@pytest.mark.asyncio
async def test_create_tables_adds_bot_mode_columns_to_legacy(monkeypatch):
    """[A2] Cobertura positiva del repair-block Bot Mode: conversation legacy
    sin columnas bot_id/is_canonical/is_hidden/capability_epoch recibe los
    4 ALTER (bot_id con REFERENCES CASCADE) + el índice único parcial."""
    sqls: list[str] = []
    engine = _make_fake_engine(sqls, table_exists=True, column_exists=False)
    monkeypatch.setattr(db, "_get_async_engine", lambda: engine)

    await db.create_tables_in_schema("legacy_ws")

    assert any("ADD COLUMN bot_id INTEGER" in s and "ON DELETE CASCADE" in s for s in sqls), sqls
    assert any("ADD COLUMN is_canonical" in s for s in sqls)
    assert any("ADD COLUMN is_hidden" in s for s in sqls)
    assert any("ADD COLUMN capability_epoch" in s for s in sqls)
    assert any(
        "CREATE UNIQUE INDEX uq_conversation_bot_chat" in s and "is_canonical IS TRUE" in s
        for s in sqls
    )
