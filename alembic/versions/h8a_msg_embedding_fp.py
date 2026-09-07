"""C2a: columna Message.embedding_fp (fingerprint del backend de embeddings)

Revision ID: h8a_msg_embedding_fp
Revises: h7a_msg_embedding
Create Date: 2026-08-25

Un cambio de familia con la misma dimensión mezclaría escalas
silenciosamente; fp distinto ⇒ vector tratado como ausente (re-encodeo
lazy en semantic_search). Guarda inspect-if-column-exists: idempotente.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "h8a_msg_embedding_fp"
down_revision: str | None = "h7a_msg_embedding"
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
        return  # BD virgen: p04_baseline ya trae la columna
    if not _has_column(insp, "message", "embedding_fp"):
        op.add_column("message", sa.Column("embedding_fp", sa.String(16), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "message" in insp.get_table_names() and _has_column(insp, "message", "embedding_fp"):
        op.drop_column("message", "embedding_fp")
