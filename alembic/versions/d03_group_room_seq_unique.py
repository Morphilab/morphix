"""Índice único (room_id, seq) en group_messages — paridad con el modelo.

Revision ID: d03_group_room_seq_unique
Revises: c02_routine_resilience
Create Date: 2026-10-18

- ``group_messages`` declara ``seq`` único por sala en el modelo
  (``core/models.py``: ``Index("uq_group_messages_room_seq", "room_id",
  "seq", unique=True)``), pero la cadena de migraciones solo creó índices
  normales (b01): los schemas migrados NO tenían el índice único y la
  carrera ``max(seq)+1`` de ``post_message`` producía duplicados
  silenciosos. Los schemas frescos (create_all) ya lo tienen desde el modelo.
- Reparación previa de duplicados conservando el MENOR id por (room_id, seq)
  (el original — espejo del criterio de c02 para conversaciones '⏰').
- ``post_message`` toma ``pg_advisory_xact_lock`` por sala y reintenta ante
  ``IntegrityError``: el índice es la frontera del invariante, el lock
  serializa escritores y el retry hace converger a los no cooperantes.

Reglas respetadas: baseline p04_baseline NO se edita; delta aditivo.
Paridad con create_all cubierta por el modelo (test_alembic_baseline).
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

logger = logging.getLogger(__name__)

revision: str = "d03_group_room_seq_unique"
down_revision: str | None = "c02_routine_resilience"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "uq_group_messages_room_seq"


def _existing_index_names(insp: sa.Inspector, table: str) -> set[str]:
    try:
        return {ix["name"] for ix in insp.get_indexes(table)}
    except Exception:
        return set()


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    existing = _existing_index_names(insp, "group_messages")
    if INDEX_NAME in existing:
        logger.info("%s ya existe en group_messages — nada que hacer", INDEX_NAME)
        return
    op.execute(
        "DELETE FROM group_messages gm USING group_messages gm2 "
        "WHERE gm.room_id = gm2.room_id AND gm.seq = gm2.seq AND gm.id > gm2.id"
    )
    op.create_index(INDEX_NAME, "group_messages", ["room_id", "seq"], unique=True)
    logger.info("%s creado en group_messages (dedup previo de (room_id, seq))", INDEX_NAME)


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if INDEX_NAME in _existing_index_names(insp, "group_messages"):
        op.drop_index(INDEX_NAME, table_name="group_messages")
