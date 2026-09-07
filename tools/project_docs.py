# tools/project_docs.py
"""project_docs — Project Knowledge Base (PKB), solo lectura v1.

El agente consulta el conocimiento curado del proyecto:
`workspaces/<ws>/knowledge/<categoria>/*.md`. Acciones: list/read/search/
inject (inject devuelve el CONTENIDO top-k relevante para la tarea).

Diseño reutilización-first: la búsqueda es por palabras (determinista);
el indexado semántico vive en MemoryManager (prefijo kb_ protegido) — sin
subsistema nuevo. Read-only: ningún action escribe (los docs se editan a
mano o por el usuario, FS como source of truth).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["make_handler", "register"]

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _current_workspace() -> str:
    from core.workspaces import get_global_workspaces

    return get_global_workspaces().current or "main"


def _knowledge_root(workspace: str | None) -> Path:
    from core.path_resolver import paths

    return paths.workspace_knowledge_dir(workspace or _current_workspace())


def _categories(root: Path) -> list[dict]:
    out: list[dict] = []
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        docs = sorted(p.stem for p in d.glob("*.md") if p.is_file())
        if docs:
            out.append({"category": d.name, "docs": [{"name": n} for n in docs]})
    return out


def _read(root: Path, category: str, name: str) -> dict:
    if not _NAME_RE.match(name or ""):
        return {"success": False, "error": "name inválido (patrón simple, sin rutas)"}
    path = root / (category or "") / f"{name}.md"
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        return {"success": False, "error": f"documento '{category}/{name}' no existe"}
    return {
        "success": True,
        "category": category,
        "name": name,
        "content": path.read_text(encoding="utf-8"),
    }


def _search(root: Path, query: str, category: str | None) -> list[dict]:
    tokens = re.findall(r"[a-z0-9_áéíóúñ]+", (query or "").lower())
    results: list[dict] = []
    base = root / category if category else root
    files = list(base.rglob("*.md")) if base.is_dir() else []
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace").lower()
        if not tokens:
            score = 0.0
        else:
            score = sum(1 for t in tokens if t in text) / len(tokens)
        if not tokens or score > 0:
            rel = f.relative_to(root)
            results.append(
                {
                    "category": rel.parts[0] if len(rel.parts) > 1 else "(root)",
                    "name": rel.stem,
                    "score": round(score, 2),
                    "content": f.read_text(encoding="utf-8", errors="replace"),
                }
            )
    return sorted(results, key=lambda r: r["score"], reverse=True)


def make_handler():
    async def execute(
        action: str,
        workspace: str | None = None,
        category: str | None = None,
        name: str | None = None,
        query: str | None = None,
        k: int = 3,
    ) -> dict:
        root = _knowledge_root(workspace)
        if action == "list":
            return {"success": True, "root": str(root), "categories": _categories(root)}
        if action == "read":
            return _read(root, category or "", name or "")
        if action == "search":
            results = _search(root, query or "", category)
            return {"success": True, "count": len(results), "results": results}
        if action == "inject":
            results = _search(root, query or "", category)[: max(1, min(int(k or 3), 10))]
            return {
                "success": True,
                "docs": [
                    {"category": r["category"], "name": r["name"], "content": r["content"]}
                    for r in results
                ],
            }
        return {"success": False, "error": f"acción inválida: {action} (solo lectura v1)"}

    return execute


def register(registry):
    registry.register("project_docs")(make_handler())


# Auto-registro (mismo patrón que memory_inspector/test_runner)
from tools.registry import tools_registry  # noqa: E402

register(tools_registry)  # noqa: E402
