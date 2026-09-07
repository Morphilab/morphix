# orchestration/bots_groups_drive.py — rondas seriales con caps
"""Drive del grupo: cada mensaje de usuario abre UNA ronda donde responden
los miembros mencionados (determinista) o TODOS si no hay menciones.

Cada respuesta se ejecuta desde la sesión oculta 'Group: <roomId>' DEL BOT
propio (su contexto, su historial) — nunca en la sala global del usuario.
Caps v1: ≤3 rondas · ≤10 msgs · silencio 180s asienta '(pass)' · cap 20 min.
"""

import logging
import re
import time

import sqlalchemy as sa

from core import bots_groups
from core.bots import BotsService
from core.constants import wrap_untrusted
from core.database import get_async_session


def _room_prompt(
    room_name: str, room_id: str, user_text: str, *, slug: str, display_name: str
) -> str:
    """El user_text (puede arrastrar contenido pegado de fuentes externas)
    va SIEMPRE dentro del marco de dato no confiable; room_name/room_id son
    configuración del dueño y quedan como encabezado legible.

    Identidad: la sala sabe QUIÉN es el bot; un saludo merece
    saludo breve, '(pass)' queda SOLO para silencio real."""
    return (
        f"Eres @{slug} ({display_name}), miembro de la sala grupal "
        f"'{room_name}' ({room_id}). El usuario escribe:\n"
        f"{wrap_untrusted(user_text)}\n\n"
        "Responde breve y directo CON TU personalidad. Un saludo merece un "
        "saludo de vuelta. Usa '(pass)' SOLO si realmente no tienes nada que "
        "aportar en este turno."
    )


logger = logging.getLogger(__name__)

_PASS_RE = re.compile(r"^\s*\(?\s*(pass|silencio)\s*\)?[\s.,;:!—-]*$", re.IGNORECASE)
_PASS_PREFIX_RE = re.compile(r"^\s*\(?(pass|silencio)\)?[\s.,;:—]+", re.IGNORECASE)
PASS_MARKS = {"sin respuesta necesaria"}
# "(pass) — nota breve" cuenta como silencio con explicación; una respuesta
# real que CONTENGA "sin respuesta necesaria" de pasada NO es pass (antes el
# substring suelto marcaba respuestas reales como silencio — caso alfa en
# pruebas.
PASS_NOTE_MAX_CHARS = 130


def _pass_now() -> float:
    return time.monotonic()


def is_pass(text: str) -> bool:
    low = (text or "").strip().lower()
    if low in PASS_MARKS:
        return True
    if _PASS_RE.match(low):
        return True
    if _PASS_PREFIX_RE.match(low) and len(low) <= PASS_NOTE_MAX_CHARS:
        return True
    return False


async def run_room_turn(room_id: str, user_text: str) -> dict:
    """Procesa un mensaje del usuario en la sala y devuelve resumen de ronda."""
    room = await bots_groups.get_room(room_id)
    if room is None:
        raise bots_groups.GroupError(f"sala inexistente '{room_id}'")

    members: list[str] = list(room["members"])
    msgs_before = len(await bots_groups.messages_of(room_id))
    if msgs_before >= bots_groups.MAX_TOTAL_MSGS:
        return {"status": "capped", "reason": f"la sala alcanzó {msgs_before} mensajes"}
    await bots_groups.post_message(room_id, "user", user_text)

    mentioned = bots_groups.parse_mentions(user_text, [m for m in members])
    responders = mentioned or members

    started = _pass_now()
    replies: list[dict] = []
    deadline_started = time.time() + bots_groups.TURN_WINDOW_S
    session_deadline = time.time() + bots_groups.SESSION_CAP_S

    _started_monotonic = started

    def _over_silence_budget():
        return time.monotonic() - _started_monotonic > bots_groups.SILENCE_TIMEOUT_S

    for slug in responders[: bots_groups.MAX_ROUNDS]:
        if _over_silence_budget():
            return {
                "status": "round-complete",
                "room": room_id,
                "reason": "presupuesto de silencio agotado",
                "replies": replies,
            }
        if time.time() > session_deadline:
            return {"status": "capped", "reason": "cap de sesión 20 min", "replies": replies}
        if time.time() > deadline_started:
            return {"status": "capped", "reason": "ventana de turno agotada", "replies": replies}

        try:
            reply = await _member_reply(slug, room_id, room["name"], user_text)
            status = "pass" if is_pass(reply) else "ok"
            await bots_groups.post_message(room_id, slug, "(pass)" if status == "pass" else reply)
            replies.append({"slug": slug, "status": status})
        except Exception as e:
            logger.warning("respuesta de @%s falló (stranded): %s", slug, e)
            # sin esta marca, una ronda fallida era invisible en el
            # log compartido (solo en el resumen de estado de la GUI).
            try:
                await bots_groups.post_message(room_id, slug, "(⚠ sin respuesta — error interno)")
            except Exception:
                pass
            replies.append({"slug": slug, "status": "stranded"})

    stranded = [r for r in replies if r["status"] == "stranded"]
    passes = sum(1 for r in replies if r["status"] == "pass")
    return {
        "status": "round-complete",
        "room": room_id,
        "responders": [r["slug"] for r in replies],
        "passes": passes,
        "stranded": stranded,
    }


async def _member_reply(slug: str, room_id: str, room_name: str, user_text: str) -> str:
    """Turno real del bot desde SU sesión 'Group: <roomId>'."""
    bot_def = await BotsService.get_bot(slug)
    if not bot_def or not bot_def.get("enabled"):
        raise bots_groups.GroupError(f"@{slug} inactivo")
    async with get_async_session() as s:
        row = (await s.execute(sa.text("SELECT id FROM bots WHERE slug=:s"), {"s": slug})).scalar()
        bot_id = int(row)
    conv_id = await bots_groups.ensure_member_session(slug, bot_id, room_id)

    prompt = _room_prompt(
        room_name, room_id, user_text, slug=slug, display_name=bot_def.get("display_name") or slug
    )

    # Turno del bot por bots_runner — identidad propia,
    # sin plantillas de workflow. dm_enabled=False ⇒ sin send_to_bot en salas.
    from orchestration.bots_runner import run_bot_turn

    final = await run_bot_turn(
        slug,
        prompt,
        conv_id,
        transport="room",
        dm_enabled=False,
    )

    content = final.strip() if isinstance(final, str) else ""
    content = content or "(pass)"
    # persistencia del intercambio EN la sesión miembro (historial del bot).
    # Pass auditable: el texto REAL que produjo el bot se conserva
    # como nota — antes se descartaba y era imposible distinguir un pass
    # genuino de un falso-positivo (caso alfa en pruebas).
    from core.bots_messaging import persist_turn_exchange

    if is_pass(content):
        await persist_turn_exchange(
            conv_id,
            body=f"[{room_name}] {user_text}",
            assistant=None,
            note=f"(pass) — {content[:200]}",
        )
    else:
        await persist_turn_exchange(
            conv_id,
            body=f"[{room_name}] {user_text}",
            assistant=content,
        )
    return content


__all__ = ["run_room_turn"]
