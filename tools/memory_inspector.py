"""memory_inspector — superficie de inspección/borrado de memoria persistente.

Da al agente visibilidad/edición controlada de
la memoria del workspace activo. El borrado requiere confirm_delete=true y
hay claves protegidas (perfil/kairos) inaccesibles.
"""

import logging

logger = logging.getLogger(__name__)

_DENIED_READ = {"security_private"}
_DENIED_DELETE = {
    "user_profile",
    "user_profile_last_update",
    "last_task_summary",
    "security_private",
}


def make_handler():
    async def execute(action: str, key: str = "", confirm_delete: bool = False) -> dict:
        from core.memory.manager import memory

        if action == "list":
            with memory._lock:
                items = [{"key": k, "chars": len(str(v))} for k, v in memory.documents]
            return {"success": True, "count": len(items), "keys": items}

        if action == "read":
            if key in _DENIED_READ:
                return {
                    "success": False,
                    "error": f"'{key}' es de auditoría interna",
                }
            val = memory.read(key)
            if val is None:
                return {"success": False, "error": f"clave '{key}' no encontrada"}
            return {"success": True, "key": key, "value": val}

        if action == "delete":
            if key in _DENIED_DELETE:
                return {"success": False, "error": f"'{key}' está protegida"}
            if not confirm_delete:
                return {"success": False, "error": "requiere confirm_delete=true"}
            with memory._lock:
                deleted_from_index = memory._ids.pop(key, None)
                if deleted_from_index is not None:
                    import numpy as np

                    try:
                        memory.index.remove_ids(np.array([deleted_from_index], dtype="int64"))
                        memory._id_to_key.pop(deleted_from_index, None)
                    except Exception:
                        logger.warning("remove_ids falló en memory_inspector", exc_info=True)
                    memory._next_id = max(memory._next_id, deleted_from_index + 1)
                memory.documents = [d for d in memory.documents if d[0] != key]
                memory._access_log.pop(key, None)

            ws = memory.active_workspace or "main"
            f = memory.base_dir / ws / f"{key}.md"
            try:
                if f.exists():
                    f.unlink()
            except OSError:
                logger.warning("No se pudo borrar %s", f, exc_info=True)
            return {"success": True, "deleted": key}

        return {"success": False, "error": f"acción inválida: {action}"}

    return execute


def register(registry):
    registry.register("memory_inspector")(make_handler())


# Auto-registro (mismo patrón que test_runner/diff_editor)
from tools.registry import tools_registry  # noqa: E402

register(tools_registry)  # noqa: E402
