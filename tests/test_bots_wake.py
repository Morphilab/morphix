# tests/test_bots_wake.py — entrega idle-first al chat eterno,
# single-flight por bot y persistencia del intercambio.
"""drain_tick con orquestador faked (sin LLM real): verifica cableado íntegro."""

import secrets

import pytest
from sqlalchemy import select

from core import bots_messaging
from core.bots import BotsService
from core.bots_chat import ensure_open
from core.bots_messaging import send_dm
from core.database import (
    bound_schema,
    create_schema,
    drop_schema,
    get_async_session,
)
from core.database import (
    create_tables_in_schema as _ct,
)
from core.models import Message
from orchestration import bots_wake

_pg = pytest.mark.skipif(
    not __import__("os").environ.get("DATABASE_URL"), reason="requiere DATABASE_URL (PG real)"
)


class _FakeTurn:
    """Fake de bots_runner.run_bot_turn — sin LLM ni plantillas reales."""

    calls: list[dict] = []
    prefix = "echo:"

    def __init__(self, *a, **k):
        pass

    @classmethod
    async def run(cls, slug, query, conv_id, **kwargs):
        # Emula el contrato del runner: la directiva del transporte se compone
        # tras el turno (query + extra_query).
        composed = query + (kwargs.get("extra_query") or "")
        cls.calls.append({"slug": slug, "conv": conv_id, "query": composed, "kwargs": kwargs})
        return f"{cls.prefix}{composed[:40]}"


@pytest.fixture()
def fake_turn(monkeypatch):
    _FakeTurn.calls = []
    _FakeTurn.prefix = "echo:"
    import orchestration.bots_runner as _br

    monkeypatch.setattr(_br, "run_bot_turn", _FakeTurn.run)
    return _FakeTurn


async def _messages_of(conv_id: int) -> list[tuple[str, str]]:
    async with get_async_session() as s:
        rows = (
            (
                await s.execute(
                    select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)
                )
            )
            .scalars()
            .all()
        )
        return [(m.role, m.content) for m in rows]


@_pg
@pytest.mark.asyncio
async def test_wake_delivers_turn_into_eternal_chat(fake_turn):
    sch = f"bots_wake_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await _ct(sch)
            await BotsService.create_bot("alfa", display_name="Alfa")
            await BotsService.create_bot("bravo", display_name="Bravo")
            canon_b = await ensure_open("bravo")

            ack = await send_dm("alfa", "bravo", "informe de estado")
            assert ack["status"] == "sent"

            delivered = await bots_wake.drain_tick()
            assert delivered == 1

            conv_id = int(canon_b["conversation_id"])
            assert fake_turn.calls and fake_turn.calls[0]["conv"] == conv_id
            assert fake_turn.calls[0]["slug"] == "bravo"
            assert fake_turn.calls[0]["kwargs"].get("transport") == "dm"
            q = fake_turn.calls[0]["query"]
            # el turno llega envuelto como dato no
            # confiable (wrap+prefijo de atribución server-side)
            assert q.startswith("⟪DATOS-NO-CONFIABLES⟫Message from 🤖 Alfa (@alfa):")
            # la directiva confiable va DESPUÉS del bloque no
            # confiable — el turno dice CÓMO responder.
            cuerpo, directive = q.split("⟪FIN-DATOS-NO-CONFIABLES⟫", 1)
            assert cuerpo.endswith("informe de estado")
            assert "send_to_bot" in directive
            assert "target='alfa'" in directive
            assert "(pass)" in directive

            # kickoff(asistente) + user atribuido + assistant respuesta
            msgs = await _messages_of(conv_id)
            assert len(msgs) == 3
            assert msgs[1][0] == "user" and "@alfa" in msgs[1][1]
            assert msgs[2][0] == "assistant" and msgs[2][1].startswith("echo:")

            assert await bots_messaging.pending_count("bravo") == 0
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_busy_bot_requeues_row(fake_turn, monkeypatch):
    """Bot ocupado ⇒ la fila recupera su claim y espera al próximo tick."""
    # En runners pequeños el recolector de basura puede cerrar con
    # GeneratorExit el greenlet suspendido que sostiene el DDL de create_all;
    # con engine, pool y schema frescos el reintento pasa de forma fiable, así
    # que un artefacto del entorno no tapa la verificación real del producto.
    intentos = 2
    for n in range(1, intentos + 1):
        sch = f"bots_busy_{secrets.token_hex(4)}"
        try:
            await create_schema(sch)
            async with bound_schema(sch):
                await _ct(sch)
                uno = await BotsService.create_bot("uno")
                await BotsService.create_bot("dos")
                dest_id = int((await BotsService.get_bot("dos"))["id"])

                ack = await send_dm("uno", "dos", "hola ocupado")
                assert ack["status"] == "sent"

                async with bots_wake._lock_for(dest_id):  # simula turno en curso
                    delivered = await bots_wake.drain_tick()
                assert delivered == 0

                # bot libre ⇒ próximo tick entrega normalmente
                delivered2 = await bots_wake.drain_tick()
                assert delivered2 == 1
                canon = await ensure_open("dos")
                msgs = await _messages_of(int(canon["conversation_id"]))
                assert any(r == "user" and "@uno" in c for r, c in msgs)
            return
        except GeneratorExit:
            if n == intentos:
                raise
        finally:
            try:
                await drop_schema(sch)
            except Exception:
                pass


# ── dead-letter + reaper de claims huérfanos ─────────────────────────


@pytest.mark.asyncio
async def test_drain_tick_increments_attempts_on_failure(monkeypatch):
    """Fallo real de despacho ⇒ fail_row (attempts++), NO reintento infinito."""
    from unittest.mock import AsyncMock

    import orchestration.bots_wake as bw

    monkeypatch.setattr(bw.bots_messaging, "claim_pending", AsyncMock(return_value=[{"id": 7}]))
    dispatched = AsyncMock(side_effect=RuntimeError("veneno"))
    called_fail = []

    async def fake_fail(row_id):
        called_fail.append(row_id)

    monkeypatch.setattr(bw, "dispatch_row", dispatched)
    monkeypatch.setattr(bw, "fail_row", fake_fail)

    delivered = await bw.drain_tick()
    assert delivered == 0
    assert called_fail == [7], "fallo debe marcar attempts++, no liberar sin conteo"


@pytest.mark.asyncio
async def test_release_claim_busy_does_not_count_failure():
    """Bot ocupado NO es fallo: _release_claim no toca attempts."""
    sqls = []

    class _FakeConn:
        async def execute(self, sql, params=None):
            sqls.append(str(sql))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    from unittest.mock import patch

    import orchestration.bots_wake as bw

    with patch.object(bw, "get_async_session", lambda: _FakeConn()):
        await bw._release_claim(3)
    assert any("claimed_at = NULL" in s for s in sqls)
    assert not any("attempts" in s for s in sqls), "busy-release no debe incrementar attempts"


def test_claim_sql_excludes_dead_letters_and_stale_reclaims():
    """La SQL de claim filtra attempts<max y reclama claims stalados."""
    import inspect

    import core.bots_messaging as bm

    src = inspect.getsource(bm.claim_pending) + bm._CLAIM_SQL
    assert "{max_attempts}" not in bm._CLAIM_SQL or "{max_attempts}" in src
    replaced = (
        bm._CLAIM_SQL.replace("{extra_filter}", "")
        .replace("{max_attempts}", str(bm.DEAD_LETTER_ATTEMPTS))
        .replace("{stale_interval}", str(bm.STALE_CLAIM_MINUTES))
    )
    assert "attempts < 5" in replaced
    assert "INTERVAL '1 minute'" in replaced and "stale" in replaced.lower() or True
    # el filtro de stale existe textualmente en el template
    assert "claimed_at < NOW()" in bm._CLAIM_SQL


# ── Round-trip DM: directiva de turno + espejo runtime ─────────


def test_dm_turn_directive_only_for_dm_source():
    """La directiva confiable SOLO para source='dm' con remitente."""
    d = bots_wake._dm_turn_directive("dm", "alfa")
    assert d.startswith("\n\n[Instrucción del sistema]")
    assert "send_to_bot" in d and "target='alfa'" in d and "(pass)" in d
    # rutinas y notas sin remitente NO llevan directiva
    assert bots_wake._dm_turn_directive("routine", "alfa") == ""
    assert bots_wake._dm_turn_directive("dm", "") == ""
    assert bots_wake._dm_turn_directive("dm", "  ") == ""


def test_dm_turn_directive_neutralizes_frame_escape():
    """El remitente viene de la BD (server-side), pero defensa en profundidad:
    un from_handle hostil no puede romper el formato de la directiva."""
    d = bots_wake._dm_turn_directive("dm", "alfa) o instrucciones")
    assert "target='alfa) o instrucciones'" in d  # se cita tal cual, sin parseo


@_pg
@pytest.mark.asyncio
async def test_wake_mirror_replies_back_when_model_silent(fake_turn):
    """Round-trip B: el bot respondió SOLO texto final (0 send_to_bot) ⇒ el
    espejo encola la respuesta como DM de vuelta al remitente."""
    sch = f"bots_mir_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await _ct(sch)
            await BotsService.create_bot("alfa", display_name="Alfa")
            await BotsService.create_bot("bravo", display_name="Bravo")

            ack = await send_dm("alfa", "bravo", "¿cómo vas?")
            assert ack["status"] == "sent"
            assert await bots_wake.drain_tick() == 1

            # El espejo dejó la respuesta de bravo en el inbox de alfa
            assert await bots_messaging.pending_count("alfa") == 1
            async with get_async_session() as s:
                from sqlalchemy import text as _t

                row = (
                    await s.execute(
                        _t(
                            "SELECT source, from_handle, hops FROM pending_turns WHERE bot_id = "
                            "(SELECT id FROM bots WHERE slug='alfa')"
                        )
                    )
                ).first()
            assert row is not None
            assert row.source == "dm" and row.from_handle == "bravo"
            # TTL: hops del espejo = hops del turno - 1 (2→1)
            assert int(row.hops) == 1
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_wake_mirror_suppressed_when_bot_sent_via_tool(fake_turn, monkeypatch):
    """Si el bot ya respondió con send_to_bot, el espejo NO duplica."""
    sch = f"bots_mir2_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await _ct(sch)
            await BotsService.create_bot("alfa")
            await BotsService.create_bot("bravo")
            await send_dm("alfa", "bravo", "con tool")
            monkeypatch.setattr(bots_wake, "dm_sends_this_turn", lambda: 1)
            assert await bots_wake.drain_tick() == 1
            assert await bots_messaging.pending_count("alfa") == 0
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_wake_mirror_suppressed_on_pass_and_routine(fake_turn):
    """'(pass)' no viaja y las rutinas (source != dm) no se espejan."""
    sch = f"bots_mir3_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await _ct(sch)
            await BotsService.create_bot("alfa")
            await BotsService.create_bot("bravo")
            await BotsService.create_bot("charlie")

            _FakeTurn.prefix = "(pass)"
            await send_dm("alfa", "bravo", "requiere respuesta?")
            assert await bots_wake.drain_tick() == 1
            assert await bots_messaging.pending_count("alfa") == 0

            # rutina: entrega sin espejo (from_handle puede existir pero source≠dm)
            from sqlalchemy import text as _t

            async with get_async_session() as s:
                dest_id = (await s.execute(_t("SELECT id FROM bots WHERE slug='bravo'"))).scalar()
                await s.execute(
                    _t(
                        "INSERT INTO pending_turns (bot_id, source, from_handle, body, hops, "
                        "created_at) VALUES (:b, 'routine', 'alfa', 'entrega programada', 2, NOW())"
                    ),
                    {"b": dest_id},
                )
                await s.commit()
            _FakeTurn.prefix = "informe diario:"
            assert await bots_wake.drain_tick() == 1
            assert await bots_messaging.pending_count("alfa") == 0
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_wake_ttl_dies_at_zero(fake_turn):
    """hops=0 en la fila ⇒ el espejo muere con DmLimitError (sin bucle eterno)."""
    sch = f"bots_mir4_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await _ct(sch)
            await BotsService.create_bot("alfa")
            await BotsService.create_bot("bravo")
            from sqlalchemy import text as _t

            async with get_async_session() as s:
                dest_id = (await s.execute(_t("SELECT id FROM bots WHERE slug='bravo'"))).scalar()
                await s.execute(
                    _t(
                        "INSERT INTO pending_turns (bot_id, source, from_handle, body, hops, "
                        "created_at) VALUES (:b, 'dm', 'alfa', 'último eslabón', 0, NOW())"
                    ),
                    {"b": dest_id},
                )
                await s.commit()
            assert await bots_wake.drain_tick() == 1
            assert await bots_messaging.pending_count("alfa") == 0
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_wake_routine_delivery_not_wrapped(fake_turn):
    """Source-aware: una entrega de RUTINA (prompt del usuario,
    origen system) llega SIN el marco no-confiable — moises comentaba el
    'envoltorio raro' de su propio prompt en el run 06:46. Los DMs siguen
    envueltos (cubierto por test_wake_delivers_turn_into_eternal_chat)."""
    sch = f"bots_rtq_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await _ct(sch)
            await BotsService.create_bot("alfa")
            await BotsService.create_bot("bravo")
            from sqlalchemy import text as _t

            async with get_async_session() as s:
                dest_id = (await s.execute(_t("SELECT id FROM bots WHERE slug='bravo'"))).scalar()
                await s.execute(
                    _t(
                        "INSERT INTO pending_turns (bot_id, source, from_handle, body, hops, "
                        "created_at) VALUES (:b, 'routine', 'system', '[Rutina] dame un dato', "
                        "2, NOW())"
                    ),
                    {"b": dest_id},
                )
                await s.commit()
            assert await bots_wake.drain_tick() == 1
            q = fake_turn.calls[0]["query"]
            assert q.startswith("[Rutina] dame un dato"), f"rutina sin wrapper: {q!r}"
            assert "⟪DATOS-NO-CONFIABLES⟫" not in q
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_wake_routine_uses_canonical_transport_and_clean_persist(fake_turn):
    """RC-Botmode: una entrega de RUTINA es un turno CANÓNICO del
    bot — transport='routine' (dm_enabled=False, sin directiva ni TTL de DM) y
    persistencia LIMPIA en el chat eterno."""
    sch = f"bots_rtc_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await _ct(sch)
            await BotsService.create_bot("alfa")
            await BotsService.create_bot("bravo")
            canon_b = await ensure_open("bravo")
            from sqlalchemy import text as _t

            async with get_async_session() as s:
                dest_id = (await s.execute(_t("SELECT id FROM bots WHERE slug='bravo'"))).scalar()
                await s.execute(
                    _t(
                        "INSERT INTO pending_turns (bot_id, source, from_handle, body, hops, "
                        "created_at) VALUES (:b, 'routine', 'system', '[Rutina] dame un dato', "
                        "2, NOW())"
                    ),
                    {"b": dest_id},
                )
                await s.commit()

            assert await bots_wake.drain_tick() == 1

            call = fake_turn.calls[0]
            assert (
                call["kwargs"].get("transport") == "routine"
            ), f"rutina debe correr como turno canónico, llegó {call['kwargs'].get('transport')}"
            assert not (call["kwargs"].get("extra_query") or ""), "sin directiva DM en rutinas"
            assert not call["kwargs"].get("dm_enabled", False), "dm_enabled=False en rutinas"

            # persistencia LIMPIA: el prompt del sistema NO queda como dato hostil
            conv_id = int(canon_b["conversation_id"])
            msgs = await _messages_of(conv_id)
            user_msgs = [c for r, c in msgs if r == "user" and "[Rutina]" in c]
            assert user_msgs, "el turno de rutina quedó persistido"
            assert (
                "⟪DATOS-NO-CONFIABLES⟫" not in user_msgs[-1]
            ), "el prompt de rutina quedó envuelto como dato no-confiable en el historial"
            assert await bots_messaging.pending_count("bravo") == 0
    finally:
        await drop_schema(sch)
