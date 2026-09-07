# orchestration/prompt_budget.py
"""Presupuesto token-aware del system prompt.

Antes el system prompt era un f-string monolítico sin techo: un perfil o
contexto gigante salía COMPLETO y comprimía el resto del historial. Ahora se
ensambla por CAPAS con prioridad: lo fijo (reglas) sobrevive; lo variable
(contexto/perfil) se recorta por líneas completas hasta caber.
"""

import logging

from core.token_counter import get_encoding

logger = logging.getLogger(__name__)


def count_tokens(text: str) -> int:
    enc = get_encoding()
    if enc is None:
        return max(1, len(text) // 4)  # fallback char-based
    return len(enc.encode(text))


def clip_to_budget(text: str, max_tokens: int) -> str:
    """Recorta por líneas completas hasta caber en max_tokens."""
    if count_tokens(text) <= max_tokens:
        return text
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    total = 0
    for line in lines:
        cost = count_tokens(line)
        if total + cost > max_tokens:
            break
        out.append(line)
        total += cost
    return "".join(out).rstrip() + "\n[…truncado por presupuesto]"


def enforce_budget(layers: list[tuple[str, str, int]], hard_cap: int) -> str:
    """Ensambla capas (name, text, token_cap) respetando hard_cap global.

    Orden = prioridad. Las capas que ya no caben se descartan con warning
    (degradación ordenada).
    """
    parts: list[str] = []
    used = 0
    for name, text, cap in layers:
        remaining = hard_cap - used
        allowance = min(cap, remaining)
        if not text.strip():
            # Capa SIN contenido (p.ej. bot recién creado sin memoria/skills):
            # estado normal, NO es un recorte por presupuesto. El warning
            # anterior ("presupuesto agotado (357/32000)") era engañoso.
            logger.debug("Capa '%s' omitida: sin contenido", name)
            continue
        if allowance <= 0:
            logger.warning(
                "Capa '%s' descartada: presupuesto agotado (%d/%d)", name, used, hard_cap
            )
            continue
        clipped = clip_to_budget(text, allowance)
        used += count_tokens(clipped)
        parts.append(clipped)
    return "\n\n".join(parts)
