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
    """Run migrations in 'online' mode with async engine.

    Las tablas viven en schemas por-workspace: ALEMBIC_SCHEMA (default
    'main') fija el search_path de la conexión de migración.
    """
    import os
    import re

    from sqlalchemy import text

    schema = os.environ.get("ALEMBIC_SCHEMA", "main")
    if not re.match(r"^[a-z][a-z0-9_]*$", schema):
        raise ValueError(f"ALEMBIC_SCHEMA inválido: '{schema}'")
    connectable = create_async_engine(
        _get_async_url(),
        echo=False,
        connect_args={"server_settings": {"search_path": schema}},
    )
    # Fix gotcha 'no schema has been selected': BD vírgenes no traen el
    # schema destino; alembic_version se crearía antes de la primera
    # migración y fallaría. Pre-creamos el schema ANTES de migrar.
    async with connectable.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
        # SA 2.0 commit-as-you-go: si cualquier statement previo dispara
        # autobegin, begin_transaction() de Alembic devuelve nullcontext y
        # sin este commit el DDL transaccional se revierte al cerrar.
        await connection.commit()
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
