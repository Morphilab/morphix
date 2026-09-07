"""H7a: columna Message.embedding (bytes float32 LE del contenido)

Revision ID: h7a_msg_embedding
Revises: a15xvi_idx_cascade
Create Date: 2026-08-24

Notas:
- Embedding persistido del contenido del mensaje — evita re-embeddeo por
  query en el cold-path de semantic_search.
- Guarda inspect-if-column-exists: idempotente sobre BDs donde create_all
  ya materializó la columna (startup_db tras el cambio de modelo).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "h7a_msg_embedding"
down_revision: str | None = "a15xvi_idx_cascade"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(insp, table, column):
    try:
        return any(c["name"] == column for c in insp.get_columns(table))
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "message" not in insp.get_table_names():
        # Tabla aún no existe (BD fresca sin startup_db): create_all la creará
        # con la columna; nada que hacer aquí.
        return
    if _has_column(insp, "message", "embedding"):
        return
    op.add_column("message", sa.Column("embedding", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "message" not in insp.get_table_names():
        return
    if not _has_column(insp, "message", "embedding"):
        return
    op.drop_column("message", "embedding")
