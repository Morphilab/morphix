# tests/test_bots_routines.py — parser, CRUD, scheduler con
# advisory-lock y entrega dual (history / bot-chat machine-local).
"""Scheduler_tick con executor inyectado faked; PG real para locks/due."""

import asyncio
import secrets

import pytest

from core import bots_routines as br
from core.bots import BotsService
from core.bots_chat import ensure_open
from core.bots_routines import (
    compute_next,
    create_routine,
    delete_routine,
    list_routines,
    parse_duration_seconds,
    schedule_next_run,
    set_routine_enabled,
)
from core.database import (
    bound_schema,
    create_schema,
    create_tables_in_schema,
    drop_schema,
)

_pg = pytest.mark.skipif(
    not __import__("os").environ.get("DATABASE_URL"), reason="requiere DATABASE_URL (PG real)"
)


def test_parser_schedules():
    assert parse_duration_seconds("30m") == 1800.0
    assert parse_duration_seconds("2h") == 7200.0 and parse_duration_seconds("1d") == 86400.0
    info = schedule_next_run("every 2h")
    assert info["kind"] == "interval" and info["interval_s"] == 7200.0

    cron = schedule_next_run("0 9 * * 1")
    assert cron["kind"] == "cron5"
    nxt = compute_next("cron5", cron, None)

    assert nxt is not None and 0 < (nxt - nxt.replace(hour=0)).total_seconds() <= 86400 * 7

    iso = schedule_next_run("2030-01-01T09:00:00")
    assert iso["kind"] == "oneshot"

    from core.bots import BotError

    for bad in ("", "tomorrow", "cada rato"):
        with pytest.raises(BotError):
            schedule_next_run(bad)
    # cron5 con '*' en campo fecha cae a branch ISO → ValueError puro
    with pytest.raises(ValueError):
        schedule_next_run("*/a * * * *")


@_pg
@pytest.mark.asyncio
async def test_crud_and_due_tick_delivery_modes():
    sch = f"bots_rt_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            await BotsService.create_bot("crone", display_name="Crone")
            await ensure_open("crone")

            r1 = await create_routine(
                "resumen", "30m", prompt="resume el repo", slug="crone", deliver="bot-chat"
            )
            r2 = await create_routine("idea", "1m", prompt="anota una idea", deliver="history")
            rows = {r["name"]: r for r in await list_routines()}
            assert rows["resumen"]["bot_id"] is not None
            assert rows["idea"]["bot_id"] is None
            assert rows["resumen"]["next_run_at"] is not None

            # FORZAR vencimiento: NULL cuenta como debido en scheduler_tick
            from sqlalchemy import text

            from core.database import get_async_session

            async with get_async_session() as s:
                await s.execute(text("UPDATE routines SET next_run_at = NULL"))

            # doble tick simultáneo: uno gana el advisory-lock de su conexión,
            # el otro reporta skipped_lock (single-flight multi-conexión)
            calls: list[tuple] = []

            async def semi_real_prompt(prompt, deliver, slug, name):
                if deliver == "bot-chat":
                    await br.enqueue_bot_chat_delivery(slug or "", prompt)
                else:
                    calls.append((name, deliver))

            res = list(
                await asyncio.gather(
                    br.scheduler_tick(run_prompt=semi_real_prompt),
                    br.scheduler_tick(run_prompt=semi_real_prompt),
                )
            )
            ran_total = sum(x.get("ran", 0) for x in res)
            assert ran_total == 2, f"ambas vencidas debieron correr: {res}"
            skips = [x for x in res if x.get("skipped_lock")]
            assert len(skips) <= 1 and any(x["ran"] > 0 or x.get("skipped_lock") for x in res)

            # entrega dual: history registrada por executor inyectado
            kinds = sorted(c[0] for c in calls)
            assert kinds == ["idea"]
            # … y bot-chat en cola real CON atribución
            async with get_async_session() as s:
                pending = (
                    await s.execute(text("SELECT source, body FROM pending_turns ORDER BY id"))
                ).fetchall()
            assert pending and pending[0][0] == "routine" and "[Rutina]" in pending[0][1]
            del skips

            # pause/resume/delete
            rid = int(rows["resumen"]["id"])
            assert await set_routine_enabled(rid, False) is True
            paused = {r["name"]: r for r in await list_routines()}["resumen"]
            assert str(paused["enabled"]) in {"false", "False", 0}
            assert await delete_routine(int(r2["id"])) is True
            assert "idea" not in {r["name"] for r in await list_routines()}
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_botchat_requires_local_bot():
    """Deliver bot-chat sin bot local ⇒ error accionable."""
    sch = f"bots_c11_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            with pytest.raises(Exception, match="EN ESTE workspace"):
                await br.enqueue_bot_chat_delivery("fantasma", "hola")
    finally:
        await drop_schema(sch)


# ── robustez del parser de schedules ────────────────────────


def test_cron_multiguion_rechaza_sin_explotar():
    """'1-2-3 * * * *' da error tipado (BotError), no ValueError crudo
    (int('2-3'))."""
    import pytest as _pytest

    from core.bots import BotError
    from core.bots_routines import schedule_next_run

    # tipado (BotError), ya no ValueError crudo; el mensaje puede ser de
    # cron-sin-ocurrencia o no-reconocido según la fase del parser
    with _pytest.raises(BotError):
        schedule_next_run("1-2-3 * * * *")


def test_oneshot_iso_con_offset_convierte_a_utc():
    """Un oneshot '+02:00' debe convertirse a UTC: conservar el reloj pared
    dispararía 2h antes."""
    from datetime import UTC, datetime, timedelta

    from core.bots_routines import schedule_next_run

    future = datetime.now(UTC) + timedelta(hours=5)
    raw = future.isoformat()  # aware con +00:00
    out = schedule_next_run(raw)
    assert out["kind"] == "oneshot"
    assert out["at"] == future.replace(tzinfo=None)

    # mismo instante expresado en +02:00 (reloj pared 2h mayor, offset -2h)
    raw2 = (future + timedelta(hours=2)).replace(tzinfo=None).isoformat() + "+02:00"
    out2 = schedule_next_run(raw2)
    assert out2["kind"] == "oneshot"
    assert out2["at"] == future.replace(
        tzinfo=None
    ), "BOT2-M3: el offset horario se descartó — la rutina dispara desplazada"


@_pg
@pytest.mark.asyncio
async def test_routine_admin_logs_toggle_and_delete(caplog):
    """Pausar o borrar una rutina debe quedar registrado. Contrato:
    set_routine_enabled y delete_routine loguean INFO."""
    sch = f"bots_rtlog_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            r = await create_routine("rt", "1m", prompt="p", deliver="history")
            rid = int(r["id"])

            with caplog.at_level("INFO", logger="core.bots_routines"):
                assert await set_routine_enabled(rid, False) is True
                assert await delete_routine(rid) is True
            text = caplog.text
            assert "pausada" in text, f"toggle debe loguear: {text}"
            assert f"rutina eliminada id={rid}" in text
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_update_routine_schedule_prompt_y_validacion():
    """Edición de rutinas sin re-crearlas.

    update_routine valida el schedule ANTES de persistir (schedule roto
    jamás deja la rutina en estado inválido) y recalcula next_run_at.
    """
    sch = f"bots_rt_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            await create_routine("upd_tmp", "30m", prompt="antes", deliver="history")
            rows = await list_routines()
            rid = next(r["id"] for r in rows if r["name"] == "upd_tmp")

            ok = await br.update_routine(rid, schedule="2h", prompt="después")
            assert ok is True
            row = next(r for r in await list_routines() if r["id"] == rid)
            assert row["schedule"] == "2h" and row["prompt"] == "después"
            assert row["next_run_at"] is not None

            # schedule inválido → False SIN persistir (estado intacto)
            ok_bad = await br.update_routine(rid, schedule="cadacien")
            assert ok_bad is False
            row2 = next(r for r in await list_routines() if r["id"] == rid)
            assert row2["schedule"] == "2h" and row2["prompt"] == "después"

            # solo prompt (schedule None no toca next_run_at)
            assert await br.update_routine(rid, prompt="tercero") is True
            row3 = next(r for r in await list_routines() if r["id"] == rid)
            assert row3["prompt"] == "tercero" and row3["schedule"] == "2h"

            # nada que cambiar → False
            assert await br.update_routine(rid) is False
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_scheduler_dead_letter_acota_re_firing():
    """N fallos consecutivos ⇒ dead-letter.

    Sin el acote, el prompt completo (tools con efectos secundarios) se
    re-ejecutaría cada CATCHUP_MIN_S sin techo. Con él: 5 fallos ⇒ el
    executor deja de invocarse (re-intento diario), last_error lo documenta
    y una edición de la rutina resetea el contador (vuelve a correr al
    siguiente tick)."""
    sch = f"bots_dl_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            from sqlalchemy import text

            from core.database import get_async_session

            await create_routine("fatal", "1m", prompt="rompe", deliver="history")

            calls = {"n": 0}

            async def exploding_prompt(prompt, deliver, slug, name):
                calls["n"] += 1
                raise RuntimeError("fallo simulado")

            async def _force_due() -> None:
                async with get_async_session() as s:
                    await s.execute(text("UPDATE routines SET next_run_at = NULL"))

            await _force_due()
            # 5 fallos consecutivos: cada uno invoca el executor y suma el contador
            for _ in range(br.ROUTINE_DEAD_LETTER):
                res = await br.scheduler_tick(run_prompt=exploding_prompt)
                assert res.get("failed") == 1, res
                await _force_due()
            assert calls["n"] == br.ROUTINE_DEAD_LETTER

            # 6º tick: dead-letter — el executor YA NO se invoca
            res = await br.scheduler_tick(run_prompt=exploding_prompt)
            assert calls["n"] == br.ROUTINE_DEAD_LETTER, "dead-letter re-ejecutó el prompt"
            assert res.get("skipped_dead") == 1, res
            row = next(r for r in await list_routines() if r["name"] == "fatal")
            assert "dead-letter" in (row["last_error"] or "")

            # edición = intervención del usuario ⇒ reset y vuelve a correr
            rid = int(row["id"])
            assert await br.update_routine(rid, prompt="cambiado") is True
            fresh = next(r for r in await list_routines() if r["id"] == rid)
            assert int(fresh["failure_count"]) == 0
            await _force_due()
            res2 = await br.scheduler_tick(run_prompt=exploding_prompt)
            # corre de nuevo (failed, el executor explota) pero SIN dead-letter
            assert res2.get("failed") == 1 and res2.get("skipped_dead") == 0, res2
            assert calls["n"] == br.ROUTINE_DEAD_LETTER + 1
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_conversation_for_history_no_duplica():
    """_conversation_for_history es idempotente (índice único parcial)."""
    sch = f"bots_b2_{secrets.token_hex(4)}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            from orchestration.bots_clock import _conversation_for_history

            id1 = await _conversation_for_history("audit_b2", None)
            id2 = await _conversation_for_history("audit_b2", None)
            id3 = await _conversation_for_history("audit_b2", None)
            assert id1 and id1 == id2 == id3, "la carrera simulada duplicó la conversación"
    finally:
        await drop_schema(sch)
