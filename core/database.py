import asyncio
import logging
import re
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from core.config import settings
from core.models import Conversation, Message  # noqa: F401 — referenced by repositories

logger = logging.getLogger(__name__)

_async_engine = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None
_current_async_schema: str = "main"
_engine_loop: asyncio.AbstractEventLoop | None = None
_schema_lock: asyncio.Lock | None = None
_schema_lock_loop: asyncio.AbstractEventLoop | None = None


def _current_running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _get_schema_lock() -> asyncio.Lock:
    """Return a schema lock bound to the current running loop.

    An ``asyncio.Lock`` binds to the loop on first use; under per-test event
    loops a single module-global lock would raise "bound to a different loop".
    Recreate it whenever the running loop changes.
    """
    global _schema_lock, _schema_lock_loop
    loop = _current_running_loop()
    if _schema_lock is None or (loop is not None and _schema_lock_loop is not loop):
        _schema_lock = asyncio.Lock()
        _schema_lock_loop = loop
    return _schema_lock


async def set_async_schema(schema: str) -> None:
    global _current_async_schema
    if not re.match(r"^[a-z][a-z0-9_]*$", schema):
        raise ValueError(f"Nombre de schema inválido: '{schema}'")
    async with _get_schema_lock():
        _current_async_schema = schema
    logger.info(f"Esquema asíncrono cambiado a: {schema}")


def _get_async_engine():
    global _async_engine, _async_session_factory, _engine_loop
    loop = _current_running_loop()
    if (
        _async_engine is not None
        and _engine_loop is not None
        and loop is not None
        and _engine_loop is not loop
    ):
        # The running loop changed (e.g. per-test event loops). The pooled
        # asyncpg connections belong to a now-closed loop — drop the stale
        # engine/factory and recreate them bound to the current loop.
        # Note: old engine connections are cleaned up by GC.
        _async_engine = None
        _async_session_factory = None
    if _async_engine is None:
        async_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://").replace(
            "postgres://", "postgresql+asyncpg://"
        )
        _async_engine = create_async_engine(
            async_url,
            echo=False,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=settings.db_pool_pre_ping,
            pool_recycle=settings.db_pool_recycle,
        )
        _engine_loop = loop
        logger.info("✅ Motor asíncrono de BD creado")
    return _async_engine


async def dispose_engine() -> None:
    """Cierra el pool de conexiones limpiamente — llamar antes de shutdown."""
    global _async_engine, _async_session_factory, _engine_loop
    if _async_engine is not None:
        await _async_engine.dispose()
        _async_engine = None
        _async_session_factory = None
        _engine_loop = None
        logger.info("🔌 Motor de BD cerrado correctamente")


def get_async_session_factory() -> async_sessionmaker[AsyncSession]:
    global _async_session_factory
    engine = _get_async_engine()  # may reset the factory on loop change
    if _async_session_factory is None:
        _async_session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
    return _async_session_factory


@asynccontextmanager
async def get_async_session():
    factory = get_async_session_factory()
    async with _get_schema_lock():
        current_schema = _current_async_schema
    session = factory()
    try:
        await session.execute(text(f"SET search_path TO {current_schema}"))
        yield session
        await session.commit()
    except Exception as e:
        await session.rollback()
        logger.error(f"Error en sesión asíncrona: {e}")
        raise
    finally:
        await session.close()


async def create_schema(schema: str) -> None:
    """Crea un esquema PostgreSQL si no existe (async)."""
    if not re.match(r"^[a-z][a-z0-9_]*$", schema):
        raise ValueError(f"Nombre de schema inválido: '{schema}'")
    engine = _get_async_engine()
    async with engine.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
    logger.info(f"Schema '{schema}' creado/verificado")


async def create_tables_in_schema(schema: str) -> None:
    """Crea las tablas SQLModel en el esquema indicado usando el motor async."""
    engine = _get_async_engine()
    async with engine.begin() as conn:
        await conn.execute(text(f"SET search_path TO {schema}"))
        await conn.run_sync(SQLModel.metadata.create_all)
    logger.info(f"Tablas creadas en el esquema {schema}")


async def list_schemas() -> list[str]:
    """Lista los esquemas (workspaces) disponibles."""
    engine = _get_async_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name NOT LIKE 'pg_%' AND schema_name != 'information_schema'"
            )
        )
        return [row[0] for row in result.fetchall()]


async def drop_schema(schema: str) -> None:
    """Elimina un esquema y todos sus objetos (CASCADE)."""
    if not re.match(r"^[a-z][a-z0-9_]*$", schema):
        raise ValueError(f"Nombre de schema inválido: '{schema}'")
    engine = _get_async_engine()
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
    logger.info(f"Schema '{schema}' eliminado")


async def startup_db():
    """Inicializa el motor y crea tablas en el esquema main.

    Alembic se invoca manualmente: poetry run alembic upgrade head
    """
    await create_schema("main")
    await create_tables_in_schema("main")
    logger.info("✅ Base de datos inicializada (tablas creadas en esquema main)")


async def init_db() -> None:
    await startup_db()
