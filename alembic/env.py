"""Alembic environment config — usa el motor asíncrono de Morphix."""

import asyncio
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

from alembic import context
from core.config import settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from core.models import Conversation, Message, User, Workflow  # noqa: F401 — metadata

target_metadata = SQLModel.metadata


def _get_async_url() -> str:
    return settings.database_url.replace("postgresql://", "postgresql+asyncpg://")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = _get_async_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    connectable = create_async_engine(_get_async_url(), echo=False)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
