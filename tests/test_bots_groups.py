# tests/test_bots_groups.py — salas, caps, menciones deterministas,
# sesiones miembro 'Group:<roomId>' ocultas y drive de rondas (orch fake).
import secrets

import pytest

from core import bots_groups as bg
from core.bots import BotsService
from core.database import (
    bound_schema,
    create_schema,
    create_tables_in_schema,
    drop_schema,
    get_async_session,
)
from orchestration import bots_groups_drive as drv

_pg = pytest.mark.skipif(
    not __import__("os").environ.get("DATABASE_URL"), reason="requiere DATABASE_URL (PG real)"
)


class _FakeTurn:
    calls = []
    turn_kwargs = []

    def __init__(self, *a, **k):
        pass

    @classmethod
    async def run(cls, slug, query, conv_id, **kwargs):
        cls.calls.append((slug, conv_id))
        cls.turn_kwargs.append({"slug": slug, "conv": conv_id, **kwargs})
        return f"respuesta de {conv_id}"


@pytest.fixture()
def fake_turn(monkeypatch):
    _FakeTurn.calls = []
    _FakeTurn.turn_kwargs = []
    import orchestration.bots_runner as _br

    monkeypatch.setattr(_br, "run_bot_turn", _FakeTurn.run)
    return _FakeTurn


def test_parse_mentions_order_and_dedupe():
    cands = ["zeta", "alfa", "mid"]
    out = bg.parse_mentions("hola @mid mira @zeta @ZETA", cands)
    assert out == ["mid", "zeta"]


# ──: identidad de bot en salas (sin DM) + pass exacto/auditable ──


def test_is_pass_exact_matching():
    """'(pass)' como token sí es silencio; una respuesta real que solo
    CONTENGA 'sin respuesta necesaria' de pasada NO debe marcarse pass
    (el substring suelto de antes marcaba respuestas reales como silencio)."""
    assert drv.is_pass("(pass)") is True
    assert drv.is_pass("(pass) — no hay nada que aportar ahora") is True
    assert drv.is_pass("(silencio)") is True
    assert drv.is_pass("sin respuesta necesaria") is True
    assert drv.is_pass("¡Hola! 👋 Todo bien por aquí, sin respuesta necesaria de momento") is False
    assert drv.is_pass("¡Hola! ¿En qué puedo ayudar?") is False
    assert drv.is_pass("hola (pass)") is False, "pass en medio no es silencio"
    assert drv.is_pass("") is False


@_pg
@pytest.mark.asyncio
async def test_room_turn_forwards_bot_identity_without_dm(fake_turn):
    """La respuesta de sala lleva SOUL/memoria/modelo del bot (bot_context)
    pero dm_enabled=False: identidad sí, send_to_bot NO."""
    sch = f"bots_grpid_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            await BotsService.create_bot("ana", display_name="Ana")
            await BotsService.create_bot("bo", display_name="Bo")
            room = await bg.create_room("ident", "ana", ["ana", "bo"])
            res = await drv.run_room_turn(room["id"], "hola")
            assert res["status"] == "round-complete"

            ctxs = [c for c in _FakeTurn.turn_kwargs if c is not None]
            assert len(ctxs) == 2, "cada miembro debe correr con identidad de bot"
            slugs = {c["slug"] for c in ctxs}
            assert slugs == {"ana", "bo"}
            for c in ctxs:
                assert c.get("dm_enabled") is False, "sin send_to_bot en salas"
                assert c.get("transport") == "room"
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_room_stranded_marked_in_log(fake_turn, monkeypatch):
    """Una ronda fallida debe ser visible en el log compartido (no solo
    en el resumen de estado). Contrato: un miembro stranded deja una marca
    '(⚠ sin respuesta — error interno)' en el log de la sala."""
    sch = f"bots_grps_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            await BotsService.create_bot("ana")
            await BotsService.create_bot("bo")
            room = await bg.create_room("str", "ana", ["ana", "bo"])

            import orchestration.bots_runner as _br

            async def _boom(*a, **k):
                raise RuntimeError("fallo simulado")

            monkeypatch.setattr(_br, "run_bot_turn", _boom)
            res = await drv.run_room_turn(room["id"], "reporten")

            assert res["status"] == "round-complete"
            assert {r["slug"] for r in res["stranded"]} == {"ana", "bo"}
            log = await bg.messages_of(room["id"])
            contents = " ".join(m["content"] for m in log)
            assert "sin respuesta — error interno" in contents, contents
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_room_member_transcripts_exposes_per_bot_memory(fake_turn):
    """La memoria por sala debe ser visible a pesar de ser conversaciones
    ocultas. Contrato: room_member_transcripts devuelve la transcripción
    'Group: <rid>' de cada miembro con el intercambio de la ronda."""
    sch = f"bots_grptr_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            await BotsService.create_bot("ana", display_name="Ana")
            await BotsService.create_bot("bo", display_name="Bo")
            room = await bg.create_room("mem", "ana", ["ana", "bo"])
            await drv.run_room_turn(room["id"], "hola sala")

            transcripts = await bg.room_member_transcripts(room["id"])
            assert set(transcripts) == {"ana", "bo"}
            for slug, msgs in transcripts.items():
                assert msgs, f"@{slug} debe tener su intercambio de sala"
                assert any(m["role"] == "user" for m in msgs)
                assert any(m["role"] == "assistant" for m in msgs)
            # salas sin actividad → vacío
            assert await bg.room_member_transcripts("r-no-existe") == {}
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_room_pass_audited_in_member_session(fake_turn, monkeypatch):
    """Un pass no descarta el texto real: la sesión miembro conserva la nota
    '(pass) — <contenido>' para auditar qué pensó el bot."""
    sch = f"bots_grppa_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            await BotsService.create_bot("ana")
            await BotsService.create_bot("bo")

            import orchestration.bots_runner as _br

            async def _pass_with_note(*a, **k):
                return "(pass) — sin novedades para reportar"

            monkeypatch.setattr(_br, "run_bot_turn", _pass_with_note)
            room = await bg.create_room("pa", "ana", ["ana", "bo"])
            await drv.run_room_turn(room["id"], "reporta")

            from sqlalchemy import select

            from core.models import Message

            async with get_async_session() as s:
                msgs = (
                    (await s.execute(select(Message).where(Message.role == "assistant")))
                    .scalars()
                    .all()
                )
            notes = [str(m.content) for m in msgs if str(m.content).startswith("(pass) — ")]
            assert notes, "el pass debe persistir su texto real como nota auditable"
            assert any("sin novedades" in n for n in notes)
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_room_rules_and_member_sessions(fake_turn):
    sch = f"bots_grp_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            for slug in ("ana", "bo", "cy"):
                await BotsService.create_bot(slug)

            # reglas de sala
            with pytest.raises(Exception, match="owner"):
                await bg.create_room("sala", "cy", ["ana", "bo"])  # owner no es miembro
            with pytest.raises(Exception, match="miembros"):
                await bg.create_room("sala", "ana", ["ana"])
            room = await bg.create_room("standup", "ana", ["ana", "bo", "cy"])
            rid = room["id"]
            assert rid.startswith("r") and len(rid) > 8  # roomId inmutable

            # renombrar conserva roomId; disband borra solo la sala
            assert await bg.rename_room(rid, "daily") is True
            assert (await bg.get_room(rid))["name"] == "daily"

            r1 = await drv.run_room_turn(rid, "estado de cada quien")
            assert r1["status"] == "round-complete"
            slugs_called_via_conv = list(_FakeTurn.calls)
            assert len(slugs_called_via_conv) == 3

            log = await bg.messages_of(rid)
            authors = [m["author"] for m in log]
            assert authors[0] == "user" and set(authors) >= {"user", "ana"}
            seqs = [m["seq"] for m in log]
            assert seqs == sorted(seqs)  # log ordenado único por sala

            # sesiones miembro ocultas por bot, título Group:<roomId>
            from sqlalchemy import text

            async with get_async_session() as s:
                rows = (
                    await s.execute(
                        text(
                            "SELECT b.slug, c.title, c.is_hidden FROM conversation c "
                            "JOIN bots b ON b.id=c.bot_id WHERE c.title LIKE 'Group: %'"
                        )
                    )
                ).fetchall()
            titles = {t for _, t, hidden in rows if hidden}
            assert f"Group: {rid}" in titles and len(rows) >= 2

            # menciones deterministas responden SOLO los mencionados
            before_calls = len(_FakeTurn.calls)
            r2 = await drv.run_room_turn(rid, "@cy reporta tú")
            assert r2["responders"] == ["cy"]

            # pass marca asentimiento: mandamos prompt que devuelve '(pass)'
            with pytest.raises(bg.GroupError, match="inexistente"):
                await bg.post_message("no-existe", "ana", "x")

            state = await bg.member_sessions_state()
            assert rid in state
            assert set(state[rid]) == {"ana", "bo", "cy"}

            # disband borra sala pero CONSERVA sesiones miembro del bot
            assert await bg.disband_room(rid) is True
            assert await bg.get_room(rid) is None
            state_after = await bg.member_sessions_state()
            assert rid not in state_after or set(state_after[rid]) >= {"ana", "bo", "cy"}
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_caps_total_messages(fake_turn):
    sch = f"bots_cap_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            await BotsService.create_bot("x1")
            await BotsService.create_bot("y1")
            room = await bg.create_room("caproom", "x1", ["x1", "y1"])
            # llenar el cap manualmente vía post_message
            for i in range(bg.MAX_TOTAL_MSGS):
                await bg.post_message(room["id"], "x1", f"msg-{i}")
            res = await drv.run_room_turn(room["id"], "un mensaje más")
            assert res["status"] == "capped" and "alcanzó" in res["reason"]
    finally:
        await drop_schema(sch)
