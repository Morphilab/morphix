import asyncio
import logging
import re
from contextlib import asynccontextmanager
from contextvars import ContextVar

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
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

# schema ligado al contexto de ejecución (p.ej. un workflow completo).
# Tiene precedencia sobre _current_async_schema mientras esté activo.
_bound_schema: ContextVar[str | None] = ContextVar("bound_schema", default=None)


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


@asynccontextmanager
async def bound_schema(schema: str):
    """Fija el schema para TODAS las sesiones abiertas dentro del contexto.

    Un workflow largo conserva su workspace aunque otra corrutina cambie el
    schema global (set_async_schema). asyncio.to_thread propaga contextvars,
    por lo que el binding también llega a código ejecutado en hilos.
    """
    if not re.match(r"^[a-z][a-z0-9_]*$", schema):
        raise ValueError(f"Nombre de schema inválido: '{schema}'")
    token = _bound_schema.set(schema)
    try:
        yield
    finally:
        _bound_schema.reset(token)


def rewrite_postgres_url(url: str) -> str:
    """Convierte URLs postgresql:// o postgres:// a postgresql+asyncpg://."""
    return url.replace("postgresql://", "postgresql+asyncpg://").replace(
        "postgres://", "postgresql+asyncpg://"
    )


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
        async_url = rewrite_postgres_url(settings.database_url)
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
    # el binding de ejecución (bound_schema) tiene precedencia sobre el
    # schema global — un workflow largo no cambia de workspace a mitad.
    bound = _bound_schema.get()
    if bound is not None:
        current_schema = bound
    else:
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
    if not re.match(r"^[a-z][a-z0-9_]*$", schema):
        raise ValueError(f"Nombre de schema inválido: '{schema}'")
    engine = _get_async_engine()
    async with engine.begin() as conn:
        await conn.execute(text(f"SET search_path TO {schema}"))
        await conn.run_sync(SQLModel.metadata.create_all)
    # Schemas legacy creados antes de Message.embedding /
    # embedding_fp — create_all no hace ALTER sobre tablas preexistentes;
    # asegura ambas columnas por paridad (idempotente).
    async with engine.begin() as conn:
        await conn.execute(text(f"SET search_path TO {schema}"))
        tbl = await conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :s AND table_name = 'message'"
            ),
            {"s": schema},
        )
        if tbl.first() is not None:
            col = await conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = 'message' "
                    "AND column_name = 'embedding'"
                ),
                {"s": schema},
            )
            if col.first() is None:
                await conn.execute(text("ALTER TABLE message ADD COLUMN embedding BYTEA NULL"))
            col_fp = await conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = 'message' "
                    "AND column_name = 'embedding_fp'"
                ),
                {"s": schema},
            )
            if col_fp.first() is None:
                await conn.execute(
                    text("ALTER TABLE message ADD COLUMN embedding_fp VARCHAR(16) NULL")
                )

    # Bot Mode: schemas legacy creados antes del delta b01 no reciben
    # ni las columnas de Conversation ni el índice único parcial (create_all
    # no hace ALTER sobre tablas preexistentes). Paridad idempotente:
    async with engine.begin() as conn:
        await conn.execute(text(f"SET search_path TO {schema}"))
        tbl = await conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :s AND table_name = 'conversation'"
            ),
            {"s": schema},
        )
        if tbl.first() is not None:
            cols = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = 'conversation'"
                ),
                {"s": schema},
            )
            present = {row[0] for row in cols.fetchall()}
            missing = {
                name: ddl
                for name, ddl in (
                    ("bot_id", "INTEGER"),
                    ("is_canonical", "BOOLEAN DEFAULT false NOT NULL"),
                    ("is_hidden", "BOOLEAN DEFAULT false NOT NULL"),
                    ("capability_epoch", "VARCHAR(12)"),
                )
                if name not in present
            }
            if "bot_id" in missing:
                await conn.execute(
                    text(
                        f"ALTER TABLE {schema}.conversation ADD COLUMN bot_id INTEGER "
                        f"REFERENCES {schema}.bots (id) ON DELETE CASCADE"
                    )
                )
                missing.pop("bot_id")
            for name, ddl in missing.items():
                await conn.execute(
                    text(f"ALTER TABLE {schema}.conversation ADD COLUMN {name} {ddl}")
                )
        # El índice parcial existe en fresh schemas vía __table_args__; en
        # schemas provisionados ANTES de la tabla bots, garantizarlo aquí:
        ix = await conn.execute(
            text(
                "SELECT 1 FROM pg_indexes WHERE schemaname = :s "
                "AND indexname = 'uq_conversation_bot_chat'"
            ),
            {"s": schema},
        )
        if ix.first() is None:
            await conn.execute(
                text(
                    f"CREATE UNIQUE INDEX uq_conversation_bot_chat "
                    f"ON {schema}.conversation (bot_id) WHERE is_canonical IS TRUE"
                )
            )
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
    """Elimina un esquema y todos sus objetos (CASCADE).

    Robustez: el DROP puede bloquearse ETERNAMENTE contra
    conexiones zombis que sostienen locks sobre relaciones del schema
    (conexiones abandonadas mid-transacción). El DROP usa lock_timeout
    de 5s; si hay holders, un segundo intento en conexión AUTOCOMMIT
    (la txn previa quedó abortada): se terminan SUS backends
    (pg_terminate_backend) y se reintenta el DROP. El teardown jamás
    queda colgando."""
    if not re.match(r"^[a-z][a-z0-9_]*$", schema):
        raise ValueError(f"Nombre de schema inválido: '{schema}'")
    engine = _get_async_engine()
    blocked = False
    async with engine.begin() as conn:
        await conn.execute(text("SET lock_timeout = '5s'"))
        try:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        except (OperationalError, DBAPIError):
            blocked = True
    if not blocked:
        logger.info(f"Schema '{schema}' eliminado")
        return

    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        holders = (
            (
                await conn.execute(
                    text(
                        "SELECT DISTINCT l.pid FROM pg_locks l "
                        "JOIN pg_class c ON c.oid = l.relation "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :s AND l.pid <> pg_backend_pid()"
                    ),
                    {"s": schema},
                )
            )
            .scalars()
            .all()
        )
        if holders:
            logger.warning(
                "drop_schema('%s'): DROP bloqueado por backends %s — terminando y reintentando",
                schema,
                holders,
            )
            for pid in holders:
                await conn.execute(text("SELECT pg_terminate_backend(:p)"), {"p": int(pid)})
            await asyncio.sleep(0.3)
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
    logger.info(f"Schema '{schema}' eliminado")


async def startup_db():
    """Inicializa el motor y crea tablas en el esquema main.

    Alembic se invoca manualmente: poetry run alembic upgrade head
    """
    await create_schema("main")
    await create_tables_in_schema("main")
    logger.info("✅ Base de datos inicializada (tablas creadas en esquema main)")


def get_async_schema() -> str:
    """Schema activo para las sesiones async (claves de cache por schema)."""
    return _current_async_schema
