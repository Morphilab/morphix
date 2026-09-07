"""5.1: índices FK/timestamp + ON DELETE CASCADE (A-XV, A-XVI, M16, B19)

Revision ID: a15xvi_indexes_cascade
Revises: (head — aplicar tras las existentes)
Create Date: 2026-08-21

Notas:
- Índices: ix_message_conversation_id, ix_message_timestamp,
  ix_paused_sessions_conversation_id, ix_paused_sessions_created_at.
- CASCADE: recrea la FK de message y paused_sessions hacia conversation con
  ondelete=CASCADE (batch_alter_table; en SQLite se materializa recreate).
- Guardas inspect-if-not-exists: idempotente sobre BDs ya migradas.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

logger = logging.getLogger(__name__)

revision: str = "a15xvi_idx_cascade"
down_revision: str | None = "p04_baseline"  # tras la baseline P0.4 (raíz previa: ninguna)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES = [
    ("message", "ix_message_conversation_id", ["conversation_id"]),
    ("message", "ix_message_timestamp", ["timestamp"]),
    ("paused_sessions", "ix_paused_sessions_conversation_id", ["conversation_id"]),
    ("paused_sessions", "ix_paused_sessions_created_at", ["created_at"]),
]

FKS = [
    # (tabla, nombre_fk_nuevo, columna, tabla_ref)
    ("message", "fk_message_conversation_cascade", "conversation_id", "conversation"),
    (
        "paused_sessions",
        "fk_paused_sessions_conversation_cascade",
        "conversation_id",
        "conversation",
    ),
]


def _existing_index_names(insp, table: str) -> set[str]:
    try:
        return {ix["name"] for ix in insp.get_indexes(table)}
    except Exception:
        return set()


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    for table, ix_name, cols in INDEXES:
        if table not in insp.get_table_names():
            logger.warning("Tabla %s no existe — omitiendo índice %s", table, ix_name)
            continue
        if ix_name in _existing_index_names(insp, table):
            continue
        op.create_index(ix_name, table, cols)
        logger.info("Índice creado: %s", ix_name)

    is_sqlite = bind.dialect.name == "sqlite"

    for table, fk_name, col, ref in FKS:
        if table not in insp.get_table_names():
            continue

        existing = [
            fk
            for fk in insp.get_foreign_keys(table)
            if fk.get("referred_table") == ref and col in (fk.get("constrained_columns") or [])
        ]
        already_cascade = any(
            (fk.get("options") or {}).get("ondelete") == "CASCADE" for fk in existing
        )
        if already_cascade:
            continue

        old_names = [fk["name"] for fk in existing if fk.get("name")]

        with op.batch_alter_table(table) as batch:
            for old in old_names:
                batch.drop_constraint(old, type_="foreignkey")
            if not is_sqlite or old_names:
                batch.create_foreign_key(fk_name, ref, [col], ["id"], ondelete="CASCADE")
        logger.info("FK %s.%s recreada con ON DELETE CASCADE", table, col)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    for table, ix_name, _cols in INDEXES:
        if ix_name in _existing_index_names(insp, table):
            op.drop_index(ix_name, table_name=table)

    for table, fk_name, col, ref in FKS:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(fk_name, type_="foreignkey")
            batch.create_foreign_key(f"{table}_{col}_fkey", ref, [col], ["id"])
