"""
File Manager - Versión Profesional y Robusta
- Alias file_path / path
- Normalización inteligente de rutas:
  1. Elimina el prefijo completo project_root si ya está presente.
  2. Elimina el nombre del proyecto como primer componente si coincide con el último segmento de project_root.
- Validación sintáctica de archivos .py antes de escribir
- I/O vía asyncio.to_thread() para no bloquear el event loop
"""

import asyncio
import logging
import re
import threading

from core.config import settings
from core.path_resolver import paths

logger = logging.getLogger(__name__)

SAFE_BASE = paths.memory_base()

# registro de escrituras por (root, path) → writers activos.
# Detecta que DOS workflows concurrentes escriban el mismo archivo en el mismo
# proyecto (aviso, no bloqueo). Keyed por root para evitar falsos positivos
# entre proyectos distintos.
_writer_registry: dict[tuple[str, str], set[int]] = {}
_writer_lock = threading.Lock()
_WRITER_REGISTRY_MAX = 4000


def _current_writer_id() -> int:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return id(task) if task is not None else id(threading.current_thread())


def record_file_write(root: str, path: str) -> bool:
    """Registra que el run actual escribió (root, path).

    Retorna True si OTRO run CONCURRENTE (aún activo) ya había escrito ese
    mismo archivo — señal de colisión cross-workflow en el mismo proyecto.
    Keyed por root para evitar falsos positivos entre proyectos distintos.
    El mismo run re-escribiendo no genera colisión.
    """
    from tools.orchestrator import get_file_write_run

    writer = get_file_write_run()
    if writer is None:
        writer = _current_writer_id()
    from core.workspaces import is_run_active

    key = (str(root), str(path))
    with _writer_lock:
        writers = _writer_registry.get(key)
        if writers is None:
            if len(_writer_registry) >= _WRITER_REGISTRY_MAX:
                _writer_registry.clear()
            writers = set()
            _writer_registry[key] = writers
        collision = any(w != writer and is_run_active(w) for w in writers)
        writers.add(writer)
        return collision


def clear_file_write_registry() -> None:
    """Limpia el registro de escrituras (tests / reinicio de sesión)."""
    with _writer_lock:
        _writer_registry.clear()


class FileManager:
    @staticmethod
    async def execute(
        action: str,
        path: str | None = None,
        file_path: str | None = None,  # alias para compatibilidad con LLM
        content: str = "",
        workspace: str | None = None,
        project_root: str | None = None,
        **kwargs,
    ) -> str:
        if workspace is None:
            workspace = settings.active_workspace
        if not re.match(r"^[a-z][a-z0-9_]*$", workspace):
            return (
                f"❌ Nombre de workspace inválido: '{workspace}'. "
                "Solo se permiten minúsculas, números y guiones bajos."
            )
        # Normalize: accept file_path as alias for path
        if file_path and not path:
            path = file_path

        if not path:
            return "❌ Error: se requiere parámetro 'path' o 'file_path'"

        # call-time — immune a recargas del módulo (dualidad SAFE_BASE)
        base = paths.memory_base() / workspace

        # project_root intelligence with prefix normalization
        if project_root:
            project_root = paths.normalize_project_root(project_root)
            path = paths.normalize_path(path, project_root)

            full_project = (base / project_root).resolve()  # type: ignore[operator]
        else:
            full_project = base

        full_path = (full_project / path).resolve()

        # Security: never leave the workspace
        try:
            full_path.relative_to(base.resolve())
        except ValueError:
            raise ValueError(f"Ruta no permitida: {path}")

        # Create directories automatically
        full_path.parent.mkdir(parents=True, exist_ok=True)

        if action == "read":
            if full_path.is_dir():
                skip = {".git", "node_modules", "__pycache__", ".venv", ".undo", ".redo"}
                entries = sorted(
                    p.name + ("/" if p.is_dir() else "")
                    for p in full_path.iterdir()
                    if p.name not in skip
                )
                listing = "\n".join(entries) if entries else "(directorio vacío)"
                return f"📁 Contenido de '{path}':\n{listing}"
            if not full_path.is_file():
                # Friendly response: reading a not-yet-existing file is an
                # expected step of the read-before-write pattern. Guide the
                # model to create the file instead of reporting a failure.
                return (
                    f"ℹ️ El archivo '{path}' no existe todavía (es un archivo nuevo). "
                    "Si la tarea es crearlo, usa action='write' con el contenido. "
                    "Si esperabas que existiera, verifica el nombre exacto."
                )
            content = await asyncio.to_thread(full_path.read_text, encoding="utf-8")
            from core.output_bounds import TOOL_OUTPUT_MAX_BYTES, bound_output

            return bound_output(content, TOOL_OUTPUT_MAX_BYTES, tail_bytes=1_000).text

        elif action == "write":
            # Save backup for undo before overwriting
            if full_path.is_file():
                from core.change_tracker import get_tracker

                get_tracker(workspace, project_root or None).save_before_write(path)

            # Syntax validation for Python files
            if full_path.suffix == ".py" and content:
                try:
                    compile(content, str(full_path), "exec")
                except SyntaxError as e:
                    return f"❌ Error de sintaxis en '{path}': {str(e)}. El archivo NO fue escrito."

            await asyncio.to_thread(full_path.write_text, content, encoding="utf-8")
            logger.info(f"✅ Archivo escrito: {full_path}")
            if record_file_write(str(full_project), path):
                logger.warning("⚠️ Colisión de archivo en '%s': escrito por otra sesión", path)
                return (
                    f"⚠️ [colisión] '{path}' escrito por otra sesión activa — verifica coherencia."
                )
            return f"Archivo '{path}' escrito correctamente."

        elif action == "append":
            if full_path.is_file():
                from core.change_tracker import get_tracker

                get_tracker(workspace, project_root or None).save_before_write(path)

            def _do_append():
                with open(full_path, "a", encoding="utf-8") as f:
                    f.write(content)

            await asyncio.to_thread(_do_append)
            if record_file_write(str(full_project), path):
                logger.warning("⚠️ Colisión de archivo en '%s': append por otra sesión", path)
                return f"⚠️ [colisión] '{path}' modificado por otra sesión activa — verifica coherencia."
            return f"Contenido añadido a '{path}'."

        elif action in ("delete", "remove"):
            if full_path.is_file():
                await asyncio.to_thread(full_path.unlink)
                from agents.audit import log_operation

                log_operation("file_delete", str(full_path), success=True)
                logger.info(f"🗑️ Archivo eliminado: {full_path}")
                return f"Archivo '{path}' eliminado."
            return f"ℹ️ El archivo '{path}' no existe — no hay nada que eliminar."

        else:
            raise ValueError(f"Acción '{action}' no soportada.")


from tools.registry import tools_registry


@tools_registry.register("file_manager")
async def file_manager_tool(action: str = "", **kwargs) -> str:
    # DeepSeek sometimes emits the tool call without the 'action' field. Infer the
    # intention instead of silently failing: if 'content' is provided the
    # intention is to write; in any other case, read.
    if not action:
        action = "write" if kwargs.get("content") else "read"
    if not kwargs.get("path") and not kwargs.get("file_path"):
        logger.debug(
            f"file_manager llamada sin path. action='{action}', kwargs={list(kwargs.keys())}"
        )
        hint = ""
        project_root = kwargs.get("project_root")
        if project_root:
            from core.path_resolver import paths

            workspace = kwargs.get("workspace", settings.active_workspace)
            proj_dir = paths.project_dir(workspace, project_root)  # M27
            if proj_dir.exists():
                files = [str(p.relative_to(proj_dir)) for p in proj_dir.rglob("*") if p.is_file()]
                if files:
                    hint = f"\nArchivos disponibles en {project_root}: {', '.join(files[:15])}"
                    if len(files) > 15:
                        hint += f" (+{len(files) - 15} más)"
        return "❌ file_manager requiere un parámetro 'path' o 'file_path'." + hint
    return await FileManager.execute(action, **kwargs)
