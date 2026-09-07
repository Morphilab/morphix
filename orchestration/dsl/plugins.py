# orchestration/dsl/plugins.py
"""Primitivas plugin built-in del motor.

Se importan por efecto (registro) desde el runtime adapter — los presets
pueden usarlas vía ``kind: plugin``. Cada plugin valida sus params
fail-loud; nunca asume determinismo del modelo (solo transforma estado).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from orchestration.dsl.engine import EngineError
from orchestration.dsl.registry import register_primitive

logger = logging.getLogger(__name__)

__all__ = ["register_text_to_list", "known_plugin_names"]


def _split_items(text: str) -> list[str]:
    """Divide texto en ítems: líneas, viñetas markdown o enumeraciones."""
    text = text.strip()
    if not text:
        return []
    lines = []
    for line in text.splitlines():
        line = re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", line).strip()
        if line:
            lines.append(line)
    return lines


@register_primitive("text_to_list")
async def _text_to_list(step: Any, state: Any, runtime: Any, qpath: str) -> None:
    """Convierte una variable texto (multilínea/viñetas) en lista.

    params: {var: "nombre_var_texto", out: "nombre_var_lista"}
    """
    var = step.params.get("var")
    out_name = step.params.get("out")
    if not var or not out_name:
        raise EngineError(f"{qpath}: text_to_list requiere params var y out")
    raw = state.vars.get(var)
    if raw is None:
        raise EngineError(f"{qpath}: text_to_list: variable '{var}' sin valor")
    state.vars[out_name] = _split_items(str(raw))
    logger.debug("text_to_list %s: %s → %d items", qpath, var, len(state.vars[out_name]))


def register_text_to_list() -> None:
    """No-op (el registro ocurre por import). Existe para re-import explícito."""


def known_plugin_names() -> list[str]:
    from orchestration.dsl.registry import known_primitives

    return known_primitives()
