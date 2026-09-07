# core/bots_gate.py — inyección condicional de send_to_bot
"""Mecanismo mínimo de gate para la tool inter-bot sobre el registry plano.

Inyección, no registro: ``send_to_bot`` NO pasa por tools_registry ni
toolsets. El system ve su schema SOLO cuando el turno corre en un chat
canónico con roster ≥2 y flag abierto; y el DESPACHO se re-gatea aquí mismo
(defensa en profundidad) leyendo el contexto de sesión activo.
"""

import json
import logging
from contextvars import ContextVar

from core.bots_messaging import send_dm
from core.feature_flags import kairos

logger = logging.getLogger(__name__)

BOT_DM_TOOL_NAME = "send_to_bot"
DM_SCHEMA = {
    "type": "function",
    "function": {
        "name": BOT_DM_TOOL_NAME,
        "description": (
            "Envía un DM a otro bot del workspace (fire-and-forget). No esperes "
            "respuesta en este turno: llegará después como mensaje entrante en "
            "tu chat. Requiere target (slug del compañero) y message."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "slug del bot destinatario (con o sin @)",
                },
                "message": {"type": "string", "description": "contenido del DM"},
            },
            "required": ["target", "message"],
        },
    },
}

# Sesión canónica ACTIVA dentro del loop (set/clear por execute_agent_loop)
_active_canonical: ContextVar[str | None] = ContextVar("bots_active_canonical", default=None)

# Round-trip DM (espejo runtime): nº de send_to_bot EXITOSOS dentro del turno
# en curso. dispatch_row lo lee al terminar — si el bot respondió por tool, el
# espejo NO duplica; si respondió solo con texto final (fallo de protocolo
# frecuente en modelos pequeños), el espejo garantiza que la respuesta llegue.
_dm_sends_this_turn: ContextVar[int] = ContextVar("bots_dm_sends_this_turn", default=0)


def dm_sends_this_turn() -> int:
    """Envíos send_to_bot exitosos dentro del turno en curso (0 fuera de uno)."""
    return _dm_sends_this_turn.get()


def set_active_canonical(slug: str | None):
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        token = _active_canonical.set(slug)
        try:
            yield
        finally:
            _active_canonical.reset(token)

    return _ctx()


def _flag(name: str, default: bool) -> bool:
    """Normaliza el valor del flag a bool de forma segura.

    Fail-safe hacia el default solo ante lecturas fallidas; un string crudo
    tipo "false"/"0"/"no" NUNCA evalúa a True."""
    try:
        val = kairos.get(name)
    except Exception:
        return default
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes", "on")
    return bool(val)


async def _roster_size() -> int:
    from core.bots import BotsService

    try:
        return len(await BotsService.list_bots(include_disabled=False))
    except Exception:
        return 0


async def canonical_tool_schema(active_slug: str | None) -> dict | None:
    """Schema de send_to_bot si TODO el gate está abierto; None ⇒ invisibilidad."""
    if not active_slug:
        return None
    if not _flag("BOT_MODE", True) or not _flag("BOT_MODE_PROTOCOL", True):
        return None
    if await _roster_size() < 2:
        return None
    return dict(DM_SCHEMA)


async def handle_dm_call(arguments: dict, workspace: str) -> tuple[bool, str]:
    """Re-gate + envío real. Devuelve (ok, output_str) al estilo del wrapper.

    Todo rechazo loguea warning con su motivo — sin esto, un DM
    que el modelo no llega a enviar es indistinguible en logs de un turno
    que nunca intentó enviarlo."""
    sender = _active_canonical.get()
    checks_ok = sender is not None and _flag("BOT_MODE", True) and _flag("BOT_MODE_PROTOCOL", True)
    if not checks_ok:
        logger.warning(
            "send_to_bot rechazado por gate: canonical=%s bot_mode=%s protocol=%s (ws=%s)",
            sender,
            _flag("BOT_MODE", True),
            _flag("BOT_MODE_PROTOCOL", True),
            workspace,
        )
        return False, json.dumps(
            {"error": "send_to_bot solo está disponible en tu chat canónico"},
            ensure_ascii=False,
        )
    roster_n = await _roster_size()
    if roster_n < 2:
        logger.warning("send_to_bot rechazado: roster=%d (<2) (ws=%s)", roster_n, workspace)
        return False, json.dumps(
            {"error": f"sin compañeros: roster={roster_n} (<2)"}, ensure_ascii=False
        )

    target = str(arguments.get("target", "") or "")
    message = str(arguments.get("message", "") or "")
    if sender is None:  # narrow para mypy; checks_ok ya lo garantiza
        return False, json.dumps({"error": "sesión canónica inactiva"}, ensure_ascii=False)
    try:
        ack = await send_dm(sender, target, message)
    except Exception as e:
        # errores ACCIONABLES: el modelo debe poder autocorregir
        logger.warning("send_to_bot %s→%s falló: %s (ws=%s)", sender, target, e, workspace)
        return False, json.dumps({"error": str(e)}, ensure_ascii=False)

    logger.info("send_to_bot %s→%s ok (%s)", sender, ack.get("to"), workspace)
    _dm_sends_this_turn.set(_dm_sends_this_turn.get() + 1)
    return True, json.dumps(ack, ensure_ascii=False)
