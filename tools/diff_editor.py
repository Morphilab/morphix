"""Diff Editor — surgical code editing via unified diffs."""

import asyncio
import logging
import re

from agents.audit import log_operation
from core.config import settings
from core.path_resolver import paths

logger = logging.getLogger(__name__)


async def _diff_editor_tool(
    file_path: str = "",
    diff_content: str | None = None,
    content: str = "",
    action: str = "apply",
    workspace: str | None = None,
    project_root: str | None = None,
    path: str = "",
) -> dict:
    """Edita archivos mediante diffs unificados. Tool auto-registrada como 'diff_editor'.

    Args:
        file_path: Ruta del archivo a editar.
        diff_content: Contenido del diff unificado a aplicar (para action='apply').
        content: Alias de diff_content (aceptado por compatibilidad con LLM).
        action: 'apply' (aplicar diff) o 'create' (generar diff de cambios).
        workspace: Workspace activo.
        project_root: Directorio del proyecto.
        path: Alias de file_path (aceptado por compatibilidad con LLM).
    """
    if workspace is None:
        workspace = settings.active_workspace
    resolved_path = file_path or path
    resolved_content = diff_content or content or None
    if not resolved_path:
        return {"success": False, "output": "❌ file_path o path es requerido."}

    base = paths.memory_dir(workspace)
    if project_root:
        base = base / project_root

    target = base / resolved_path

    try:
        target.resolve().relative_to(base.resolve())
    except ValueError:
        return {"success": False, "output": "❌ Path inseguro: fuera del workspace."}

    if action == "apply":
        if not resolved_content:
            return {"success": False, "output": "❌ diff_content es requerido para action='apply'."}

        if not target.exists():
            return {"success": False, "output": f"❌ Archivo no encontrado: {resolved_path}"}

        original = await asyncio.to_thread(target.read_text, encoding="utf-8")
        lines = original.splitlines(keepends=True)
        new_lines = _apply_patch_lines(lines, resolved_content)

        if new_lines is None:
            return {
                "success": False,
                "output": "❌ No se pudo aplicar el diff. Puede que los números de línea no coincidan.",
            }

        new_content = "".join(new_lines)
        await asyncio.to_thread(target.write_text, new_content, encoding="utf-8")

        # Post-apply validation: ensure result is syntactically valid Python
        if resolved_path.endswith(".py"):
            try:
                compile(new_content, str(target), "exec")
            except SyntaxError as e:
                # Rollback to original — the diff produced invalid code
                await asyncio.to_thread(target.write_text, original, encoding="utf-8")
                logger.warning(
                    "diff_editor produced invalid Python for %s, rolled back: %s",
                    resolved_path,
                    e,
                )
                return {
                    "success": False,
                    "output": (
                        f"❌ Diff aplicado pero el resultado tiene errores de sintaxis "
                        f"({e.msg} en línea {e.lineno}). Se revirtió el cambio."
                    ),
                }

        log_operation("diff_editor_apply", str(target), success=True)
        return {"success": True, "output": f"✅ Diff aplicado correctamente a {resolved_path}."}

    if action == "create":
        if not target.exists():
            return {"success": False, "output": f"❌ Archivo no encontrado: {resolved_path}"}

        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "diff",
                "--",
                str(target),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(base),
            )
            stdout, _ = await proc.communicate()
            output = stdout.decode()
            return {
                "success": True,
                "output": output if output else "(sin cambios respecto al último commit)",
            }
        except Exception as e:
            return {"success": False, "output": f"❌ Error generando diff: {e}"}

    return {"success": False, "output": f"❌ Acción no soportada: {action}"}


def _apply_patch_lines(original_lines: list, diff_text: str) -> list | None:
    """Aplica un diff unificado simple a una lista de líneas.

    Soporta el formato estándar de diff:
        @@ -start,count +start,count @@
        - removed line
        + added line
    """
    try:
        import re

    except ImportError:
        return None

    lines = list(original_lines)
    hunks = re.findall(
        r"@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@\n?(.*?)(?=@@|\Z)",
        diff_text,
        re.DOTALL,
    )

    if not hunks:
        # Try to apply simple "replace X with Y" changes
        return _apply_simple_search_replace(original_lines, diff_text)

    offset = 0
    for old_start, old_count, _new_start, _new_count, body in hunks:
        old_idx = int(old_start) - 1 + offset
        old_cnt = int(old_count) if old_count else 1

        if old_idx < 0 or old_idx > len(lines):
            return None

        new_chunk = []
        for line in body.splitlines(keepends=True):
            if line.startswith("+"):
                new_chunk.append(line[1:])
            elif line.startswith("-"):
                continue
            elif line.startswith(" "):
                new_chunk.append(line[1:])

        del lines[old_idx : old_idx + old_cnt]
        for i, nl in enumerate(new_chunk):
            lines.insert(old_idx + i, nl)
        offset += len(new_chunk) - old_cnt

    return lines


def _apply_simple_search_replace(lines: list, diff_text: str) -> list | None:
    """Fallback: reemplazo simple de texto en el archivo."""
    for match in re.finditer(
        r"<<<<<<< ORIGINAL\n(.*?)=======\n(.*?)>>>>>>> REPLACEMENT", diff_text, re.DOTALL
    ):
        old = match.group(1).strip()
        new = match.group(2).strip()
        content = "".join(lines)
        if old in content:
            content = content.replace(old, new, 1)
            return list(content.splitlines(keepends=True))
    return None


# Registro directo en tools_registry
from tools.registry import tools_registry

tools_registry.register("diff_editor")(_diff_editor_tool)
