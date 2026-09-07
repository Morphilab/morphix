"""Bot Mode: identidad conversacional por NOMBRE (contrato 08 v2)

Revision ID: b01_bot_mode
Revises: h8a_msg_embedding_fp
Create Date: 2026-08-26

Contratos implementados (spec _reversa_sdd/hermes/bot-mode/08-contrato-clon-morphix-v2.md):
- C1/T02: índice único parcial uq_conversation_bot_chat (bot_id WHERE is_canonical)
  — el registro de ≤1 chat eterno por bot.
- T09/C9: bots = filas por workspace-schema; sin tablas globales de sesiones.
- C10/T12: ui_meta CAS + historial de revisiones que sobrevive al borrado.

Reglas respetadas: baseline p04_baseline NO se edita; este delta es aditivo.
La reparación pre-índice demueve duplicados canónicos conservando el más reciente
(espejo del repair TestSessionTitleIndexRepair de hermes).

NOTA de paridad con create_all: los tipos/columnas de las tablas nuevas se
escribieron espejando el compilado CreateTable(postgresql) del metadata;
test_alembic_baseline::test_upgrade_head_on_virgin_schema_matches_create_all
lo verifica catálogo completo contra un schema gemelo provisionado por create_all.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import ForeignKey

from alembic import op

logger = logging.getLogger(__name__)

revision: str = "b01_bot_mode"
down_revision: str | None = "h8a_msg_embedding_fp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BOT_MODE_INDEX = "uq_conversation_bot_chat"

CONVERSATION_COLS = [
    # (nombre, DDL de columna, igual al modelo core/models.py)
    ("bot_id", "INTEGER"),
    ("is_canonical", "BOOLEAN DEFAULT false NOT NULL"),
    ("is_hidden", "BOOLEAN DEFAULT false NOT NULL"),
    ("capability_epoch", "VARCHAR(12)"),
]


def _existing_index_names(insp, table: str) -> set[str]:
    try:
        return {ix["name"] for ix in insp.get_indexes(table)}
    except Exception:
        return set()


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # ── 1. Tablas nuevas (orden FKs: bots primero) ──────────────────────────
    op.create_table(
        "bots",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("soul_md", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("tool_names", sa.JSON(), nullable=False),
        sa.Column("skill_allowlist", sa.JSON(), nullable=False),
        sa.Column("memory_prefix", sa.String(128), nullable=False),
        sa.Column("ui_meta", sa.JSON(), nullable=False),
        sa.Column("ui_meta_rev", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint("slug", name="uq_bots_slug"),
    )

    op.create_table(
        "bot_meta_history",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("bot_slug", sa.String(64), nullable=False),
        sa.Column("rev", sa.Integer(), nullable=False),
        sa.Column("ui_meta", sa.JSON(), nullable=False),
        sa.Column("tombstone", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_index("ix_bot_meta_history_bot_slug", "bot_meta_history", ["bot_slug"])
    op.create_index("ix_bot_meta_history_recorded_at", "bot_meta_history", ["recorded_at"])

    op.create_table(
        "group_rooms",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("owner_bot_slug", sa.String(64), nullable=False),
        sa.Column("members", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
    )

    op.create_table(
        "group_messages",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column(
            "room_id", sa.Text(), ForeignKey("group_rooms.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("author", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
    )
    op.create_index("ix_group_messages_room_id", "group_messages", ["room_id"])
    op.create_index("ix_group_messages_created_at", "group_messages", ["created_at"])
    op.create_index(
        "uq_group_messages_room_seq",
        "group_messages",
        ["room_id", "seq"],
        unique=True,
    )

    op.create_table(
        "routines",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("bot_id", sa.Integer(), ForeignKey("bots.id", ondelete="CASCADE"), nullable=True),
        sa.Column("schedule", sa.String(128), nullable=False),
        sa.Column("deliver", sa.String(16), server_default=sa.text("'history'"), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("context_from", sa.JSON(), nullable=False),
    )
    op.create_index("ix_routines_next_run_at", "routines", ["next_run_at"])

    op.create_table(
        "pending_turns",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column(
            "bot_id", sa.Integer(), ForeignKey("bots.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("from_handle", sa.String(64), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("hops", sa.Integer(), server_default=sa.text("2"), nullable=False),
        # BOT-M: despachos fallidos acumulados (dead-letter de filas venenosas)
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
    )
    op.create_index("ix_pending_turns_bot_id", "pending_turns", ["bot_id"])
    op.create_index("ix_pending_turns_claimed_at", "pending_turns", ["claimed_at"])
    op.create_index("ix_pending_turns_created_at", "pending_turns", ["created_at"])

    # ── 2. Columnas Bot Mode sobre conversation ─────────────────────────────
    conv_cols = (
        {c["name"] for c in sa.inspect(bind).get_columns("conversation")}
        if "conversation" in sa.inspect(bind).get_table_names()
        else set()
    )

    for name, ddl in CONVERSATION_COLS:
        if name in conv_cols:
            continue
        fk = "" if name != "bot_id" or is_sqlite else " REFERENCES bots (id) ON DELETE CASCADE"
        op.execute(f"ALTER TABLE conversation ADD COLUMN {name} {ddl}{fk}")
        logger.info("conversation.%s añadida", name)

    # [BOT-H2] En fresh-PG el ALTER ya creó la FK inline con CASCADE: crear
    # aquí fk_conversation_bots_cascade DUPLICARÍA la constraint. El bloque de
    # reparación solo aplica cuando la columna PREEXISTÍA (corrida parcial
    # anterior) — espejo exacto del repair-block de core/database.py.
    if "bot_id" not in conv_cols and is_sqlite:
        logger.info("bot_id recién añadida en sqlite: sin FK soportada por ALTER")
    elif "bot_id" not in conv_cols:
        logger.info("FK inline CASCADE creada por ALTER (fresh-path): sin FK extra")
    else:
        # FK posiblemente ya creada por una corrida previa; garantiza CASCADE
        fks = [
            fk
            for fk in sa.inspect(bind).get_foreign_keys("conversation")
            if fk.get("referred_table") == "bots"
            and "bot_id" in (fk.get("constrained_columns") or [])
        ]
        if fks and not any((fk.get("options") or {}).get("ondelete") == "CASCADE" for fk in fks):
            old = [fk["name"] for fk in fks if fk.get("name")]
            with op.batch_alter_table("conversation") as batch:
                for o in old:
                    batch.drop_constraint(o, type_="foreignkey")
                batch.create_foreign_key(
                    "fk_conversation_bots_cascade", "bots", ["bot_id"], ["id"], ondelete="CASCADE"
                )

    # ── 3. Reparación de duplicados ANTES del índice único (C1) ─────────────
    # Conservar el chat más reciente como canónico; resto desmarcado y oculto.
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            UPDATE conversation SET is_canonical = FALSE, is_hidden = TRUE
            WHERE bot_id IS NOT NULL AND is_canonical = TRUE
              AND id NOT IN (
                SELECT MAX(id) FROM conversation
                WHERE bot_id IS NOT NULL AND is_canonical = TRUE
                GROUP BY bot_id
              )
        """
        )
    else:  # sqlite (entornos de prueba legados)
        op.execute(
            """
            UPDATE conversation SET is_canonical = 0, is_hidden = 1
            WHERE bot_id IS NOT NULL AND is_canonical = 1
              AND id NOT IN (
                SELECT MAX(id) FROM conversation
                WHERE bot_id IS NOT NULL AND is_canonical = 1
                GROUP BY bot_id
              )
        """
        )

    # ── 4. Índice único parcial — EL contrato de identidad (C1/T02) ─────────
    insp = sa.inspect(bind)
    if BOT_MODE_INDEX not in _existing_index_names(insp, "conversation"):
        op.create_index(
            BOT_MODE_INDEX,
            "conversation",
            ["bot_id"],
            unique=True,
            postgresql_where=sa.text("is_canonical IS TRUE"),
            sqlite_where=sa.text("is_canonical IS TRUE"),
        )
        logger.info("Índice %s creado", BOT_MODE_INDEX)


def _conversation_bot_fk_name(bind) -> str | None:
    """INT-M1: el nombre de la FK conversation→bots NO es fijo.

    El fresh-path la crea inline en el ADD COLUMN (PG la auto-nombra
    ``conversation_bot_id_fkey``); el repair-path (columna preexistente) usa
    ``fk_conversation_bots_cascade``. Resolver por catálogo — asumir un solo
    nombre rompía el downgrade en toda BD provisionada limpiamente."""
    if bind.dialect.name == "sqlite":
        return None
    row = bind.execute(
        sa.text(
            "SELECT c.conname FROM pg_constraint c "
            "JOIN pg_class t ON t.oid = c.conrelid "
            "JOIN pg_namespace nt ON nt.oid = t.relnamespace "
            "JOIN pg_class r ON r.oid = c.confrelid "
            "JOIN pg_namespace nr ON nr.oid = r.relnamespace "
            "WHERE t.relname = 'conversation' AND c.contype = 'f' "
            "AND r.relname = 'bots' AND nt.nspname = nr.nspname "
            "ORDER BY c.conname LIMIT 1"
        )
    ).first()
    return str(row[0]) if row else None


def downgrade() -> None:
    insp = sa.inspect(bind := op.get_bind())
    if BOT_MODE_INDEX in _existing_index_names(insp, "conversation"):
        op.drop_index(BOT_MODE_INDEX, table_name="conversation")

    with op.batch_alter_table("conversation") as batch:
        if not bind.dialect.name == "sqlite":
            fk_name = _conversation_bot_fk_name(bind)
            if fk_name:
                batch.drop_constraint(fk_name, type_="foreignkey")
            else:
                logger.warning(
                    "downgrade b01: sin FK conversation→bots que soltar "
                    "(¿esquema parcial?) — se continúan las columnas"
                )
        for name, _ddl in reversed(CONVERSATION_COLS):
            batch.drop_column(name)

    op.drop_table("pending_turns")
    op.drop_table("routines")
    op.drop_table("group_messages")
    op.drop_table("group_rooms")
    op.drop_table("bot_meta_history")
    op.drop_table("bots")
