"""P0.4: baseline de tablas base — deploy limpio vía `alembic upgrade head`.

Revision ID: p04_baseline
Revises: (raíz del historial)
Create Date: 2026-08-25

Notas:
- DDL CONGELADO generado hoy desde SQLModel.metadata (mismo generador que
  usa create_all en startup_db) — paridad estructural por construcción.
  NO editar a mano: los deltas futuros entran como migraciones nuevas.
- Orden topológico por dependencias FK; ON DELETE CASCADE ya presente en
  message/paused_sessions (los índices ix_* los añade a15xvi después).
- El schema destino lo asegura alembic/env.py antes de ejecutar migraciones
  (fix del gotcha 'no schema has been selected' en BD vírgenes).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "p04_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_DDL: list[str] = [
    """CREATE TABLE blackboard_entries (
\tid SERIAL NOT NULL,
\tsession_id VARCHAR NOT NULL,
\tphase VARCHAR NOT NULL,
\tkey VARCHAR NOT NULL,
\tvalue TEXT,
\tcreated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
\tPRIMARY KEY (id)
)""",
    """CREATE TABLE "user" (
\tid SERIAL NOT NULL,
\tusername VARCHAR NOT NULL,
\tpassword_hash VARCHAR NOT NULL,
\tPRIMARY KEY (id),
\tUNIQUE (username)
)""",
    """CREATE TABLE workflow (
\tid SERIAL NOT NULL,
\tquery VARCHAR NOT NULL,
\tsubtasks VARCHAR NOT NULL,
\tstatus VARCHAR NOT NULL,
\tscorecard VARCHAR,
\tcreated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
\tPRIMARY KEY (id)
)""",
    """CREATE TABLE conversation (
\tid SERIAL NOT NULL,
\ttitle VARCHAR NOT NULL,
\tcreated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
\ttags VARCHAR,
\tworkflow_id INTEGER,
\tPRIMARY KEY (id),
\tFOREIGN KEY(workflow_id) REFERENCES workflow (id)
)""",
    """CREATE TABLE message (
\tid SERIAL NOT NULL,
\tconversation_id INTEGER NOT NULL,
\trole VARCHAR NOT NULL,
\tcontent VARCHAR NOT NULL,
\ttimestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL,
\tembedding BYTEA,
\tembedding_fp VARCHAR(16),
\tPRIMARY KEY (id),
\tFOREIGN KEY(conversation_id) REFERENCES conversation (id) ON DELETE CASCADE
)""",
    """CREATE TABLE paused_sessions (
\tid SERIAL NOT NULL,
\tconversation_id INTEGER,
\tclarification_question VARCHAR NOT NULL,
\tclarification_options VARCHAR,
\tpaused_state VARCHAR NOT NULL,
\tclarification_answer VARCHAR,
\tcreated_at TIMESTAMP WITHOUT TIME ZONE,
\tresolved_at TIMESTAMP WITHOUT TIME ZONE,
\tPRIMARY KEY (id),
\tFOREIGN KEY(conversation_id) REFERENCES conversation (id) ON DELETE CASCADE
)""",
]


def upgrade() -> None:
    for ddl in _TABLE_DDL:
        op.execute(ddl)


def downgrade() -> None:
    # Orden inverso al topológico; las FKs cascada no requieren drops previos.
    for ddl in reversed(_TABLE_DDL):
        table = ddl.split("CREATE TABLE ", 1)[1].split(" (", 1)[0]
        op.execute(f"DROP TABLE IF EXISTS {table}")
