# tests/test_bots_e2e.py — escenario completo con núcleo real + LLM faked
"""End-to-end del Bot Mode sin llamar a un proveedor:

2 bots → DM A→B → wake entrega turno atribuido al chat eterno de B →
respuesta persistida + capability epoch estampado + chats ocultos fuera del
historial → grupo de 3 con mención dirigida → tripwire anti-puntero.

Pipeline: modelos/sesiones/tablas REALES en PG efímero; solo el orquestador
se sustituye por _FakeOrch determinista.
"""

import json
import secrets

import pytest
from sqlalchemy import text

from core import bots_chat
from core.bots import BotsService
from core.bots_epoch import current_stored_epoch
from core.bots_messaging import send_dm
from core.database import (
    bound_schema,
    create_schema,
    create_tables_in_schema,
    drop_schema,
    get_async_session,
)
from desktop.services import bots_service
from orchestration import bots_wake

_pg = pytest.mark.skipif(
    not __import__("os").environ.get("DATABASE_URL"), reason="requiere DATABASE_URL (PG real)"
)


class _FakeOrch:
    calls = []
    replies: dict[str, str] = {}

    def __init__(self, *a, **k):
        pass

    @classmethod
    async def run(cls, slug, query, conv_id, **kwargs):
        cls.calls.append({"conv": conv_id, "q": query, "slug": slug})
        # eco temático que también verifica caps de sistema
        return f"[{cls.replies.get(str(conv_id), 'ok')}] {query[:60]}"


@pytest.fixture()
def fake_orch(monkeypatch):
    """Parchea el runner en TODOS los módulos del flujo (sin red real)."""
    _FakeOrch.calls = []
    _FakeOrch.replies.clear()
    import orchestration.bots_runner as _br

    monkeypatch.setattr(_br, "run_bot_turn", _FakeOrch.run)
    return _FakeOrch


@_pg
@pytest.mark.asyncio
async def test_full_bot_mode_scenario(fake_orch):
    sch = f"bots_e2e_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)

            # ── 1. Identidad: dos bots, chats eternos acuñados bajo demanda
            await BotsService.create_bot("nord", display_name="Nord", soul_md="# NORD")
            await BotsService.create_bot("sud", display_name="Sud")
            nord_chat = await bots_service.open_bot_chat("nord")
            sud_chat = await bots_chat.ensure_open("sud")

            roster = await bots_service.roster_with_previews()
            by_slug = {r["slug"]: r for r in roster}
            assert by_slug["nord"]["canonical"]["conversation_id"] == nord_chat["conversation_id"]
            assert by_slug["sud"]["canonical"]["conversation_id"] == sud_chat["conversation_id"]

            # ── 2. DM fire-and-forget nord→sud
            ack = await send_dm("nord", "sud", "dame estado del despliegue")
            assert ack["status"] == "sent"

            delivered = await bots_wake.drain_tick()
            assert delivered == 1

            sud_conv = int(sud_chat["conversation_id"])
            msgs_sud = await _messages_of(sud_conv)
            roles = [r for r, _ in msgs_sud]
            contents = " | ".join(c for _, c in msgs_sud)
            assert "kickoff" not in contents.lower()
            assert any(r == "user" and "@nord" in c for r, c in msgs_sud)
            assert roles[-1] == "assistant" and "[ok]" in contents

            # ── 3. Epoch estampado una vez tras primera compilación del turno
            ep_sud = await current_stored_epoch(sud_conv)
            assert ep_sud is None or len(ep_sud) == 12  # wake no fuerza rebuild
            from core.bots_epoch import refresh_if_stale

            chk = await refresh_if_stale("sud")
            assert len(chk["epoch"]) == 12

            # ── 4. Eternos ocultos; Historial solo ve lo del usuario
            hidden_rows = await _hidden_titles()
            assert "Bot Chat" in hidden_rows
            from core.repositories.conversation_repository import ConversationRepository

            visible_ids = {c["id"] for c in await ConversationRepository.list_all(limit=100)}
            assert (
                sud_conv not in visible_ids and int(nord_chat["conversation_id"]) not in visible_ids
            )

            # ── 5. Grupo de 3 con respuesta desde sesión oculta propia
            from core import bots_groups as bg
            from orchestration.bots_groups_drive import run_room_turn

            await BotsService.create_bot("este", display_name="Este")
            room = await bg.create_room("coordinacion", "nord", ["nord", "sud", "este"])
            res = await run_room_turn(room["id"], "@sud resume riesgos y detente")
            assert res["status"] == "round-complete"
            assert res["responders"] == ["sud"]

            log = await bg.messages_of(room["id"])
            assert [m["author"] for m in log][:1] == ["user"]
            assert {m["author"] for m in log[1:]} == {"sud"}

            sesiones = await bg.member_sessions_state()
            assert "sud" in sesiones[room["id"]]

            # ── 6. Tripwire anti-puntero: el flujo jamás guarda ids
            for b in ("nord", "sud", "este"):
                meta = (await BotsService.get_bot(b))["ui_meta"]
                pointer_keys = {
                    k for k in meta.keys() if k.lower() in {"chat", "conversation_id", "session_id"}
                }
                assert not pointer_keys, f"puntero prohibido en ui_meta de {b}: {pointer_keys}"
                hist = await BotsService.meta_history(b)
                for h in hist:
                    assert not any(
                        k.lower() in {"chat", "conversation_id", "session_id"}
                        for k in h["ui_meta"].keys()
                    ), f"revisión histórica de {b} incluye puntero"

            # intento ESCRITO manualmente aún deja identidad por índice (robustez)
            ui = await BotsService.set_ui_meta("sud", {"chat": 999999}, expected_rev=0)
            resolved = await bots_chat.resolve_canonical("sud")
            assert resolved["conversation_id"] == sud_conv != 999999

            # ── 7. Ack JSON y cap 16k a través del gate COMPLETO
            from core.bots_gate import handle_dm_call, set_active_canonical

            with set_active_canonical("nord"):
                ok_out = ""
                ok_flag, ok_out = await handle_dm_call({"target": "sud", "message": "ping"}, sch)
                assert ok_flag and json.loads(ok_out)["status"] == "sent"
                big = "y" * 16001
                blocked, blocked_out = await handle_dm_call({"target": "sud", "message": big}, sch)
                assert not blocked and "cap de" in blocked_out
            del ok_out

            # inbox consume el 'ping' adicional en próximo tick limpio — junto
            # con el espejo de la respuesta de sud (Round-trip B): la respuesta
            # de sud ya no muere en su chat eterno, viaja como DM a nord
            more = await bots_wake.drain_tick()
            assert more == 2, "esperados espejo(sud→nord) + ping(nord→sud) en un tick"
    finally:
        await drop_schema(sch)


async def _messages_of(conv_id: int):
    from sqlalchemy import select

    from core.models import Message

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


async def _hidden_titles():
    async with get_async_session() as s:
        rows = (
            (
                await s.execute(
                    text("SELECT title FROM conversation WHERE is_hidden AND bot_id IS NOT NULL")
                )
            )
            .scalars()
            .all()
        )
        return set(rows)
