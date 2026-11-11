"""MemoriaService — lógica del tab Memoria (Qt-free).

Envuelve los handlers de tools/memory_inspector.py para que la GUI no
conozca detalles del singleton de memoria. El borrado SIEMPRE va con
confirm_delete=True (la confirmación humana vive en la GUI).
"""

from __future__ import annotations

from tools.memory_inspector import make_handler

_execute = make_handler()


async def list_keys() -> list[dict]:
    res = await _execute(action="list")
    return list(res.get("keys") or []) if res.get("success") else []


async def read_key(key: str) -> str | None:
    res = await _execute(action="read", key=key)
    return str(res["value"]) if res.get("success") else None


async def delete_key(key: str) -> bool:
    res = await _execute(action="delete", key=key, confirm_delete=True)
    return bool(res.get("success"))
