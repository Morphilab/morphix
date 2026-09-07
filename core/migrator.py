# core/migrator.py — wrapper endurecido para migraciones Alembic
"""Punto ÚNICO de invocación de migraciones (invariante buzz):

1. Advisory-lock de sesión PG (clave constante) sostenido en una conexión
   independiente durante TODA la operación — serializa la migración contra
   otras instancias y operaciones destructivas.
2. Guard previo fail-closed: rechaza estados ambiguos con error accionable
   (tablas-sin-stamp ⇒ falta `alembic stamp head`; revisión fantasma).
3. Ejecuta `alembic upgrade head` como subprocess aislado.
4. Verificación POST del catálogo: tablas base presentes + versión == head.

Ninguna otra capa debe invocar alembic directamente — el lint-test
test_lint_no_direct_alembic_outside_wrapper lo custodia.
"""

import asyncio
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

logger = logging.getLogger(__name__)

# Clave i64 estable ('MORF' little-endian truncada) — misma en todos los procesos.
ADVISORY_LOCK_KEY = 0x4D4F5246

_BASE_TABLES = {
    "conversation",
    "message",
    "user",
    "workflow",
    "paused_sessions",
    "blackboard_entries",
}


@dataclass
class MigrationOutcome:
    ok: bool
    blocked: bool = False
    detail: str = ""


def current_head() -> str:
    """Head del script map (sin tocar BD)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"Script map con múltiples heads: {heads}")
    return heads[0]


async def _fetch_stamped_revision(conn, schema: str) -> str | None:
    exists = await conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = :s AND table_name = 'alembic_version'"
        ),
        {"s": schema},
    )
    if exists.scalar() is None:
        return None
    res = await conn.execute(sa.text(f"SELECT version_num FROM {schema}.alembic_version"))
    return res.scalar()


async def _base_tables_present(conn, schema: str) -> set[str]:
    res = await conn.execute(
        sa.text("SELECT table_name FROM information_schema.tables " "WHERE table_schema = :s"),
        {"s": schema},
    )
    return {row[0] for row in res.fetchall()}


async def _default_runner(argv: list[str], env: dict) -> int:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        logger.error("alembic falló:\n%s", out.decode(errors="replace")[-2000:])
    return proc.returncode or 0


def _validate_schema_name(schema: str) -> None:
    if not re.match(r"^[a-z][a-z0-9_]*$", schema):
        raise ValueError(f"Nombre de schema inválido: '{schema}'")


async def upgrade_head(
    schema: str = "main",
    *,
    engine=None,
    runner=None,
) -> MigrationOutcome:
    """Migra `schema` a head bajo advisory-lock, con guards y verificación."""
    _validate_schema_name(schema)

    from core.config import settings

    url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
    own_engine = engine is None
    eng = engine or create_async_engine(url)
    runner_fn = runner or _default_runner
    conn = eng.connect()
    try:
        async with conn:
            # 1) lock de sesión sostenido por esta conexión durante todo el flujo
            await conn.execute(sa.text("SELECT pg_advisory_lock(:k)"), {"k": ADVISORY_LOCK_KEY})
            try:
                stamped = await _fetch_stamped_revision(conn, schema)
                tables = await _base_tables_present(conn, schema)

                # 2) guards previos fail-closed
                if stamped is not None and stamped not in _known_revisions():
                    return MigrationOutcome(
                        ok=False,
                        blocked=True,
                        detail=(
                            f"alembic_version contiene la revisión desconocida "
                            f"'{stamped}' — BD de otro árbol de migraciones; no se toca."
                        ),
                    )
                if stamped is None and tables & _BASE_TABLES:
                    return MigrationOutcome(
                        ok=False,
                        blocked=True,
                        detail=(
                            "Tablas base existen sin stamp de Alembic (BD creada por "
                            "startup_db pre-P0.4): ejecuta una vez "
                            "`ALEMBIC_SCHEMA=<schema> poetry run alembic stamp head` "
                            "y reintenta."
                        ),
                    )

                # 3) migrar (subprocess aislado; env.py pre-crea el schema)
                env_vars = dict(os.environ)
                env_vars["DATABASE_URL"] = settings.database_url
                env_vars["ALEMBIC_SCHEMA"] = schema
                rc = await runner_fn([sys.executable, "-m", "alembic", "upgrade", "head"], env_vars)
                if rc != 0:
                    return MigrationOutcome(ok=False, detail=f"alembic upgrade head rc={rc}")

                # 4) verificación POST fail-closed
                post_tables = await _base_tables_present(conn, schema)
                missing = _BASE_TABLES - post_tables
                if missing:
                    return MigrationOutcome(
                        ok=False,
                        detail=f"Post-migración: faltan tablas del catálogo base: {sorted(missing)}",
                    )
                post_stamped = await _fetch_stamped_revision(conn, schema)
                if post_stamped != current_head():
                    return MigrationOutcome(
                        ok=False,
                        detail=(
                            f"Post-migración: alembic_version='{post_stamped}' "
                            f"!= head '{current_head()}'"
                        ),
                    )
                logger.info("Migraciones al día (%s): schema '%s'", post_stamped, schema)
                return MigrationOutcome(ok=True)
            finally:
                await conn.execute(
                    sa.text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_KEY}
                )
    finally:
        if own_engine:
            await eng.dispose()


def _known_revisions() -> set[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)
    walk: set[str] = set()
    for rev in script.walk_revisions():
        walk.add(rev.revision)
    return walk


if __name__ == "__main__":  # pragma: no cover — CLI operativo
    import argparse

    parser = argparse.ArgumentParser(description="Migra Morphix a head (wrapper endurecido)")
    parser.add_argument("--schema", default="main", help="Schema destino (default: main)")
    args = parser.parse_args()

    outcome = asyncio.run(upgrade_head(schema=args.schema))
    print(("✅" if outcome.ok else "❌") + f" {outcome.detail}" or ("✅ ok" if outcome.ok else ""))
    sys.exit(0 if outcome.ok else 1)
