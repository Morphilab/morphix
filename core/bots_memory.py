# core/bots_memory.py — memoria curada por bot, snapshot congelado
"""Dos archivos markdown curados por bot.

Layout (por workspace):
    <memory_dir(workspace)>/bots/<slug>/MEMORY.md   — notas del bot
    <memory_dir(workspace)>/bots/<slug>/USER.md     — datos del usuario vistos por el bot

El snapshot que entra al system prompt se CONGELA por turno: si un tool edita
los archivos a mitad del turno, el turno actual no cambia de prefijo (caché).
Presupuesto de caracteres para acotar el costo.
"""

import logging
from pathlib import Path

from core.path_resolver import paths

logger = logging.getLogger(__name__)

MEMORY_FILENAME = "MEMORY.md"
USER_FILENAME = "USER.md"
DEFAULT_BUDGET_CHARS = 6000


def bot_memory_dir(slug: str, workspace: str | None = None) -> Path:
    """API pública lista para cablearse: valida slug y
    workspace antes de componer rutas (anti path-traversal si alguien la
    expone a una tool sin revisar)."""
    from core.bots import validate_slug

    return paths.memory_dir(_safe_ws(workspace)) / "bots" / validate_slug(slug)


def _safe_ws(workspace: str | None) -> str:
    """Valida el nombre de workspace contra el patrón canónico del repo."""
    import re

    ws = workspace or _active_workspace()
    if not re.match(r"^[a-z][a-z0-9_]*$", ws):
        raise ValueError(f"workspace inválido para memoria de bot: {ws!r}")
    return ws


def _active_workspace() -> str:
    from core.workspaces import get_global_workspaces

    return get_global_workspaces().current


def _read_capped(path: Path, remaining_chars_budget: int) -> tuple[str, int]:
    if not path.is_file():
        return "", remaining_chars_budget
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:  # read-fail → degradar sin romper el turno
        logger.warning("lectura de memoria falló (%s): %s", path, e)
        return "", remaining_chars_budget
    take = raw[:remaining_chars_budget]
    spent = len(take)
    return take, remaining_chars_budget - spent


def snapshot(
    slug: str,
    *,
    workspace: str | None = None,
    max_chars: int = DEFAULT_BUDGET_CHARS,
) -> str:
    """Snapshot combinado MEMORY.md + USER.md dentro del presupuesto total."""
    base = bot_memory_dir(slug, workspace)
    memory_text, budget_left = _read_capped(base / MEMORY_FILENAME, max_chars)
    user_text, _ = _read_capped(base / USER_FILENAME, budget_left)
    parts: list[str] = []
    if memory_text.strip():
        parts.append(f"### {MEMORY_FILENAME}\n{memory_text}")
    if user_text.strip():
        parts.append(f"### {USER_FILENAME}\n{user_text}")
    return "\n\n".join(parts).strip()


def save_section(
    slug: str,
    *,
    which: str,
    content: str,
    workspace: str | None = None,
) -> Path:
    """Escribe/actualiza una sección curada (equivalente del memory_tool).

    ``which`` ∈ {memory, user}. Guardas anti-pérdida: escritura atómica
    vía tmp+rename; NUNCA trunca con contenido vacío accidental sin flag.
    """
    if which not in {"memory", "user"}:
        raise ValueError("which debe ser 'memory' o 'user'")
    target = bot_memory_dir(slug, workspace) / (
        MEMORY_FILENAME if which == "memory" else USER_FILENAME
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)
    logger.info("memoria de bot '%s' actualizada: %s (%d chars)", slug, which, len(content))
    return target
