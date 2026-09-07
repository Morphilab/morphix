"""Resiliencia de rutinas — dead-letter + dedupe.

Revision ID: c02_routine_resilience
Revises: b01_bot_mode
Create Date: 2026-09-02

- Columna ``routines.failure_count`` (fallos consecutivos del scheduler). Al
  alcanzar ROUTINE_DEAD_LETTER (core/bots_routines.py) el re-firing se acota
  a 1 intento/día en lugar de re-ejecutar el prompt completo (con efectos
  secundarios de tools) cada CATCHUP_MIN_S.
- Índice único parcial ``uq_conversation_routine_title`` sobre
  ``conversation(title) WHERE title LIKE '⏰ %'`` — la carrera
  SELECT-then-INSERT de ``_conversation_for_history`` duplicaba
  conversaciones '⏰ <nombre>'. Reparación previa: demove duplicados
  conservando la MÁS VIEJA (la original, con el historial acumulado;
  espejo inverso del criterio de b01, que preservaba el canónico fresco).

Reglas respetadas: baseline p04_baseline NO se edita; delta aditivo.
Paridad con create_all cubierta por el campo del modelo Routine
(test_alembic_baseline compara catálogo completo).
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

logger = logging.getLogger(__name__)

revision: str = "c02_routine_resilience"
down_revision: str | None = "b01_bot_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROUTINE_TITLE_INDEX = "uq_conversation_routine_title"


def _existing_index_names(insp: sa.Inspector, table: str) -> set[str]:
    try:
        return {ix["name"] for ix in insp.get_indexes(table)}
    except Exception:
        return set()


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # ── 1. routines.failure_count (A5) ─────────────────────────────────────
    insp = sa.inspect(bind)
    routine_cols = {c["name"] for c in insp.get_columns("routines")}
    if "failure_count" not in routine_cols:
        op.add_column(
            "routines",
            sa.Column("failure_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        )
        logger.info("routines.failure_count añadida")
    else:
        logger.info("routines.failure_count ya existía — paridad create_all, no se toca")

    if is_sqlite:
        # SQLite: no ALTER ADD CONSTRAINT parcial relevante; el índice parcial
        # con LIKE soportado, pero la reparación usa la misma SQL.
        pass

    # ── 2. Dedupe de conversaciones de rutina (B2) — conservar la más vieja ─
    bind.execute(
        sa.text(
            """
            DELETE FROM conversation
            WHERE title LIKE '⏰ %'
              AND id NOT IN (
                SELECT MIN(id) FROM conversation WHERE title LIKE '⏰ %' GROUP BY title
              )
            """
        )
    )

    # ── 3. Índice único parcial (B2) ────────────────────────────────────────
    insp = sa.inspect(bind)
    if ROUTINE_TITLE_INDEX not in _existing_index_names(insp, "conversation"):
        op.create_index(
            ROUTINE_TITLE_INDEX,
            "conversation",
            ["title"],
            unique=True,
            postgresql_where=sa.text("title LIKE '⏰ %'"),
            sqlite_where=sa.text("title LIKE '⏰ %'"),
        )
        logger.info("Índice %s creado", ROUTINE_TITLE_INDEX)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if ROUTINE_TITLE_INDEX in _existing_index_names(insp, "conversation"):
        op.drop_index(ROUTINE_TITLE_INDEX, table_name="conversation")
    insp = sa.inspect(bind)
    if "failure_count" in {c["name"] for c in insp.get_columns("routines")}:
        op.drop_column("routines", "failure_count")
