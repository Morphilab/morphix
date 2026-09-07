# tests/test_alembic_baseline.py — deploy limpio vía alembic upgrade head
"""La baseline p04_baseline congela el DDL de las tablas base; la cadena completa
(baseline → a15xvi → h7a) debe provisionar una BD virgen idéntica a lo que produce
startup_db/create_all, con los índices de a15xvi encima."""

import os
import secrets
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


# ── Unidades sin BD ────────────────────────────────────────────────────────


def test_chain_single_head_and_order():
    """Historial lineal: p04_baseline → a15xvi → h7a → h8a → b01 → c02 (head único)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert heads == ["c02_routine_resilience"], "un solo head; los deltas se encadenan lineales"

    base = script.get_revision("p04_baseline")
    assert base.down_revision is None, "la baseline debe ser la raíz"
    a15 = script.get_revision("a15xvi_idx_cascade")
    assert a15.down_revision == "p04_baseline"
    h7 = script.get_revision("h7a_msg_embedding")
    assert h7.down_revision == "a15xvi_idx_cascade"
    h8 = script.get_revision("h8a_msg_embedding_fp")
    assert h8.down_revision == "h7a_msg_embedding"
    b01 = script.get_revision("b01_bot_mode")
    assert b01.down_revision == "h8a_msg_embedding_fp"
    c02 = script.get_revision("c02_routine_resilience")
    assert c02.down_revision == "b01_bot_mode"


def _load_baseline():
    """alembic/versions/ no es paquete Python; carga la migración por ruta."""
    import importlib.util

    path = REPO_ROOT / "alembic" / "versions" / "p04_baseline_base_tables.py"
    spec = importlib.util.spec_from_file_location("p04_baseline_mod", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_baseline_covers_every_model_table():
    """El DDL congelado de la baseline cubre SUS 6 tablas base; las tablas que
    añadieron deltas posteriores (b01 bot-mode) viven solo en sus migraciones
    y en el metadata — la relación baseline↔metadata es de subconjunto."""
    from sqlmodel import SQLModel

    import core.models  # noqa: F401 — registra las tablas en el metadata

    mod = _load_baseline()

    ddl_tables = {
        d.split("CREATE TABLE ", 1)[1].split(" (", 1)[0].strip('"') for d in mod._TABLE_DDL
    }
    meta_tables = {t.name for t in SQLModel.metadata.sorted_tables}
    assert ddl_tables <= meta_tables, f"baseline↛metadata: {ddl_tables - meta_tables}"
    # Las 6 tablas congeladas siguen existiendo sin renombres
    assert ddl_tables >= {
        "conversation",
        "message",
        "workflow",
        "user",
        "paused_sessions",
        "blackboard_entries",
    }


def test_baseline_ddl_matches_metadata_shape():
    """Paridad textual baseline↔metadata, respetando deltas aditivos.

    Las tablas NO tocadas por deltas posteriores deben ser textualmente
    idénticas (mismo generador ⇒ drift manual imposible). Las tablas que un
    delta extendió de forma aditiva (b01 añadió columnas Bot Mode a
    conversation) se verifican por SUBCONJUNTO: toda columna congelada debe
    seguir presente e intacta en el compilado; las columnas añadidas quedan
    cubiertas por el e2e de catálogo alembic↔create_all.
    """
    import re

    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable, MetaData
    from sqlmodel import SQLModel

    import core.models  # noqa: F401 — registra las tablas en el metadata

    # tablas que deltas posteriores extendieron aditivamente (b01 bot-mode)
    DELTA_ADDITIVE = {"conversation"}

    mod = _load_baseline()

    frozen: dict[str, str] = {}
    for d in mod._TABLE_DDL:
        raw = d.split("CREATE TABLE ", 1)[1].split(" (", 1)[0]
        frozen[raw.strip('"')] = d

    norm = lambda s: re.sub(r"\s+", " ", s)  # noqa: E731

    def _frozen_column_names(ddl: str) -> list[str]:
        cols = []
        for line in ddl.splitlines()[1:]:
            tok = line.strip().rstrip(",").split(" ", 1)[0].strip('"')
            if not tok or tok.upper() in {
                "PRIMARY",
                "UNIQUE",
                "FOREIGN",
                "CONSTRAINT",
                "CHECK",
            }:
                continue
            if line.strip().startswith(")"):
                break
            cols.append(tok)
        return cols

    isolated = MetaData()
    for t in SQLModel.metadata.sorted_tables:
        t.to_metadata(isolated)
    for t in isolated.sorted_tables:
        if t.name not in frozen:
            continue  # tablas de deltas posteriores: paridad vía e2e de catálogo
        compiled_norm = norm(
            str(CreateTable(t).compile(dialect=postgresql.dialect())).strip().rstrip(";")
        )
        if t.name in DELTA_ADDITIVE:
            for col in _frozen_column_names(frozen[t.name]):
                assert re.search(
                    rf"\b{re.escape(col)}\b", compiled_norm
                ), f"columna congelada '{col}' ausente o alterada en {t.name}"
        else:
            frozen_norm = norm(frozen[t.name])
            assert frozen_norm == compiled_norm, f"drift en {t.name}"


# ── E2E contra PostgreSQL real (CI service / local) ───────────────────────


def _db_url() -> str | None:
    load = os.environ.get("DATABASE_URL")
    return load


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="requiere DATABASE_URL (PG real)")
@pytest.mark.asyncio
async def test_upgrade_head_on_virgin_schema_matches_create_all():
    """BD/schema vírgenes: upgrade head provisions TODO y queda par con create_all."""
    import asyncio

    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine

    suffix = secrets.token_hex(4)
    sch_alembic = f"p04e2e_a_{suffix}"
    sch_ref = f"p04e2e_b_{suffix}"
    url = os.environ["DATABASE_URL"]
    async_url = url.replace("postgresql://", "postgresql+asyncpg://")

    env = dict(os.environ)
    env["DATABASE_URL"] = url
    env["ALEMBIC_SCHEMA"] = sch_alembic
    # El schema NO se pre-crea: el flujo debe bastarse solo (gotcha fix).

    cleaner = create_async_engine(async_url)

    async def _drop_schemas() -> None:
        async with cleaner.begin() as conn:
            await conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {sch_alembic} CASCADE"))
            await conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {sch_ref} CASCADE"))

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "alembic",
            "upgrade",
            "head",
            cwd=str(REPO_ROOT),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        assert proc.returncode == 0, f"alembic falló:\n{out.decode()[-2000:]}"

        async with cleaner.connect() as conn:
            q = sa.text(
                "SELECT table_name, column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name != 'alembic_version' ORDER BY 1, 2"
            )
            alembic_cat = {(r[0], r[1]): r[2] for r in (await conn.execute(q, {"s": sch_alembic}))}
            version = (
                await conn.execute(
                    sa.text(f"SELECT version_num FROM {sch_alembic}.alembic_version")
                )
            ).scalar()
            from alembic.config import Config as _Cfg
            from alembic.script import ScriptDirectory as _SD

            _script = _SD.from_config(_Cfg(str(REPO_ROOT / "alembic.ini")))
            expected_head = _script.get_heads()[0]
            assert version == expected_head
            ix = (
                (
                    await conn.execute(
                        sa.text(
                            "SELECT indexname FROM pg_indexes WHERE schemaname = :s "
                            "AND (indexname LIKE 'ix_%' OR indexname LIKE 'uq_%')"
                        ),
                        {"s": sch_alembic},
                    )
                )
                .scalars()
                .all()
            )
        assert set(ix) >= {
            "ix_message_conversation_id",
            "ix_message_timestamp",
            "ix_paused_sessions_conversation_id",
            "ix_paused_sessions_created_at",
            "uq_conversation_bot_chat",
        }

        # Referencia: create_all en schema hermano (startup_db crea el schema antes)
        from sqlmodel import SQLModel

        import core.models  # noqa: F401

        async with cleaner.begin() as conn:
            await conn.execute(sa.text(f"CREATE SCHEMA IF NOT EXISTS {sch_ref}"))
        aeng = create_async_engine(
            async_url, connect_args={"server_settings": {"search_path": sch_ref}}
        )
        try:
            async with aeng.begin() as conn:
                await conn.execute(sa.text(f"SET search_path TO {sch_ref}"))
                await conn.run_sync(SQLModel.metadata.create_all)
        finally:
            await aeng.dispose()

        async with cleaner.connect() as conn:
            ref_cat = {(r[0], r[1]): r[2] for r in (await conn.execute(q, {"s": sch_ref}))}
        assert alembic_cat == ref_cat, (
            f"paridad rota: solo-alembic={set(alembic_cat) - set(ref_cat)} "
            f"sole-ref={set(ref_cat) - set(alembic_cat)}"
        )

        # [BOT-H2] Paridad de FOREIGN KEYS también: la duplicada fk_conversation_
        # bots_cascade + la inline del ALTER fresh pasaban desapercibidas sin esto.
        fq = sa.text(
            "SELECT conname, pg_get_constraintdef(c.oid) FROM pg_constraint c "
            "JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = :s AND t.relname = 'conversation' "
            "AND c.contype = 'f' ORDER BY 1"
        )
        async with cleaner.connect() as conn:
            a_fks = {(r[0], r[1]) for r in (await conn.execute(fq, {"s": sch_alembic}))}
            r_fks = {(r[0], r[1]) for r in (await conn.execute(fq, {"s": sch_ref}))}
        assert len(a_fks) == len(
            r_fks
        ), f"FK conversation duplicadas en fresh-path alembic={a_fks} ref={r_fks}"
    finally:
        try:
            await _drop_schemas()
        finally:
            await cleaner.dispose()


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="requiere DATABASE_URL (PG real)")
@pytest.mark.asyncio
async def test_repeated_upgrade_head_is_idempotent():
    """upgrade head dos veces: segunda pasada es no-op limpio (sin errores)."""
    import asyncio

    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine

    suffix = secrets.token_hex(4)
    sch = f"p04e2e_c_{suffix}"
    url = os.environ["DATABASE_URL"]
    env = dict(os.environ)
    env["DATABASE_URL"] = url
    env["ALEMBIC_SCHEMA"] = sch

    eng = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://"))

    async def _drop() -> None:
        async with eng.begin() as conn:
            await conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {sch} CASCADE"))

    try:
        for i in range(2):
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "alembic",
                "upgrade",
                "head",
                cwd=str(REPO_ROOT),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
            assert proc.returncode == 0, f"pasada {i + 1} falló:\n{out.decode()[-1500:]}"
    finally:
        try:
            await _drop()
        finally:
            await eng.dispose()


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="requiere DATABASE_URL (PG real)")
@pytest.mark.asyncio
async def test_downgrade_from_head_delegates_fk_resolution():
    """`alembic downgrade -1` (b01→h8a) en schema virgen.

    El fresh-path crea la FK conversation→bots INLINE en el ADD COLUMN
    (PG la auto-nombra `conversation_bot_id_fkey`); el downgrade anterior
    soltaba incondicionalmente `fk_conversation_bots_cascade` →
    UndefinedObjectError en TODA BD provisionada limpiamente."""
    import asyncio

    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine

    suffix = secrets.token_hex(4)
    sch = f"p04e2e_d_{suffix}"
    url = os.environ["DATABASE_URL"]
    env = dict(os.environ)
    env["DATABASE_URL"] = url
    env["ALEMBIC_SCHEMA"] = sch

    eng = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://"))

    async def _drop() -> None:
        async with eng.begin() as conn:
            await conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {sch} CASCADE"))

    async def _alembic(*args: str) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "alembic",
            *args,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        return proc.returncode, out.decode()[-1500:]

    try:
        rc, out = await _alembic("upgrade", "head")
        assert rc == 0, f"upgrade falló:\n{out}"
        rc, out = await _alembic("downgrade", "-1")
        assert rc == 0, (
            "INT-M1: el downgrade de b01 falla tras upgrade limpio "
            f"(resolución de FK por introspección rota):\n{out}"
        )
    finally:
        try:
            await _drop()
        finally:
            await eng.dispose()


def test_env_py_rejects_invalid_schema_name():
    """La validación de ALEMBIC_SCHEMA existe en el fuente de env.py (fail-closed)."""
    src = (REPO_ROOT / "alembic" / "env.py").read_text(encoding="utf-8")
    assert "CREATE SCHEMA IF NOT EXISTS" in src
    assert 're.match(r"^[a-z][a-z0-9_]*$", schema)' in src
