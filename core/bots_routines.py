# core/bots_routines.py — rutinas por bot
"""Parser de schedules sin dependencias nuevas, scheduler asyncio single-flight
con advisory-lock PostgreSQL y entrega dual ('history' | 'bot-chat').

Machine-local: una rutina SOLO corre en el workspace donde vive su bot —
garantizado por construcción aquí (todo lo que hace usa el schema activo).

Deny-list de tools para turnos de rutina definida UNA vez:
messaging entre bots (anti-eco programado) y clarificaciones interactivas.
"""

import logging
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from core.bots import BotError
from core.database import get_async_session
from core.models import PendingTurn, Routine

logger = logging.getLogger(__name__)

ROUTINE_DENIED_TOOLS = {"send_to_bot", "ask_clarification"}
CATCHUP_MIN_S = 120
CATCHUP_MAX_S = 7200  # clamp del período de catchup (120s–2h)
DEFAULT_BOT_CHAT_TIMEOUT_S = 600
# tras ROUTINE_DEAD_LETTER fallos consecutivos el
# re-firing se acota a 1 intento/día (antes re-ejecutaba el prompt completo,
# con efectos secundarios de tools, cada CATCHUP_MIN_S sin techo). Una
# edición o re-habilitación por el usuario reinicia el contador.
ROUTINE_DEAD_LETTER = 5
DEAD_LETTER_BACKOFF_S = 24 * 60 * 60

# ── Parser de schedules ────────────────────────────────────────────────────

_DUR_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration_seconds(raw: str) -> float | None:
    s = (raw or "").strip().lower()
    unit = s[-1] if s else ""
    if unit in _DUR_UNITS and s[:-1].isdigit():
        return int(s[:-1]) * _DUR_UNITS[unit]
    return None


def _is_cron5(s: str) -> bool:
    parts = (s or "").split()
    if len(parts) != 5:
        return False
    ok_tokens = {"*", "-", ",", "/", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"}
    return all(set(p) <= ok_tokens for p in parts)


# límites legales por campo cron (min, hora, dom, mes, dow). Cotas
# superiores absurdas (p.ej. "0-99999999999") ya no materializan rangos
# gigantes ni fuerzan escaneos degenerados (DoS con un schedule de 20 chars).
_CRON_FIELD_LIMITS = {"min": 59, "hour": 23, "dom": 31, "mon": 12, "dow": 6}


def _cron_field_matches(
    field: str,
    value: int,
    *,
    dom=False,
    dow=False,
    kind: str | None = None,
) -> bool:
    if field == "*":
        return True
    if kind is None:
        # inferencia retro-compatible cuando el caller no declara el campo
        kind = "dow" if dow else ("dom" if dom else "min")
    hi_cap = _CRON_FIELD_LIMITS.get(kind, 59)
    matches = False
    for chunk in field.split(","):
        step = 1
        rng = chunk
        if "/" in chunk:
            rng, step_s = chunk.split("/", 1)
            try:
                step = max(1, int(step_s))
            except ValueError:
                return False
        if "-" in rng:
            a, b = rng.split("-", 1)
            # tokens multi-guion ('1-2-3') explotaban con ValueError
            # crudo — el contrato de schedule_next_run es BotError/False.
            try:
                lo, hi = int(a), int(b)
            except ValueError:
                return False
            # bound fuera del rango legal ⇒ campo malformado → rechazo
            if not (0 <= lo <= hi_cap and 0 <= hi <= hi_cap):
                return False
        elif rng == "*":
            lo, hi = (0, hi_cap)
        else:
            lo = hi = int(rng)
            if not (0 <= lo <= hi_cap):
                return False
        cand = [v for v in range(lo, hi + 1) if (v - lo) % step == 0]
        if value in cand:
            matches = True
            break
    return matches


def cron5_next_run(expr: str, after: datetime) -> datetime | None:
    """Próxima ocurrencia de un cron5 ``min hora dom mes dow`` tras ``after``."""
    if not _is_cron5(expr):
        return None
    f_min, f_hour, f_dom, f_mon, f_dow = expr.split()

    t = (after + timedelta(minutes=1)).replace(second=0, microsecond=0)
    limit = t + timedelta(days=370)
    while t < limit:
        if (
            _cron_field_matches(f_mon, t.month, kind="mon")
            and _cron_field_matches(f_dom, t.day, dom=True, kind="dom")
            and _cron_field_matches(f_dow, t.weekday(), dow=True, kind="dow")  # lunes=0
            and _cron_field_matches(f_hour, t.hour, kind="hour")
            and _cron_field_matches(f_min, t.minute, kind="min")
        ):
            return t
        t += timedelta(minutes=1)
    return None


def schedule_next_run(schedule: str, *, now: datetime | None = None) -> dict:
    """Devuelve {'kind', 'interval_s'|'at'} o lanza BotError si es inválido."""
    now = now or datetime.now(UTC).replace(tzinfo=None)
    raw = (schedule or "").strip()
    if not raw:
        raise BotError("schedule vacío")

    low = raw.lower()
    if low.startswith("every "):
        sec = parse_duration_seconds(low[len("every ") :])
        if not sec or sec <= 0:
            raise BotError(f"'every' inválido: '{raw}'")
        return {"kind": "interval", "interval_s": float(sec)}

    sec = parse_duration_seconds(low)
    if sec and sec > 0:
        return {"kind": "interval", "interval_s": float(sec)}

    if _is_cron5(raw):
        nxt = cron5_next_run(raw, now)
        if nxt is None:
            raise BotError(f"cron5 sin próxima ocurrencia: '{raw}'")
        return {"kind": "cron5", "expr": raw}

    try:  # ISO one-shot
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        # un ISO con offset ≠ UTC conservaba el reloj pared —
        # '10:00+02:00' disparaba 2h antes. Normalizar a UTC naive.
        if dt.tzinfo is not None:
            dt = dt.astimezone(UTC).replace(tzinfo=None)
        if dt <= now:
            raise BotError("one-shot en el pasado")
        return {"kind": "oneshot", "at": dt}
    except ValueError as e:
        raise BotError(f"schedule no reconocido: '{raw}'. Usa '30m'|'every 2h'|cron5|ISO") from e


def compute_next(
    schedule_kind: str,
    payload: dict,
    prev: datetime | None,
    *,
    last_finished: datetime | None = None,
) -> datetime | None:
    now = datetime.now(UTC).replace(tzinfo=None)
    if schedule_kind == "interval":
        base = prev or now
        nxt = base + timedelta(seconds=float(payload["interval_s"]))
        # catchup window: mitad del período clamped
        drift_s = (now - nxt).total_seconds() if nxt < now else 0
        if drift_s > 0:
            window = min(max(payload["interval_s"] / 2, CATCHUP_MIN_S), CATCHUP_MAX_S)
            if drift_s > window:
                nxt = now + timedelta(seconds=payload["interval_s"])
        return nxt
    if schedule_kind == "cron5":
        base = last_finished or prev or now
        return cron5_next_run(payload["expr"], base)
    return payload.get("at")  # oneshot


# ── CRUD ───────────────────────────────────────────────────────────────────


async def create_routine(
    name: str,
    schedule: str,
    *,
    prompt: str,
    slug: str | None = None,
    deliver: str = "history",
    context_from: list[str] | None = None,
) -> dict:
    if deliver not in {"history", "bot-chat"}:
        raise BotError("deliver debe ser 'history' o 'bot-chat'")
    info = schedule_next_run(schedule)

    async with get_async_session() as session:
        bot_id = None
        if slug:
            bot_id = (
                await session.execute(sa.text("SELECT id FROM bots WHERE slug=:s"), {"s": slug})
            ).scalar()
            if bot_id is None:
                raise BotError(f"no existe el bot '{slug}'")
        nxt = compute_next(info["kind"], info, None)
        r = Routine(
            name=name[:128],
            bot_id=bot_id,
            schedule=schedule[:128],
            prompt=prompt,
            deliver=deliver,
            enabled=True,
            next_run_at=nxt,
            context_from=list(context_from or []),
        )
        session.add(r)
        await session.flush()
        await session.refresh(r)
        out = {
            "id": r.id,
            "name": r.name,
            "bot_id": r.bot_id,
            "schedule": r.schedule,
            "deliver": r.deliver,
            "enabled": r.enabled,
            "next_run_at": r.next_run_at,
        }
    logger.info("rutina creada '%s' (%s)", name, info["kind"])
    return out


async def list_routines(slug: str | None = None) -> list[dict]:
    async with get_async_session() as session:
        stmt = sa.text(
            "SELECT id,name,bot_id,schedule,deliver,prompt,enabled,last_run_at,"
            "next_run_at,last_error,failure_count FROM routines"
            + (" WHERE bot_id=(SELECT id FROM bots WHERE slug=:s)" if slug else "")
            + " ORDER BY id"
        )
        rows = (await session.execute(stmt, {"s": slug} if slug else {})).mappings().all()
        return [dict(r) for r in rows]


async def set_routine_enabled(routine_id: int, enabled: bool) -> bool:
    async with get_async_session() as session:
        # re-habilitar (o pausar+editar) es intervención del usuario ⇒ el
        # contador de fallos consecutivos muere.
        res = await session.execute(
            sa.text("UPDATE routines SET enabled=:e, failure_count=0 WHERE id=:i"),
            {"e": enabled, "i": routine_id},
        )
        ok = bool(getattr(res, "rowcount", 0))
    # sin log, un pausado/borrado en GUI era invisible en el log
    # (la rutina 'saludos' del run 06:45-06:47 desapareció sin rastro).
    if ok:
        logger.info("rutina %s %s", routine_id, "habilitada" if enabled else "pausada")
    return ok


async def update_routine(
    routine_id: int,
    *,
    name: str | None = None,
    schedule: str | None = None,
    prompt: str | None = None,
) -> bool:
    """Actualiza campos editables de una rutina.

    El schedule se valida con el parser ANTES del UPDATE (fail-loud: un
    schedule roto jamás deja la rutina persistida en estado inválido) y
    next_run_at se recalcula. Sin campos → False.
    """
    nxt = None
    if schedule is not None:
        try:
            info = schedule_next_run(schedule)
            nxt = compute_next(info["kind"], info, None)
        except Exception:
            return False
    sets: dict[str, object] = {}
    if name is not None:
        sets["name"] = name[:128]
    if schedule is not None:
        sets["schedule"] = schedule[:128]
        sets["next_run_at"] = nxt
    if prompt is not None:
        sets["prompt"] = prompt
    if not sets:
        return False
    # cualquier edición real cuenta como intervención del usuario ⇒ reset.
    sets["failure_count"] = 0
    assign = ", ".join(f"{k}=:{k}" for k in sets)
    async with get_async_session() as session:
        res = await session.execute(
            sa.text(f"UPDATE routines SET {assign} WHERE id=:i"),
            {**sets, "i": routine_id},
        )
        ok = bool(getattr(res, "rowcount", 0))
    if ok:
        logger.info("rutina %s actualizada (%s)", routine_id, ", ".join(sets))
    return ok


async def delete_routine(routine_id: int) -> bool:
    async with get_async_session() as session:
        res = await session.execute(sa.text("DELETE FROM routines WHERE id=:i"), {"i": routine_id})
        ok = bool(getattr(res, "rowcount", 0))
    if ok:
        logger.info("rutina eliminada id=%d", routine_id)
    return ok


async def mark_fired(
    routine_id: int,
    *,
    next_at: datetime | None,
    error: str | None = None,
    now: datetime | None = None,
    failure_count: int | None = None,
) -> None:
    """Registra el firing. ``failure_count``: 0 tras éxito, N+1 tras fallo;
    None = no tocar (skip por dead-letter, que no es un intento)."""
    now = now or datetime.now(UTC).replace(tzinfo=None)
    sets = "last_run_at=:now, next_run_at=:nxt, last_error=:err"
    params: dict[str, object] = {"now": now, "nxt": next_at, "err": error, "i": routine_id}
    if failure_count is not None:
        sets += ", failure_count=:fc"
        params["fc"] = failure_count
    async with get_async_session() as session:
        await session.execute(
            sa.text(f"UPDATE routines SET {sets} WHERE id=:i"),
            params,
        )


# ── Scheduler single-flight (advisory-lock PG) ─────────────────────────────

_SCHED_LOCK_KEY = 917231001  # espacio propio de Bot Mode


async def _try_advisory_lock() -> object | None:
    from core.database import _get_async_engine

    engine = _get_async_engine()
    conn = await engine.connect()
    got = (await conn.execute(sa.text(f"SELECT pg_try_advisory_lock({_SCHED_LOCK_KEY})"))).scalar()
    if not got:
        await conn.close()
        return None
    return conn


async def release_advisory_lock(conn) -> None:
    try:
        await conn.execute(sa.text(f"SELECT pg_advisory_unlock({_SCHED_LOCK_KEY})"))
    finally:
        await conn.close()


async def scheduler_tick(*, run_prompt=None) -> dict:
    """Un tick: filas debido ∧ habilitadas; NUNCA dos corredores simultáneos.

    ``run_prompt(prompt, deliver, bot_slug|None, routine_name)`` corrutina
    inyectada por la capa orchestration (evita invertir dependencias).
    """
    conn = await _try_advisory_lock()
    if conn is None:
        logger.debug("scheduler_tick omitido: otro nodo mantiene el lock")
        return {"ran": 0, "skipped_lock": True}
    ran = failed = skipped_dead = 0
    try:
        now = datetime.now(UTC).replace(tzinfo=None)
        async with get_async_session() as session:
            rows = (
                (
                    await session.execute(
                        sa.text(
                            "SELECT r.*, b.slug AS slug FROM routines r LEFT JOIN bots b ON b.id=r.bot_id "
                            "WHERE r.enabled AND (r.next_run_at IS NULL OR r.next_run_at<=:now)"
                        ),
                        {"now": now},
                    )
                )
                .mappings()
                .all()
            )

        for row in rows:
            # dead-letter de rutinas — tras N fallos
            # consecutivos el re-firing pasa a 1 intento/día. Antes el prompt
            # completo (tools con efectos secundarios) se re-ejecutaba cada
            # CATCHUP_MIN_S sin techo. La edición/re-habilitación reinicia.
            if int(row.get("failure_count") or 0) >= ROUTINE_DEAD_LETTER:
                skipped_dead += 1
                logger.warning(
                    "rutina %s ('%s') en dead-letter (%d fallos) — re-intento diario; "
                    "edítala o re-habilita para resetear. Último error: %s",
                    row["id"],
                    row.get("name"),
                    int(row.get("failure_count") or 0),
                    str(row.get("last_error") or "")[:200],
                )
                await mark_fired(
                    int(row["id"]),
                    next_at=datetime.now(UTC).replace(tzinfo=None)
                    + timedelta(seconds=DEAD_LETTER_BACKOFF_S),
                    error=(
                        f"dead-letter ({ROUTINE_DEAD_LETTER}+ fallos consecutivos) — "
                        f"re-intento diario; editar/re-habilitar resetea. Último: "
                        f"{str(row.get('last_error') or '')[:300]}"
                    ),
                )
                continue
            try:
                info = schedule_next_run(row["schedule"])
                kind, payload = info["kind"], info
                if row["deliver"] == "bot-chat":
                    assert row["slug"], "bot requerido para deliver bot-chat"

                if run_prompt is not None:
                    await run_prompt(
                        str(row["prompt"]), str(row["deliver"]), row["slug"], str(row["name"])
                    )

                # oneshot se deshabilita tras disparar
                enable_after = False
                nxt = compute_next(
                    kind,
                    payload,
                    row["last_run_at"],
                    last_finished=datetime.now(UTC).replace(tzinfo=None),
                )
                if kind == "oneshot":
                    enable_after = True
                await mark_fired(int(row["id"]), next_at=nxt, error=None, failure_count=0)
                if enable_after:
                    await set_routine_enabled(int(row["id"]), False)
                ran += 1
            except Exception as e:
                failed += 1
                await mark_fired(
                    int(row["id"]),
                    next_at=datetime.now(UTC).replace(tzinfo=None)
                    + timedelta(seconds=CATCHUP_MIN_S),
                    error=str(e)[:500],
                    failure_count=int(row.get("failure_count") or 0) + 1,
                )
        return {"ran": ran, "failed": failed, "skipped_dead": skipped_dead}
    finally:
        await release_advisory_lock(conn)


async def enqueue_bot_chat_delivery(
    bot_slug: str, body: str, *, timeout_s: int = DEFAULT_BOT_CHAT_TIMEOUT_S
) -> int:
    """Entrega al Bot Chat SIEMPRE machine-local (workspace actual)."""
    async with get_async_session() as session:
        dest = (
            await session.execute(sa.text("SELECT id FROM bots WHERE slug=:s"), {"s": bot_slug})
        ).scalar()
        if dest is None:
            raise BotError(f"delivery bot-chat requiere al bot EN ESTE workspace ({bot_slug})")
        row = PendingTurn(
            bot_id=int(dest),
            source="routine",
            from_handle="system",
            body=f"[Rutina] {body}"[:8000],
            claimed_at=None,
        )
        session.add(row)
        await session.flush()
        pos = int(row.id or 0)
    logger.info(
        "entrega bot-chat encolada para @%s (pos=%d, timeout=%ds)", bot_slug, pos, timeout_s
    )
    return pos


__all__ = [
    "ROUTINE_DENIED_TOOLS",
    "schedule_next_run",
    "compute_next",
    "create_routine",
    "list_routines",
    "set_routine_enabled",
    "delete_routine",
    "mark_fired",
    "scheduler_tick",
    "enqueue_bot_chat_delivery",
]
