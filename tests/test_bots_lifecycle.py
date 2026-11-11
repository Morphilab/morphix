# tests/test_bots_lifecycle.py — CRUD, CAS de ui_meta y
# supervivencia de revisiones tras el borrado.
"""Ciclo de vida de bots contra PostgreSQL real en un schema efímero.

Convenciones del repo: sin fixtures compartidos; helpers inline; PG vía
DATABASE_URL (skip si no está).
"""

import os
import secrets

import pytest

from core.bots import BOT_UI_META_MAX_BYTES, BotError, BotsService, validate_slug
from core.bots_registry import BotsRegistry, UnknownTargetError, resolve_target
from core.database import (
    bound_schema,
    create_schema,
    create_tables_in_schema,
    drop_schema,
)


def _url() -> str | None:
    return os.environ.get("DATABASE_URL")


# ── Unidades sin BD ────────────────────────────────────────────────────────


def test_validate_slug_rules():
    assert validate_slug("alpha") == "alpha"
    assert validate_slug("  beta-1_x ") == "beta-1_x"
    for bad in ("", "x" * 65, "-lead", "_lead", "Con Mayúsculas", "espacio x", "a/b"):
        with pytest.raises(BotError):
            validate_slug(bad)


def test_ui_meta_cap_is_64kib():
    assert BOT_UI_META_MAX_BYTES == 64 * 1024


def test_registry_is_instantiable():
    r = BotsRegistry()
    assert r.principal is None
    r.set_principal(None)
    assert r.principal is None


# ── E2E contra PostgreSQL real ─────────────────────────────────────────────


@pytest.mark.skipif(not _url(), reason="requiere DATABASE_URL (PG real)")
@pytest.mark.asyncio
async def test_bot_lifecycle_crud_cas_history_survives_delete():
    """Crear → errores claros → editar → CAS ok/conflicto/tope →
    clonado -2/-3 → borrar con historial intacto."""
    sch = f"bots_life_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)

            # ── Crear
            bot = await BotsService.create_bot(
                "coder",
                display_name="Coder",
                soul_md="# SOUL",
                model="deepseek-v4-flash",
                tool_names=["terminal"],
            )
            assert bot["memory_prefix"] == "bots/coder"
            assert bot["ui_meta_rev"] == 0

            # Slug duplicado → error accionable
            with pytest.raises(BotError, match="ya existe"):
                await BotsService.create_bot("coder")

            # ── Editar capacidades (el epoch se dispara arriba)
            upd = await BotsService.update_bot(
                "coder", soul_md="# SOUL v2", skill_allowlist=["planning"]
            )
            assert upd["soul_md"] == "# SOUL v2"
            assert upd["skill_allowlist"] == ["planning"]

            # Desconocido → error claro
            with pytest.raises(BotError, match="no existe"):
                await BotsService.update_bot("fantasma", soul_md="x")

            # ── CAS ui_meta: rev correcta pasa, vieja rechazada
            rev0 = upd["ui_meta_rev"]
            m1 = await BotsService.set_ui_meta("coder", {"avatar": "a"}, expected_rev=rev0)
            assert m1["ui_meta_rev"] == rev0 + 1
            assert m1["ui_meta"] == {"avatar": "a"}

            with pytest.raises(BotError, match="desfasado"):
                await BotsService.set_ui_meta("coder", {"avatar": "b"}, expected_rev=rev0)

            huge = {"blob": "x" * (BOT_UI_META_MAX_BYTES + 1)}
            with pytest.raises(BotError, match="excede"):
                await BotsService.set_ui_meta("coder", huge, expected_rev=m1["ui_meta_rev"])

            # clone_bot eliminado — sin callers de GUI
            # (la GUI clona vía YAML, decisión bots-template-first). No queda
            # ningún test de clonación por convención <base>-2.

            # Roster ordenado
            roster = await BotsService.list_bots()
            assert [b["slug"] for b in roster] == ["coder"]

            # ── Borrado inexistente vs real
            assert await BotsService.delete_bot("ghost") is False
            assert await BotsService.delete_bot("coder") is True

            # las revisiones del borrado sobreviven (+ tombstone tras el update)
            hist = await BotsService.meta_history("coder")
            assert len(hist) == 2 and hist[-1]["tombstone"] is True

            # Purga explícita de historial (nunca automática)
            purged = await BotsService.purge_history("coder")
            assert purged >= 1
            assert await BotsService.get_bot("coder") is None
    finally:
        await drop_schema(sch)


@pytest.mark.skipif(not _url(), reason="requiere DATABASE_URL (PG real)")
@pytest.mark.asyncio
async def test_target_resolution_and_error_messages():
    """Roster en errores (autocorrección), self-block, alias @, peers."""
    sch = f"bots_tgt_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            await BotsService.create_bot("alpha")
            await BotsService.create_bot("beta")

            # Case-insensitive y @ opcional
            t1 = await resolve_target("ALPHA")
            t2 = await resolve_target("@beta")
            assert t1["slug"] == "alpha" and t2["slug"] == "beta"

            # Self-DM bloqueado
            with pytest.raises(BotError, match="ti mismo"):
                await resolve_target("alpha", sender_slug="alpha")

            # Desconocido → roster completo en el mensaje
            with pytest.raises(UnknownTargetError) as ei:
                await resolve_target("ghost")
            msg = str(ei.value)
            assert "@alpha" in msg and "@beta" in msg

            # Alias @ sin principal configurado
            with pytest.raises(UnknownTargetError, match="principal"):
                await resolve_target("@")

            # Cross-machine: fuera de alcance v1 con error explícito
            with pytest.raises(UnknownTargetError, match="fuera de alcance"):
                await resolve_target("spark/researcher")

            # Alias @ resuelve al principal de la instancia inyectada
            reg = BotsRegistry()
            reg.set_principal("alpha")
            t3 = await resolve_target("@", reg=reg)
            assert t3["slug"] == "alpha"

            ro = await reg.roster()
            assert [b["slug"] for b in ro] == ["alpha", "beta"]
            assert await reg.is_bot_managed() is True
    finally:
        await drop_schema(sch)
