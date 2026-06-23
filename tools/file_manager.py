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

from core.path_resolver import paths

logger = logging.getLogger(__name__)

SAFE_BASE = paths.memory_base()


class FileManager:
    @staticmethod
    async def execute(
        action: str,
        path: str | None = None,
        file_path: str | None = None,  # alias para compatibilidad con LLM
        content: str = "",
        workspace: str = "main",
        project_root: str | None = None,
        **kwargs,
    ) -> str:
        # Normalize: accept file_path as alias for path
        if file_path and not path:
            path = file_path

        if not path:
            return "❌ Error: se requiere parámetro 'path' o 'file_path'"

        base = SAFE_BASE / workspace

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
                raise FileNotFoundError(f"Archivo no encontrado: {path}")
            return await asyncio.to_thread(full_path.read_text, encoding="utf-8")

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
            return f"Archivo '{path}' escrito correctamente."

        elif action == "append":
            if full_path.is_file():
                from core.change_tracker import get_tracker

                get_tracker(workspace, project_root or None).save_before_write(path)

            def _do_append():
                with open(full_path, "a", encoding="utf-8") as f:
                    f.write(content)

            await asyncio.to_thread(_do_append)
            return f"Contenido añadido a '{path}'."

        elif action in ("delete", "remove"):
            if full_path.is_file():
                await asyncio.to_thread(full_path.unlink)
                from agents.audit import log_operation

                log_operation("file_delete", str(full_path), success=True)
                logger.info(f"🗑️ Archivo eliminado: {full_path}")
                return f"Archivo '{path}' eliminado."
            raise FileNotFoundError(f"Archivo no encontrado: {path}")

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

            workspace = kwargs.get("workspace", "main")
            proj_dir = paths.memory_dir(workspace) / project_root
            if proj_dir.exists():
                files = [str(p.relative_to(proj_dir)) for p in proj_dir.rglob("*") if p.is_file()]
                if files:
                    hint = f"\nArchivos disponibles en {project_root}: {', '.join(files[:15])}"
                    if len(files) > 15:
                        hint += f" (+{len(files) - 15} más)"
        return "❌ file_manager requiere un parámetro 'path' o 'file_path'." + hint
    return await FileManager.execute(action, **kwargs)
