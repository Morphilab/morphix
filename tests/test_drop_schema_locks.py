# tests/test_drop_schema_locks.py — robustez del teardown
"""drop_schema contra conexiones zombis: el escenario EXACTO del flake de
suite (run I colgó 43 min: un `DROP SCHEMA ... CASCADE` esperando locks de
`relation` sostenidos por conexiones abandonadas mid-transacción, con las
conexiones congeladas en ClientRead). El teardown ahora acota con
lock_timeout, termina los holders y reintenta — jamás cuelga."""

import asyncio
import os
import secrets

import asyncpg
import pytest

from core.database import bound_schema, create_schema, create_tables_in_schema, drop_schema

_pg = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="requiere DATABASE_URL (PG real)"
)


@_pg
@pytest.mark.asyncio
async def test_drop_schema_termina_holders_y_no_cuelga():
    """Una conexión ajena con txn abierta sobre el schema NO bloquea el drop."""
    sch = f"droplock_{secrets.token_hex(4)}"
    await create_schema(sch)
    async with bound_schema(sch):
        await create_tables_in_schema(sch)

    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    holder = await asyncpg.connect(url)
    try:
        # Transacción abierta con lock sobre una relación del schema (zombi simulado)
        await holder.execute(
            f"BEGIN; INSERT INTO {sch}.conversation (title, created_at) VALUES ('x', now())"
        )
        await asyncio.wait_for(drop_schema(sch), timeout=30)
    finally:
        await holder.close()
    # el schema ya no existe
    probe = await asyncpg.connect(url)
    try:
        existe = await probe.fetchval(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name=$1", sch
        )
    finally:
        await probe.close()
    assert not existe


@_pg
@pytest.mark.asyncio
async def test_drop_schema_sin_conflicto_sigue_funcionando():
    """Camino feliz intacto: schema sin holders se elimina al primer intento."""
    sch = f"dropok_{secrets.token_hex(4)}"
    await create_schema(sch)
    async with bound_schema(sch):
        await create_tables_in_schema(sch)
    await asyncio.wait_for(drop_schema(sch), timeout=30)
    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    probe = await asyncpg.connect(url)
    try:
        existe = await probe.fetchval(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name=$1", sch
        )
    finally:
        await probe.close()
    assert not existe
