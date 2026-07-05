"""Code Search — búsqueda de patrones regex en archivos del proyecto."""

import asyncio
import logging
import re

from agents.audit import log_operation
from core.config import settings
from core.path_resolver import paths
from tools.registry import tools_registry

logger = logging.getLogger(__name__)


async def _code_search_tool(
    pattern: str,
    path: str = ".",
    include: str = "*.py",
    max_results: int = 20,
    workspace: str | None = None,
    project_root: str | None = None,
    **kwargs,
) -> str:
    """Busca un patrón regex en archivos del proyecto.

    Args:
        pattern: Patrón regex a buscar.
        path: Directorio relativo de búsqueda (defecto: raíz del proyecto).
        include: Glob de archivos a incluir (defecto: *.py).
        max_results: Máximo de resultados.
        workspace: Workspace activo.
        project_root: Directorio del proyecto.

    Returns:
        Resultados formateados: archivo:línea → contenido.
    """
    if workspace is None:
        workspace = settings.active_workspace
    base = paths.memory_dir(workspace)
    if project_root:
        base = base / project_root
    search_dir = (base / path).resolve()

    try:
        search_dir.relative_to(base.resolve())
    except ValueError:
        return "❌ Ruta fuera del workspace"

    if not search_dir.exists():
        return f"❌ Directorio no encontrado: {path}"

    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"❌ Regex inválido: {e}"

    results = []
    files_scanned = 0

    for file_path in search_dir.rglob(include):
        if file_path.is_file() and not any(
            p in file_path.parts
            for p in (".git", "__pycache__", "node_modules", ".venv", ".undo", ".redo")
        ):
            files_scanned += 1
            if files_scanned > 500:
                break
            try:
                content = await asyncio.to_thread(
                    file_path.read_text, encoding="utf-8", errors="replace"
                )
            except Exception:
                continue

            found = list(compiled.finditer(content))
            for match in found[-5:]:  # max 5 per file
                line_no = content[: match.start()].count("\n") + 1
                line = content.split("\n")[line_no - 1].strip()[:200]
                rel = file_path.relative_to(search_dir)
                results.append(f"{rel}:{line_no}: {line}")
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break

    if not results:
        return f"🔍 Sin resultados para '{pattern}' en {files_scanned} archivos."

    log_operation("code_search", pattern[:200], success=True)
    return (
        f"🔍 {len(results)} resultados para '{pattern}' ({files_scanned} archivos):\n\n"
        + "\n".join(results)
    )


tools_registry.register("code_search")(_code_search_tool)
