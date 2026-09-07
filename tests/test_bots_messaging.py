# tests/test_bots_messaging.py — C7/C8/C12 · T21/T23/T24 + TTL anti-bucle
"""send_to_bot: validaciones accionables, atribución server-side, cap 16k,
claim atómico FIFO y TTL de hops (mejora sobre hermes)."""

import json
import secrets

import pytest

from core import bots_messaging
from core.bots import BotsService
from core.bots_gate import BOT_DM_TOOL_NAME, handle_dm_call, set_active_canonical
from core.bots_messaging import (
    DEFAULT_HOPS,
    MAX_DM_CHARS,
    DmLimitError,
    compact_body,
    hop_scope,
    send_dm,
)
from core.database import (
    bound_schema,
    create_schema,
    create_tables_in_schema,
    drop_schema,
    get_async_session,
)

_pg = pytest.mark.skipif(
    not __import__("os").environ.get("DATABASE_URL"), reason="requiere DATABASE_URL (PG real)"
)


async def _fresh(two_bots=True):
    sch = f"bots_msg_{secrets.token_hex(4)}"
    await create_schema(sch)
    async with bound_schema(sch):
        await create_tables_in_schema(sch)
        if two_bots:
            await BotsService.create_bot("alpha", display_name="Alpha")
            await BotsService.create_bot("beta", display_name="Beta")
    return sch


def test_compact_body():
    assert compact_body("  hola\n\n mundo  ") == "hola mundo"
    assert len(compact_body("x" * 99999)) == MAX_DM_CHARS


@_pg
@pytest.mark.asyncio
async def test_send_dm_validations_and_attribution():
    sch = await _fresh()
    try:
        async with bound_schema(sch):
            # self-block
            with pytest.raises(Exception, match="ti mismo"):
                await send_dm("alpha", "alpha", "hola yo")
            # desconocido con ROSTER en el error
            with pytest.raises(Exception, match="@beta"):
                await send_dm("alpha", "ghost", "hi")
            # peer fuera de alcance v1
            with pytest.raises(Exception, match="fuera de alcance"):
                await send_dm("alpha", "spark/researcher", "hi")
            # cap 16k
            with pytest.raises(DmLimitError, match="cap de"):
                await send_dm("alpha", "beta", "z" * (MAX_DM_CHARS + 1))
            # vacío
            with pytest.raises(Exception, match="vacío"):
                await send_dm("alpha", "beta", "   ")

            ack = await send_dm("alpha", "@beta", "¿estado del build?")
            assert ack["status"] == "sent" and ack["to"] == "beta"

            from sqlalchemy import text

            async with get_async_session() as s:
                row = (
                    (
                        await s.execute(
                            text(
                                "SELECT bot_id, source, from_handle, body, hops FROM pending_turns "
                                "ORDER BY id DESC LIMIT 1"
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
            assert row["source"] == "dm" and row["from_handle"] == "alpha"
            assert row["body"].startswith("Message from 🤖 Alpha (@alpha): ¿estado")
            assert row["hops"] == DEFAULT_HOPS
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_claim_fifo_atomic_and_idempotent():
    sch = await _fresh()
    try:
        async with bound_schema(sch):
            for i in range(3):
                await send_dm("alpha", "beta", f"msg-{i}")
            batch1 = await bots_messaging.claim_pending(limit=2)
            assert [r["body"].split(": ", 1)[1] for r in batch1] == ["msg-0", "msg-1"]
            batch2 = await bots_messaging.claim_pending(limit=5)
            assert [r["body"].split(": ", 1)[1] for r in batch2] == ["msg-2"]
            empty = await bots_messaging.claim_pending(limit=5)
            assert empty == []
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_hop_scope_ttl_chain():
    """Mejora anti-bucle: dentro del despacho los re-envíos decrecen; a 0 muere."""
    sch = await _fresh()
    try:
        async with bound_schema(sch):
            # sin scope: hops frescos
            await send_dm("alpha", "beta", "libre-1")
            # cadena hops=1 → primer re-envío ok(hops=0), segundo muere
            with hop_scope(1):
                await send_dm("alpha", "beta", "hop-last")
                with pytest.raises(DmLimitError, match="TTL"):
                    await send_dm("alpha", "beta", "bucle infinito")
            # scope 0 directamente
            with hop_scope(0):
                with pytest.raises(DmLimitError, match="TTL"):
                    await send_dm("alpha", "beta", "no pasa")
            from sqlalchemy import text

            async with get_async_session() as s:
                hops = [
                    int(r[0])
                    for r in (
                        await s.execute(text("SELECT hops FROM pending_turns ORDER BY id"))
                    ).fetchall()
                ]
            assert hops == [DEFAULT_HOPS, 0]
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_handle_dm_call_regate_and_monkeypatched_flag(monkeypatch):
    """Re-gate: sin canónico activo ni flags ⇒ error estructurado, no envío."""
    sch = await _fresh()
    try:
        async with bound_schema(sch):
            # fuera de canónico: rechaza ANTES de validar target
            ok, out = await handle_dm_call({"target": "beta", "message": "x"}, "ws")
            assert not ok and "canónico" in out

            # flag apagado ⇒ gate cerrado aunque estemos en canónico
            from core import bots_gate

            monkeypatch.setattr(bots_gate, "_flag", lambda name, default=True: False)
            with set_active_canonical("alpha"):
                ok2, out2 = await handle_dm_call({"target": "beta", "message": "x"}, "ws")
                assert not ok2
                monkeypatch.setattr(bots_gate, "_flag", lambda name, default=True: True)

                # dentro de canónico con gates abiertos → envía y acusa recibo
                ok3, out3 = await handle_dm_call({"target": "beta", "message": "ping"}, "ws")
                assert ok3
                payload = json.loads(out3)
                assert payload["status"] == "sent" and payload["to"] == "beta"
            # sanity final directo en BD
            from sqlalchemy import text

            async with get_async_session() as s:
                cnt = (await s.execute(text("SELECT COUNT(*) FROM pending_turns"))).scalar()
            assert cnt >= 1
            assert BOT_DM_TOOL_NAME == "send_to_bot"
    finally:
        await drop_schema(sch)


def test_annotate_mentions_identification_only():
    from core.bots_protocol import annotate_user_mentions

    roster = [{"slug": "beta"}, {"slug": "gamma"}]
    q = "pregúntale a @Beta qué onda, y también a @ghost y @beta"
    out = annotate_user_mentions(q, roster)
    assert "@Beta qué onda" in out
    # beta duplicada se deduplica ⇒ UNA nota; ghost no está en roster
    assert out.count("NO reenviar verbatim") == 1
    notas = out.split("\n\n")[-1]
    assert "@Beta:" in notas and "ghost" not in notas

    # sin roster o sin menciones → intacto byte a byte
    assert annotate_user_mentions(q, []) == q
    plain = "nada de menciones aquí"
    assert annotate_user_mentions(plain, roster) == plain


# ── claim_pending real debe construir el SQL sin explotar ──────────


@pytest.mark.asyncio
async def test_claim_pending_executes_with_all_placeholders_resolved(monkeypatch):
    """Un .format() crudo revienta con KeyError 'stale_interval' porque el
    template mezcla placeholders de format y de replace. La llamada REAL
    debe resolver los 3 placeholders (extra_filter/max_attempts/stale_interval)
    sin excepción y con el SQL final completo."""
    from unittest.mock import AsyncMock

    import core.bots_messaging as bm

    executed: list[str] = []

    class _FakeMappings:
        def all(self):
            return []

    class _FakeResult:
        def mappings(self):
            return _FakeMappings()

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        execute = AsyncMock(
            side_effect=lambda sql, params=None: executed.append(str(sql)) or _FakeResult()
        )

    monkeypatch.setattr(bm, "get_async_session", lambda: _FakeSession())

    rows = await bm.claim_pending(limit=3, bot_id=42)

    assert rows == []
    assert len(executed) == 1, "debe ejecutar exactamente el UPDATE de claim"
    sql = executed[0]
    assert (
        "{extra_filter}" not in sql
        and "{max_attempts}" not in sql
        and "{stale_interval}" not in sql
    )
    assert f"attempts < {bm.DEAD_LETTER_ATTEMPTS}" in sql
    assert "INTERVAL '1 minute'" in sql and str(bm.STALE_CLAIM_MINUTES) in sql
    assert "AND bot_id = :b" in sql, "el filtro por bot debe inyectarse"


@pytest.mark.asyncio
async def test_ttl_check_runs_before_session(monkeypatch):
    """Un TTL agotado NO debe loguearse como ERROR falso ("Error en
    sesión asíncrona"): el check vive FUERA del context manager de
    sesión. Contrato: el DmLimitError sale limpio ANTES de abrir la sesión."""
    from unittest.mock import AsyncMock

    entered: list[int] = []

    class _BoomCM:
        async def __aenter__(self):
            entered.append(1)
            raise AssertionError("la sesión no debía abrirse")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(bots_messaging, "get_async_session", lambda: _BoomCM())
    monkeypatch.setattr(
        bots_messaging,
        "resolve_target",
        AsyncMock(return_value={"slug": "bravo"}),
    )
    with pytest.raises(DmLimitError):
        with hop_scope(0):
            await send_dm("alfa", "bravo", "cadena agotada")
    assert entered == [], "el check TTL debe correr ANTES de abrir la sesión"


@pytest.mark.asyncio
async def test_peers_check_runs_before_session(monkeypatch):
    """El check de peers cross-machine también es pre-sesión (misma categoría)."""
    from unittest.mock import AsyncMock

    entered: list[int] = []

    class _BoomCM:
        async def __aenter__(self):
            entered.append(1)
            raise AssertionError("la sesión no debía abrirse")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(bots_messaging, "get_async_session", lambda: _BoomCM())
    monkeypatch.setattr(
        bots_messaging,
        "resolve_target",
        AsyncMock(return_value={"slug": "bravo"}),
    )
    from core.bots_registry import UnknownTargetError

    with pytest.raises(UnknownTargetError):
        await send_dm("alfa", "peer/bravo", "cross-machine")
    assert entered == [], "el check de peers debe correr ANTES de abrir la sesión"
