# tests/test_bots_epoch.py — C6/T16/T17 + T13/T25
"""Fingerprint de capacidades, rebuild ×1, fail-closed, snapshot memoria y
sección de protocolo determinista."""

import secrets

import pytest

from core import bots_epoch
from core.bots import BotsService
from core.bots_chat import ensure_open
from core.bots_epoch import compute_fingerprint, refresh_if_stale
from core.bots_memory import DEFAULT_BUDGET_CHARS, save_section, snapshot
from core.bots_protocol import PROTOCOL_HEADING, section_text
from core.database import (
    bound_schema,
    create_schema,
    create_tables_in_schema,
    drop_schema,
)

_pg = pytest.mark.skipif(
    not __import__("os").environ.get("DATABASE_URL"), reason="requiere DATABASE_URL (PG real)"
)


def _bot(model="m1", tools=("a", "b"), skills=("s1",), soul="# SOUL", slug="alpha"):
    return {
        "provider": None,
        "model": model,
        "temperature": 0.7,
        "tool_names": list(tools),
        "skill_allowlist": list(skills),
        "soul_md": soul,
        "slug": slug,
    }


def test_fingerprint_stable_and_orderless():
    base = compute_fingerprint(_bot(), [{"slug": "b"}, {"slug": "a"}])
    reorder = compute_fingerprint(_bot(tools=("b", "a")), [{"slug": "a"}, {"slug": "b"}])
    assert base == reorder and len(base) == 12

    assert compute_fingerprint(_bot(model="m2")) != base
    assert compute_fingerprint(_bot(soul="# SOUL v2")) != base


@_pg
@pytest.mark.asyncio
async def test_epoch_first_turn_then_rebuild_once_on_drift():
    """Primer turno crea stamp; cambio SOLO cuando capacidades cambian."""
    sch = f"bots_ep_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            await BotsService.create_bot("ep1", soul_md="# v0")
            canonical = await ensure_open("ep1")
            cid = canonical["conversation_id"]

            # primera verificación tras acuñar: no hay stamp ⇒ UN rebuild
            # (el turno fundacional arma su prompt y estampa el epoch)
            first = await refresh_if_stale("ep1")
            assert first["rebuild"] is True and first["conversation_id"] == cid
            from core.models import Conversation

            async with await _sess() as s:
                row = await s.get(Conversation, cid)
                stored = row.capability_epoch
            assert stored and len(stored) == 12 and stored == first["epoch"]

            # mismo contenido ⇒ sin drift extra
            steady = await refresh_if_stale("ep1")
            assert steady["rebuild"] is False

            # drift real: cambia el modelo
            await BotsService.update_bot("ep1", model="deepseek-v4-flash")
            after = await refresh_if_stale("ep1")
            assert after["rebuild"] is True and after["epoch"] != stored

            # segundo chequeo idéntico NO reconstruye (solo UNA vez)
            again = await refresh_if_stale("ep1")
            assert again["rebuild"] is False and again["epoch"] == after["epoch"]
    finally:
        await drop_schema(sch)


async def _sess():
    from core.database import get_async_session

    return get_async_session()


@_pg
@pytest.mark.asyncio
async def test_epoch_fail_closed_when_probe_breaks(monkeypatch):
    """Si el cómputo falla, fail-closed — no stamp, no rebuild flag."""
    sch = f"bots_efc_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            await BotsService.create_bot("efc")
            await ensure_open("efc")

            monkeypatch.setattr(
                bots_epoch,
                "compute_fingerprint",
                lambda *_: (_ for _ in ()).throw(RuntimeError("probe roto")),
            )
            res = await refresh_if_stale("efc")
            assert res["rebuild"] is False and res["epoch"] == ""
    finally:
        await drop_schema(sch)


def test_bot_memory_snapshot_budget_and_sections(tmp_path, monkeypatch):
    ws = "ws_mem"

    class _Paths:
        def memory_dir(self, workspace: str):
            return tmp_path / "mem" / workspace

    monkeypatch.setattr("core.path_resolver.paths", _Paths())
    monkeypatch.setattr("core.bots_memory.paths", _Paths())
    monkeypatch.setattr("core.bots_memory._active_workspace", lambda: ws)

    big = "x" * (DEFAULT_BUDGET_CHARS * 3)
    save_section("zed", which="memory", content=big, workspace=ws)
    save_section("zed", which="user", content=" usuario límite ", workspace=ws)

    snap = snapshot("zed", workspace=ws)
    # MEMORY agota el presupuesto completo → USER no entra en este snapshot
    assert snap.startswith("### MEMORY.md")
    assert "USER.md" not in snap

    # con contenido pequeño entran AMBAS secciones, orden fijo
    save_section("paz", which="memory", content="nota-1", workspace=ws)
    save_section("paz", which="user", content="user-1", workspace=ws)
    both = snapshot("paz", workspace=ws)
    assert "nota-1" in both and "user-1" in both
    assert both.index("MEMORY.md") < both.index("USER.md")

    empty = snapshot("ghost-bot", workspace=ws)
    assert empty == ""


def test_protocol_section_deterministic_and_gates():
    roster = [
        {"slug": "beta", "display_name": "Beta"},
        {"slug": "gamma", "display_name": ""},
    ]
    a = section_text("Alpha", roster, self_slug="alpha")
    b = section_text("Alpha", list(reversed(roster)), self_slug="alpha")
    assert a == b and PROTOCOL_HEADING in a
    assert "@beta" in a and "@gamma" in a
    assert "\n- @alpha" not in a, "el propio bot nunca lista su slug como compañero"
    # Round-trip: la sección deja EXPLÍCITO que el texto final no
    # viaja solo — un DM entrante se responde con la herramienta (2/2 corridas
    # con respuesta muerta en el chat eterno del receptor).
    assert "Message from 🤖" in a and "send_to_bot" in a
    assert "NO le llega solo" in a
    # single-bot: la sección es responsabilidad del caller (roster≥2); texto
    # aún válido pero marcando soledad:
    solo = section_text("Solo", [{"slug": "solo"}], self_slug="solo")
    assert "no hay otros bots" in solo


@_pg
@pytest.mark.asyncio
async def test_epoch_fail_closed_si_roster_falla(monkeypatch):
    """Fallo de list_bots DEBE propagar al handler fail-closed
    (rebuild=False): tragarse el error con roster=[] produce un fingerprint
    sin eje de protocolo ≠ almacenado → rebuild espurio del prompt cacheado."""
    sch = f"bots_efc2_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            await BotsService.create_bot("efc2")
            await BotsService.create_bot("peer2")
            await ensure_open("efc2")
            res_first = await refresh_if_stale("efc2")
            assert res_first["rebuild"] is True  # primer turno: stampéa
            res_ok = await refresh_if_stale("efc2")
            assert res_ok["rebuild"] is False  # ya estable

            async def _boom(*a, **k):
                raise RuntimeError("BD caída")

            monkeypatch.setattr(BotsService, "list_bots", _boom)
            res = await refresh_if_stale("efc2")
            assert res["rebuild"] is False, (
                "INT-M2: fallo de roster computa fingerprint con lista vacía "
                "→ invalidación espuria del caché; debía ir fail-closed"
            )
    finally:
        await drop_schema(sch)
