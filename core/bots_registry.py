# core/bots_registry.py — roster vivo y resolución de targets (contrato 08)
"""Roster de bots del workspace activo + resolución de targets de mensajería.

Contratos:
- El roster SIEMPRE se lee del schema del workspace activo (isla).
- Errores accionables con roster incluido para autocorrección del modelo.

El alias ``@`` resuelve al bot principal del workspace; sin principal
configurado, el alias se rechaza igual que un target desconocido.
"""

import logging

from core.bots import BotError, BotsService, validate_slug

logger = logging.getLogger(__name__)

ALIAS_PRINCIPAL = "@"


class UnknownTargetError(BotError):
    """Target inexistente — mensaje incluye el roster para autocorrección."""


def _roster_text(bots: list[dict], principal: str | None) -> str:
    names = ", ".join(f"@{b['slug']}" for b in bots)
    hint = f" (principal: @{principal})" if principal else ""
    return f"{names}{hint}" if names else "(workspace sin bots)"


class BotsRegistry:
    """Roster vivo por esquema activo; instanciable para tests."""

    def __init__(self) -> None:
        self._principal_slug: str | None = None

    # ── Configuración ────────────────────────────────────────────────

    def set_principal(self, slug: str | None) -> None:
        """Fija el bot que responde al alias ``@`` (bot 'principal')."""
        self._principal_slug = validate_slug(slug) if slug else None

    @property
    def principal(self) -> str | None:
        return self._principal_slug

    # ── Roster / estado ──────────────────────────────────────────────

    async def roster(self, include_disabled: bool = False) -> list[dict]:
        return await BotsService.list_bots(include_disabled=include_disabled)

    async def is_bot_managed(self) -> bool:
        """True cuando el workspace lleva bots gestionados (análogo al probe)."""
        try:
            bots = await self.roster(include_disabled=True)
        except Exception as e:
            logger.warning("probe de roster falló (fail-closed): %s", e)
            return False
        return len(bots) > 0


async def resolve_target(
    target: str,
    *,
    sender_slug: str | None = None,
    reg: BotsRegistry | None = None,
) -> dict:
    """Resuelve un target de mensajería contra el roster vivo.

    - Acepta slug directo, case-insensitive, con o sin ``@`` inicial.
    - Alias ``@`` → bot principal (si está configurado en ``reg`` o en la
      instancia por defecto).
    - Rechaza targets cross-machine ``<peer>/<agente>`` (fuera de alcance v1).
    - Self-DM bloqueado cuando ``sender_slug`` coincide con el destino.
    - Desconocido → UnknownTargetError con roster completo en el mensaje.

    Devuelve el dict del bot destino.
    """
    cfg = reg if reg is not None else registry
    raw = (target or "").strip()
    if not raw:
        raise UnknownTargetError("target vacío — indica el slug del bot destinatario")

    if "/" in raw:
        raise UnknownTargetError(
            f"targets cross-machine ('<peer>/<agente>') están fuera de alcance v1: '{raw}'"
        )

    candidate = raw.lstrip("@").lower() if raw != ALIAS_PRINCIPAL else ""
    if raw == ALIAS_PRINCIPAL:
        principal = cfg.principal
        if not principal:
            raise UnknownTargetError(
                "alias '@' sin bot principal configurado — usa un slug concreto"
            )
        candidate = principal

    if not candidate:
        raise UnknownTargetError(f"target inválido: '{target}'")

    bots = await BotsService.list_bots(include_disabled=False)
    match = next((b for b in bots if b["slug"].lower() == candidate), None)
    if match is None:
        raise UnknownTargetError(
            f"target desconocido: '{target}'. Roster disponible: "
            f"{_roster_text(bots, cfg.principal)}"
        )
    if sender_slug and match["slug"] == sender_slug:
        raise BotError(f"no puedes enviarte un DM a ti mismo (@{match['slug']})")
    return match


# Instancia por defecto del proceso (config principal compartida)
registry = BotsRegistry()
