"""Diff Editor — surgical code editing via unified diffs."""

import asyncio
import logging
import re
from pathlib import Path

from agents.audit import log_operation
from core.config import settings
from core.path_resolver import paths

logger = logging.getLogger(__name__)

# lock por-ruta — cierra la ventana RMW (read→apply→write) ante
# dos workflows editando el mismo archivo (lost-update).
_file_locks: dict[str, asyncio.Lock] = {}


async def _diff_editor_tool(
    file_path: str = "",
    diff_content: str | None = None,
    content: str = "",
    *,
    action: str,
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
            Obligatorio y sin default: sin él la función ejecutaría
            su rama de escritura silenciosamente → TypeError fail-closed.
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
    root_resolved = base.resolve()
    if project_root:
        # un project_root ABSOLUTO reemplazaría la base vía pathlib y el
        # containment contra ella sería auto-consistente (escritura arbitraria).
        pr = Path(str(project_root)).expanduser()
        if pr.is_absolute():
            return {
                "success": False,
                "output": "❌ project_root debe ser RELATIVO al workspace.",
            }
        base = base / pr

    target = base / resolved_path

    try:
        # Containment SIEMPRE vs la raíz del workspace (no vs base ya
        # desplazada) — también rechaza symlinks que apunten fuera.
        target.resolve().relative_to(root_resolved)
    except ValueError:
        return {"success": False, "output": "❌ Path inseguro: fuera del workspace."}

    if action == "apply":
        if not resolved_content:
            return {"success": False, "output": "❌ diff_content es requerido para action='apply'."}

        if not target.exists():
            return {"success": False, "output": f"❌ Archivo no encontrado: {resolved_path}"}

        async with _file_locks.setdefault(str(target.resolve()), asyncio.Lock()):
            try:
                # Referencia ANTES del read: si el mtime cambia antes del
                # write, alguien más editó el archivo durante la ventana.
                mtime0 = target.stat().st_mtime_ns
            except OSError:
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

            def _write_if_unmodified() -> str | None:
                """Escribe solo si nadie más tocó el archivo durante la ventana."""
                if target.stat().st_mtime_ns != mtime0:
                    return "modified"
                target.write_text(new_content, encoding="utf-8")
                return None

            # backup para undo ANTES del write (paridad file_manager).
            try:
                from core.change_tracker import get_tracker

                get_tracker(workspace, project_root).save_before_write(resolved_path)
            except Exception:
                logger.warning("No se pudo guardar backup en change_tracker", exc_info=True)

            conflict = await asyncio.to_thread(_write_if_unmodified)
            if conflict == "modified":
                return {
                    "success": False,
                    "output": (
                        f"⚠️ Archivo modificado concurrentemente mientras se aplicaba "
                        f"el diff: {resolved_path}. Reintenta con el contenido actual."
                    ),
                }

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
            from tools._subprocess import communicate_or_kill

            stdout, stderr = await communicate_or_kill(proc, timeout=120.0)
            output = stdout.decode()
            err_text = stderr.decode().strip()
            # respetar returncode/stderr — sin git repo el diff vacío
            # NO es "(sin cambios)", es un fallo de entorno.
            if proc.returncode != 0:
                detail = err_text.splitlines()[-1] if err_text else f"git exit {proc.returncode}"
                return {"success": False, "output": f"❌ git diff falló: {detail}"}
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
        expected_old: list[str] = []  # líneas que serán eliminadas ('-' y contexto)
        for raw in body.splitlines(keepends=True):
            stripped = raw.rstrip("\r\n")
            if raw.startswith("+"):
                new_chunk.append(raw[1:])
            elif raw.startswith("-"):
                expected_old.append(raw[1:].rstrip("\r\n"))
            elif raw.startswith(" "):
                content = raw[1:]
                new_chunk.append(content)
                expected_old.append(content.rstrip("\r\n"))
            elif raw.strip() == "":
                # contexto VACÍO sin prefijo espacio (formato laxo)
                # se trata como contexto — jamás se descarta silenciosamente.
                new_chunk.append(raw)
                expected_old.append(stripped)

        # verificar que las líneas a borrar COINCIDEN con disco
        if expected_old:
            actual = [ln.rstrip("\r\n") for ln in lines[old_idx : old_idx + len(expected_old)]]
            if actual != expected_old:
                logger.warning(
                    "Hunk desalineado en línea %d: esperado %r, disco %r",
                    old_idx + 1,
                    expected_old[:3],
                    actual[:3],
                )
                return None
            delete_cnt = len(expected_old)
        else:
            delete_cnt = 0

        del lines[old_idx : old_idx + delete_cnt]
        for i, nl in enumerate(new_chunk):
            lines.insert(old_idx + i, nl)
        offset += len(new_chunk) - delete_cnt

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
