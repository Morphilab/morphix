"""Vista unificada de workflows para la GUI: legacy y DSL con la misma forma.

La GUI no debe conocer dos formatos. ``load_workflow_view`` devuelve un dict
plano con los campos que la UI consume (description/agents/tools/project/
skills); para documentos DSL deriva los mismos campos del árbol de steps.
``project_required`` se normaliza AQUÍ para que el guard de orquestación no
dependa de la rama legacy.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["load_workflow_view", "is_dsl", "summarize_dsl"]


def is_dsl(doc: dict | None) -> bool:
    """True si el documento crudo es un workflow DSL (campo ``version``)."""
    return isinstance(doc, dict) and "version" in doc


def summarize_dsl(doc: dict) -> dict:
    """Resumen legible de un doc DSL para el panel de detalle de plantilla."""
    steps = doc.get("steps") or []
    kinds: dict[str, int] = {}

    def _count_all(step_list: list) -> None:
        for step in step_list:
            if not isinstance(step, dict):
                continue
            kind = str(step.get("kind", "?"))
            kinds[kind] = kinds.get(kind, 0) + 1
            body = step.get("body") or []
            _count_all(body)
            branches = step.get("branches")
            if isinstance(branches, list):
                for branch in branches:
                    _count_all((branch or {}).get("steps") or [])
            elif isinstance(branches, dict):
                for sub in branches.values():
                    _count_all(sub or [])

    _count_all(steps)
    return {
        "name": doc.get("name"),
        "description": doc.get("description", ""),
        "dsl": True,
        "agents": (doc.get("agents") or {}).get("allowed") or [],
        "tools": (doc.get("tools") or {}).get("allowed") or [],
        "project_required": bool((doc.get("project") or {}).get("required")),
        "skills": bool(doc.get("skills", False)),
        "step_kinds": dict(sorted(kinds.items())),
    }


def load_workflow_view(workspace: str | None, workflow_name: str) -> dict[str, Any] | None:
    """Carga el workflow (legacy o DSL) y devuelve una vista uniforme.

    - None si no existe (callers actuales tratan None como "sin plantilla").
    - Campos garantizados: description, agents_allowed, tools_allowed,
      project_required, skills, dsl (bool), kind_summary (solo DSL).
    """
    from orchestration.loader import load_workflow_document

    doc = load_workflow_document(workspace, workflow_name)
    if doc is None:
        return None

    if is_dsl(doc):
        summary = summarize_dsl(doc)
        return {
            "description": summary["description"],
            "agents_allowed": summary["agents"],
            "tools_allowed": summary["tools"],
            "project_required": summary["project_required"],
            "skills": summary["skills"],
            "dsl": True,
            "kind_summary": summary["step_kinds"],
            "raw": doc,
        }

    return {
        "description": doc.get("description", ""),
        "agents_allowed": (doc.get("agents") or {}).get("allowed") or [],
        "tools_allowed": (doc.get("tools") or {}).get("allowed") or [],
        "project_required": bool((doc.get("project") or {}).get("required")),
        "skills": bool(doc.get("skills", False)),
        "dsl": False,
        "kind_summary": None,
        "raw": doc,
    }
