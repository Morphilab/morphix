# core/models.py
from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlmodel import Field, Relationship, SQLModel


def _utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


# Bot Mode: identidad conversacional = (bot_id, título exacto "Bot Chat").
# El índice único parcial DEBE vivir en __table_args__: los schemas de workspace
# se provisionan con create_all y NUNCA reciben los índices de las migraciones.
UQ_BOT_CHAT_INDEX = Index(
    "uq_conversation_bot_chat",
    "bot_id",
    unique=True,
    postgresql_where=text("is_canonical IS TRUE"),
)

# conversación dedicada de rutina por título
# '⏰ <nombre>' — hace idempotente el INSERT de _conversation_for_history.
# Mismo motivo que UQ_BOT_CHAT_INDEX: DEBE vivir en __table_args__ o los
# schemas de workspace (create_all) jamás lo recibirán.
UQ_ROUTINE_TITLE_INDEX = Index(
    "uq_conversation_routine_title",
    "title",
    unique=True,
    postgresql_where=text("title LIKE '⏰ %'"),
)

BOT_CHAT_TITLE = "Bot Chat"


class Conversation(SQLModel, table=True):  # type: ignore[call-arg]
    __table_args__ = (UQ_BOT_CHAT_INDEX, UQ_ROUTINE_TITLE_INDEX)
    id: int | None = Field(default=None, primary_key=True)
    title: str
    created_at: datetime = Field(default_factory=_utc_now)
    tags: str | None = None
    workflow_id: int | None = Field(default=None, foreign_key="workflow.id")
    workflow: Optional["Workflow"] = Relationship(back_populates="conversations")
    messages: list["Message"] = Relationship(back_populates="conversation")
    # Bot Mode: dueño del chat canónico eterno (NULL = conversación del usuario)
    bot_id: int | None = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("bots.id", ondelete="CASCADE"), nullable=True),
    )
    is_canonical: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
    is_hidden: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
    # Capability epoch del system prompt del canónico (SHA-256[:12])
    capability_epoch: str | None = Field(default=None, sa_column=Column(String(12), nullable=True))


class Message(SQLModel, table=True):  # type: ignore[call-arg]
    id: int | None = Field(default=None, primary_key=True)
    # FK con CASCADE + índice — delete() ya no choca con IntegrityError
    conversation_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("conversation.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        ),
    )
    role: str
    content: str
    timestamp: datetime = Field(
        sa_column=Column(DateTime, default=_utc_now, index=True, nullable=False),
        default_factory=_utc_now,
    )
    conversation: Optional["Conversation"] = Relationship(back_populates="messages")
    # embedding persistido del contenido (float32 little-endian) — evita
    # re-embeddeo por query (cold-path semantic_search).
    embedding: bytes | None = Field(
        default=None, sa_column=Column("embedding", LargeBinary, nullable=True)
    )
    # Fingerprint del backend que generó el vector — un cambio de familia
    # con misma dimensión mezclaría escalas silenciosamente; fp distinto ⇒
    # el vector se considera ausente (re-encodeo lazy).
    embedding_fp: str | None = Field(
        default=None,
        sa_column=Column(String(16), nullable=True),
    )


class Workflow(SQLModel, table=True):  # type: ignore[call-arg]
    id: int | None = Field(default=None, primary_key=True)
    query: str
    subtasks: str  # JSON str de subtareas
    status: str = "pending"
    scorecard: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    conversations: list[Conversation] = Relationship(back_populates="workflow")


class User(SQLModel, table=True):  # type: ignore[call-arg]
    id: int = Field(default=None, primary_key=True)
    username: str = Field(unique=True)
    password_hash: str


class PausedSession(SQLModel, table=True):  # type: ignore[call-arg]
    __tablename__ = "paused_sessions"
    id: int | None = Field(default=None, primary_key=True)
    # FK con CASCADE + índice; purga diaria de pausas resueltas
    conversation_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("conversation.id", ondelete="CASCADE"),
            index=True,
            nullable=True,
        ),
    )
    clarification_question: str
    clarification_options: str | None = None  # JSON string
    paused_state: str = ""  # JSON string
    clarification_answer: str | None = None
    created_at: datetime = Field(
        sa_column=Column(DateTime, default=_utc_now, index=True),
        default_factory=_utc_now,
    )
    resolved_at: datetime | None = None


class BlackboardEntry(SQLModel, table=True):  # type: ignore[call-arg]
    __tablename__ = "blackboard_entries"
    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    phase: str = Field(default="default")
    key: str
    value: str = Field(sa_column=Column(Text))
    created_at: datetime = Field(default_factory=_utc_now)


# ══════════════════════════════════════════════════════════════════════════
# Bot Mode: cada bot vive aislado en el schema de SU workspace; sin tablas globales.
# ══════════════════════════════════════════════════════════════════════════

BOT_SLUG_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"


class Bot(SQLModel, table=True):  # type: ignore[call-arg]
    """Un bot con identidad persistente (NO es una tabla de sesiones)."""

    __tablename__ = "bots"
    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(sa_column=Column(String(64), nullable=False, unique=True), default="")
    display_name: str = Field(sa_column=Column(String(64), nullable=False), default="")
    description: str = Field(default="", sa_column=Column(Text, nullable=False))
    soul_md: str = Field(default="", sa_column=Column(Text, nullable=False))
    provider: str | None = Field(default=None, sa_column=Column(String(64)))
    model: str | None = Field(default=None, sa_column=Column(String(128)))
    temperature: float | None = Field(default=None, sa_column=Column(Float))
    tool_names: str = Field(default="[]", sa_column=Column(JSON, nullable=False))
    skill_allowlist: str = Field(default="[]", sa_column=Column(JSON, nullable=False))
    memory_prefix: str = Field(sa_column=Column(String(128), nullable=False), default="")
    ui_meta: dict = Field(default={}, sa_column=Column(JSON, nullable=False))
    # CAS server-authoritative
    ui_meta_rev: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default=text("0"))
    )
    enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime, default=_utc_now, nullable=False),
        default_factory=_utc_now,
    )
    updated_at: datetime = Field(
        sa_column=Column(DateTime, default=_utc_now, onupdate=_utc_now, nullable=False),
        default_factory=_utc_now,
    )


class BotMetaHistory(SQLModel, table=True):  # type: ignore[call-arg]
    """Las revisiones ui_meta sobreviven al borrado del bot."""

    __tablename__ = "bot_meta_history"
    id: int | None = Field(default=None, primary_key=True)
    bot_slug: str = Field(sa_column=Column(String(64), nullable=False, index=True))
    rev: int = Field(sa_column=Column(Integer, nullable=False))
    ui_meta: dict = Field(default={}, sa_column=Column(JSON, nullable=False))
    tombstone: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
    recorded_at: datetime = Field(
        sa_column=Column(DateTime, default=_utc_now, index=True, nullable=False),
        default_factory=_utc_now,
    )


class GroupRoom(SQLModel, table=True):  # type: ignore[call-arg]
    """Sala grupal: roomId inmutable 'r<base36>-<rand>'."""

    __tablename__ = "group_rooms"
    id: str = Field(sa_column=Column(Text, primary_key=True))
    name: str = Field(default="", sa_column=Column(String(128), nullable=False))
    owner_bot_slug: str = Field(default="", sa_column=Column(String(64), nullable=False))
    members: list = Field(default=[], sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(
        sa_column=Column(DateTime, default=_utc_now, nullable=False),
        default_factory=_utc_now,
    )


class GroupMessage(SQLModel, table=True):  # type: ignore[call-arg]
    """Log ordenado client-side de la sala (seq único por sala)."""

    __tablename__ = "group_messages"
    id: int | None = Field(default=None, primary_key=True)
    room_id: str = Field(
        sa_column=Column(
            Text,
            ForeignKey("group_rooms.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    author: str = Field(sa_column=Column(String(64), nullable=False))  # 'user' | slug
    content: str = Field(sa_column=Column(Text, nullable=False))
    seq: int = Field(sa_column=Column(Integer, nullable=False))
    created_at: datetime = Field(
        sa_column=Column(DateTime, default=_utc_now, index=True, nullable=False),
        default_factory=_utc_now,
    )
    __table_args__ = (Index("uq_group_messages_room_seq", "room_id", "seq", unique=True),)


class Routine(SQLModel, table=True):  # type: ignore[call-arg]
    """Rutina por bot; bot_id NULL = corre en contexto del usuario."""

    __tablename__ = "routines"
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(sa_column=Column(String(128), nullable=False))
    bot_id: int | None = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("bots.id", ondelete="CASCADE"), nullable=True),
    )
    schedule: str = Field(sa_column=Column(String(128), nullable=False))
    deliver: str = Field(
        default="history",
        sa_column=Column(String(16), nullable=False, server_default=text("'history'")),
    )  # 'history' | 'bot-chat'
    prompt: str = Field(default="", sa_column=Column(Text, nullable=False))
    enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
    )
    last_run_at: datetime | None = Field(default=None, sa_column=Column(DateTime))
    next_run_at: datetime | None = Field(default=None, sa_column=Column(DateTime, index=True))
    last_error: str | None = Field(default=None, sa_column=Column(Text))
    # fallos consecutivos; al alcanzar
    # ROUTINE_DEAD_LETTER el re-firing se acota (sin re-ejecución ilimitada
    # de tools con efectos secundarios).
    failure_count: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default=text("0"))
    )
    context_from: list = Field(default=[], sa_column=Column(JSON, nullable=False))


class PendingTurn(SQLModel, table=True):  # type: ignore[call-arg]
    """Inbox real del bot: la entrada encolada ES un turno user-role futuro.

    hops = TTL anti-bucle (cadenas A→B→C mueren solas).
    """

    __tablename__ = "pending_turns"
    id: int | None = Field(default=None, primary_key=True)
    bot_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("bots.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    source: str = Field(sa_column=Column(String(16), nullable=False))  # 'dm'|'routine'|'group'
    from_handle: str = Field(default="", sa_column=Column(String(64), nullable=False))
    body: str = Field(sa_column=Column(Text, nullable=False))
    hops: int = Field(
        default=2, sa_column=Column(Integer, nullable=False, server_default=text("2"))
    )
    # contador de despachos fallidos; ≥ dead-letter threshold ⇒ la fila
    # ya no se reclama (venenosa) y queda auditable en lugar de golpear forever.
    attempts: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default=text("0"))
    )
    claimed_at: datetime | None = Field(default=None, sa_column=Column(DateTime, index=True))
    created_at: datetime = Field(
        sa_column=Column(DateTime, default=_utc_now, index=True, nullable=False),
        default_factory=_utc_now,
    )
