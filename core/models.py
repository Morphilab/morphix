# core/models.py
from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import Column, Text
from sqlmodel import Field, Relationship, SQLModel


def _utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


class Conversation(SQLModel, table=True):  # type: ignore[call-arg]
    id: int | None = Field(default=None, primary_key=True)
    title: str
    created_at: datetime = Field(default_factory=_utc_now)
    tags: str | None = None
    workflow_id: int | None = Field(default=None, foreign_key="workflow.id")
    workflow: Optional["Workflow"] = Relationship(back_populates="conversations")
    messages: list["Message"] = Relationship(back_populates="conversation")


class Message(SQLModel, table=True):  # type: ignore[call-arg]
    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="conversation.id")
    role: str
    content: str
    timestamp: datetime = Field(default_factory=_utc_now)
    conversation: Optional["Conversation"] = Relationship(back_populates="messages")


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
    conversation_id: int | None = Field(default=None, foreign_key="conversation.id")
    clarification_question: str
    clarification_options: str | None = None  # JSON string
    paused_state: str = ""  # JSON string
    clarification_answer: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    resolved_at: datetime | None = None


class BlackboardEntry(SQLModel, table=True):  # type: ignore[call-arg]
    __tablename__ = "blackboard_entries"
    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    phase: str = Field(default="default")
    key: str
    value: str = Field(sa_column=Column(Text))
    created_at: datetime = Field(default_factory=_utc_now)
