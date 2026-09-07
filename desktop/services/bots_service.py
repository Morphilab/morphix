# desktop/services/bots_service.py — lógica GUI de la pestaña Bots (sin Qt)
"""Servicio testable sin Qt para bots_tab.py (convención desktop/services).

Responsabilidades:
- Roster con previews desde LA MISMA resolución canónica que el clic:
  preview identity == click identity por construcción.
- open_bot_chat: adopt-before-mint vía core.bots_chat + sweep posterior.
- Guard: bloquear "nueva conversación" dentro del chat eterno.
"""

import logging

from core import bots_chat
from core.bots import BotsService

logger = logging.getLogger(__name__)

GUARD_NEW_IN_CANONICAL_MSG = (
    "Chat eterno del bot: no admite nueva conversación. Usa compactar si el "
    "contexto creció demasiado."
)


async def roster_with_previews() -> list[dict]:
    """Bots del workspace + su registro canónico resuelto server-side.

    Cada entrada trae ``canonical`` (dict|None) leído con la MISMA función que
    usará el clic — jamás un puntero persistido.
    """
    bots = await BotsService.list_bots(include_disabled=True)
    out: list[dict] = []
    for b in bots:
        canonical = await bots_chat.resolve_canonical(b["slug"])
        out.append({**b, "canonical": canonical})
    return out


async def open_bot_chat(slug: str) -> dict:
    """Abre o acuña el chat eterno del bot y reconcilia ocultos."""
    row = await bots_chat.ensure_open(slug)
    await run_sweep()
    return row


async def run_sweep() -> int:
    """Sweep reconciliador + reparación defensiva de duplicados."""
    changed = await bots_chat.sweep_hidden_bot_chats()
    demoted = await bots_chat.repair_duplicate_canonicals()
    return int(changed) + int(demoted)


def new_conversation_guard(in_canonical: bool) -> tuple[bool, str | None]:
    """Guard: dentro del canónico, 'nueva conversación' está prohibida."""
    if in_canonical:
        return False, GUARD_NEW_IN_CANONICAL_MSG
    return True, None


def derive_clone_slug(base_slug: str, existing: set[str]) -> str:
    """Convención <base>-2, <base>-3… para clonado desde la pestaña."""
    n = 1
    while f"{base_slug}-{n + 1}" in existing:
        n += 1
    return f"{base_slug}-{n + 1}"
