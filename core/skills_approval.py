"""Aprobación de skills locales (workspace) — H8 cierre.

Estado en JSON sidecar `workspaces/<ws>/skills/.aprobaciones.json`:
  {"<name>": {"sha256": "...", "date": "ISO"}}   → aprobada
  {"<name>": {"rejected": true, "date": "ISO"}}  → rechazada

El hash del CONTENIDO manda: skill aprobada cuyo archivo cambió → "changed"
(= pendiente de re-aprobación). Sin entrada → "pending". Global skills NO
pasan por aquí (siempre visibles). Diseño aprobado por el usuario (4 defaults).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Literal

from core.path_resolver import paths

logger = logging.getLogger(__name__)

ApprovalState = Literal["approved", "rejected", "pending", "changed"]


def _sidecar(workspace: str):
    return paths.workspace_skills_dir(workspace) / ".aprobaciones.json"


def load_approvals(workspace: str) -> dict[str, dict]:
    p = _sidecar(workspace)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        logger.warning("sidecar de aprobaciones corrupto — se ignora: %s", p)
        return {}


def _save(workspace: str, data: dict[str, dict]) -> None:
    p = _sidecar(workspace)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def approve_skill(workspace: str, name: str, content: str) -> None:
    data = load_approvals(workspace)
    data[name] = {
        "sha256": _sha(content),
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    _save(workspace, data)


def reject_skill(workspace: str, name: str) -> None:
    data = load_approvals(workspace)
    data[name] = {
        "rejected": True,
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    _save(workspace, data)


def approval_status(workspace: str) -> dict[str, ApprovalState]:
    """Estado por skill workspace existente en disco (solo lectura).

    El hash canónico es sobre el BODY PARSEADO (frontmatter fuera, .strip())
    — exactamente lo que `load_skill(...).body` entrega al agente. Aprobar
    con otro texto (p.ej. el archivo crudo) produciría "changed" eterno.

    Aprobación implícita: `_bootstrap_workspace_skills`
    COPIA las skills globales a cada workspace (aditivo) — una copia byte-
    idéntica a la global del mismo nombre se considera aprobada (es el propio
    catálogo del usuario, no contenido de terceros). El rechazo explícito del
    usuario prevalece sobre esta regla.
    """
    from core.skills import Paths, _skills_in_dir

    ws_dir = paths.workspace_skills_dir(workspace)
    saved = load_approvals(workspace)
    globales = _skills_in_dir(Paths.templates_skills_dir())
    out: dict[str, ApprovalState] = {}
    for name, (_path, _front, body) in _skills_in_dir(ws_dir).items():
        entry = saved.get(name)
        if entry is not None and entry.get("rejected"):
            out[name] = "rejected"
        elif name in globales and globales[name][2] == body:
            out[name] = "approved"  # copia idéntica del catálogo global
        elif entry is None:
            out[name] = "pending"
        elif entry.get("sha256") == _sha(body):
            out[name] = "approved"
        else:
            out[name] = "changed"
    return out
