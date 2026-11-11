# orchestration/prompt_budget.py
"""Presupuesto token-aware del system prompt.

Antes el system prompt era un f-string monolítico sin techo: un perfil o
contexto gigante salía COMPLETO y comprimía el resto del historial. Ahora se
ensambla por CAPAS con prioridad: lo fijo (reglas) sobrevive; lo variable
(contexto/perfil) se recorta por líneas completas hasta caber.
"""

import logging

from core.token_counter import get_encoding
from core.utils import truncar_en_palabra

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


def _cut_with_suffix(text: str, keep: int) -> str:
    """Corta a <= keep chars conservando el sufijo de truncado "…".

    `truncar_en_palabra` puede devolver un sufijo que no cabe en `keep`; el
    recorte duro posterior lo borraba y el texto quedaba cortado a media
    palabra sin señal. Aquí el sufijo siempre sobrevive.
    """
    if keep <= 0:
        return ""
    if len(text) <= keep:
        return text
    if keep == 1:
        return "…"
    clipped = truncar_en_palabra(text, keep - 1)
    if not clipped.endswith("…") or len(clipped) > keep:
        clipped = clipped[: keep - 1].rstrip() + "…"
    if len(clipped) > keep:
        clipped = clipped[: keep - 1].rstrip() + "…"
    return clipped


def fit_text(text: str, *, max_chars: int, max_tokens: int) -> str:
    """Recorta texto libre a chars Y tokens, preservando el sufijo "…"."""
    if max_chars <= 0 or max_tokens <= 0:
        return ""
    text = _cut_with_suffix(str(text or ""), max_chars)
    for _ in range(16):
        if len(text) <= max_chars and count_tokens(text) <= max_tokens:
            return text
        tokens_now = count_tokens(text)
        ratio = min(
            max_chars / max(1, len(text)),
            max_tokens / max(1, tokens_now),
        )
        keep = int(len(text) * ratio * 0.9)
        if keep >= len(text):
            keep = len(text) - max(1, len(text) // 10)
        if keep <= 0:
            return ""
        text = _cut_with_suffix(text, keep)
        if not text.strip():
            return ""
    return ""


def fit_marked_block(
    body: str,
    open_tag: str,
    close_tag: str,
    *,
    max_chars: int,
    max_tokens: int,
) -> str:
    """Ensambla ``open_tag\\n<body>\\nclose_tag`` respetando chars Y tokens.

    Un bloque con marcadores de cierre (``<RUN_STATE>``/``<BLACKBOARD>``) no
    puede confiar solo en su cap de chars: el presupuesto de la capa del
    prompt es en TOKENS y ``clip_to_budget`` recorta por líneas, así que un
    contenido token-denso (CJK, emojis) perdería la línea de cierre y la capa
    siguiente quedaría pegada como si fuera parte del bloque. Aquí se recorta
    el body hasta que el bloque COMPLETO (con cierre) cabe en ambos caps.
    Sin cuerpo posible devuelve "" (no hay bloque válido que inyectar).
    """
    header = f"{open_tag}\n"
    footer = f"\n{close_tag}"
    overhead_chars = len(header) + len(footer)
    overhead_tokens = count_tokens(header + footer)
    if max_chars <= overhead_chars + 8 or max_tokens <= overhead_tokens + 6:
        return ""
    text = fit_text(
        body,
        max_chars=max_chars - overhead_chars,
        max_tokens=max_tokens - overhead_tokens - 4,
    )
    if not text.strip():
        return ""
    candidate = header + text + footer
    if len(candidate) <= max_chars and count_tokens(candidate) <= max_tokens:
        return candidate
    # La tokenización no es perfectamente aditiva en las fronteras: último
    # ajuste proporcional conservando el cierre.
    for _ in range(8):
        tokens_now = count_tokens(candidate)
        ratio = min(
            max_chars / max(1, len(candidate)),
            max_tokens / max(1, tokens_now),
        )
        keep = int(len(text) * ratio * 0.9)
        if keep <= 0:
            return ""
        text = _cut_with_suffix(text, keep)
        candidate = header + text + footer
        if len(candidate) <= max_chars and count_tokens(candidate) <= max_tokens:
            return candidate
    return ""
