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
# dos workflows editando el mismo archivo (lost-update). Acotado: las
# entradas liberadas se purgan al superar el tope.
_MAX_FILE_LOCKS = 2000
_file_locks: dict[str, asyncio.Lock] = {}


def _get_file_lock(key: str) -> asyncio.Lock:
    """Lock por-ruta con purga de entradas liberadas al superar el tope."""
    lock = _file_locks.get(key)
    if lock is None:
        if len(_file_locks) >= _MAX_FILE_LOCKS:
            for vieja in [k for k, v in _file_locks.items() if not v.locked()]:
                del _file_locks[vieja]
                if len(_file_locks) < _MAX_FILE_LOCKS:
                    break
        lock = _file_locks.setdefault(key, asyncio.Lock())
    return lock


def _path_from_diff(diff_text: str) -> str | None:
    """Deriva la ruta destino de un diff unificado de UN archivo.

    El modelo a veces manda solo el diff (el path vive en el header
    '+++ b/ruta'); sin esto la tool reintentaba 3 veces el mismo error.
    Varios archivos distintos ⇒ None (ambigüedad: el caller debe pasar path).
    """

    def _clean(raw: str) -> str:
        p = raw.split("\t", 1)[0].strip()
        if p.startswith(("a/", "b/")):
            p = p[2:]
        return p

    plus: list[str] = []
    minus: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("@@"):
            break  # los headers viven antes del primer hunk
        if line.startswith("+++ "):
            p = _clean(line[4:])
            if p and p != "/dev/null":
                plus.append(p)
        elif line.startswith("--- "):
            p = _clean(line[4:])
            if p and p != "/dev/null":
                minus.append(p)
    candidates = plus or minus
    unique = list(dict.fromkeys(candidates))
    return unique[0] if len(unique) == 1 else None


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
        file_path: Ruta del archivo a editar; si falta, se deriva del header
            '+++ b/<ruta>' del diff (un único archivo).
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
    if not resolved_path and resolved_content:
        resolved_path = _path_from_diff(resolved_content) or ""
    if not resolved_path:
        return {
            "success": False,
            "output": (
                "❌ file_path o path es requerido (o incluí un único header "
                "'+++ b/<ruta>' en el diff)."
            ),
        }

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

        async with _get_file_lock(str(target.resolve())):
            try:
                # Referencia ANTES del read: si el mtime cambia antes del
                # write, alguien más editó el archivo durante la ventana.
                mtime0 = target.stat().st_mtime_ns
            except OSError:
                return {"success": False, "output": f"❌ Archivo no encontrado: {resolved_path}"}

            original = await asyncio.to_thread(target.read_text, encoding="utf-8")

            lines = original.splitlines(keepends=True)
            new_lines, mismatch = _apply_patch_lines_detailed(lines, resolved_content)

            if new_lines is None:
                return {
                    "success": False,
                    "output": f"❌ No se pudo aplicar el diff. {mismatch}",
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

    Contrato histórico: las líneas resultantes o None. El detalle del fallo
    vive en `_apply_patch_lines_detailed` (consumido por la tool para
    devolverle al modelo las líneas reales del disco).
    """
    result, _detail = _apply_patch_lines_detailed(original_lines, diff_text)
    return result


def _apply_patch_lines_detailed(original_lines: list, diff_text: str) -> tuple[list | None, str]:
    """Aplica el diff y devuelve `(líneas | None, detalle del fallo)`.

    Soporta el formato estándar de diff:
        @@ -start,count +start,count @@
        - removed line
        + added line

    El detalle viaja al modelo: un "hunk desalineado" sin las líneas reales
    del disco lo hacía reintentar el mismo diff a ciegas.
    """
    try:
        import re

    except ImportError:
        return None, "el motor de regex no está disponible"

    lines = list(original_lines)
    hunks = re.findall(
        r"@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@\n?(.*?)(?=@@|\Z)",
        diff_text,
        re.DOTALL,
    )

    if not hunks:
        # Try to apply simple "replace X with Y" changes
        replaced = _apply_simple_search_replace(original_lines, diff_text)
        if replaced is None:
            return None, (
                "No se encontró el bloque a reemplazar. Verifica que el texto "
                "del diff exista tal cual en el archivo (o usa file_manager.write)."
            )
        return replaced, ""

    offset = 0
    for old_start, old_count, _new_start, _new_count, body in hunks:
        old_idx = int(old_start) - 1 + offset
        old_cnt = int(old_count) if old_count else 1

        if old_idx < 0 or old_idx > len(lines):
            return None, (
                f"Hunk fuera de rango: apunta a la línea {old_idx + 1} de un "
                f"archivo con {len(lines)} líneas."
            )

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
                detail = (
                    f"Hunk desalineado en línea {old_idx + 1}: el diff espera "
                    f"{expected_old[:3]!r} pero en disco hay {actual[:3]!r}. "
                    "Re-lee el archivo y ajusta el hunk (o usa file_manager.write)."
                )
                logger.warning(detail)
                return None, detail
            delete_cnt = len(expected_old)
        else:
            delete_cnt = 0

        del lines[old_idx : old_idx + delete_cnt]
        for i, nl in enumerate(new_chunk):
            lines.insert(old_idx + i, nl)
        offset += len(new_chunk) - delete_cnt

    return lines, ""


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
