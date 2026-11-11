# desktop/services/skills_service.py — manager de skills del workspace
"""Servicio Qt-free para el manager permanente de skills (Config → Skills).

Envuelve el ciclo de aprobación local (aprobar/rechazar con sidecar)
sin tocar Qt: la
pestaña solo renderiza. Aprobar exige el BODY PARSEADO (hash canónico) —
el mismo texto que load_skill entrega al agente.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

__all__ = ["list_workspace_skills", "approve_workspace_skill", "reject_workspace_skill"]

_ESTADO_ICONO = {
    "approved": "✅",
    "pending": "🕐",
    "rejected": "🚫",
    "changed": "⚠",
}


def list_workspace_skills(workspace: str) -> list[dict]:
    """Skills del workspace con su estado de aprobación (orden estable)."""
    from core.skills_approval import approval_status

    estados = approval_status(workspace)
    out: list[dict] = []
    for name in sorted(estados):
        estado = estados[name]
        out.append(
            {
                "name": name,
                "state": estado,
                "icon": _ESTADO_ICONO.get(estado, "?"),
            }
        )
    return out


def _body_of(workspace: str, name: str) -> str | None:
    from core.path_resolver import paths
    from core.skills import _skills_in_dir

    entries = _skills_in_dir(paths.workspace_skills_dir(workspace))
    entry = entries.get(name)
    return entry[2] if entry else None


def approve_workspace_skill(workspace: str, name: str) -> bool:
    """Aprueba con el hash del body parseado; False si la skill no existe."""
    from core.skills_approval import approve_skill

    body = _body_of(workspace, name)
    if body is None:
        return False
    approve_skill(workspace, name, body)
    logger.info("skill '%s' aprobada (ws=%s)", name, workspace)
    return True


def reject_workspace_skill(workspace: str, name: str) -> bool:
    from core.skills_approval import approval_status, reject_skill

    if name not in approval_status(workspace):
        return False
    reject_skill(workspace, name)
    logger.info("skill '%s' rechazada (ws=%s)", name, workspace)
    return True
