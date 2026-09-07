# orchestration/dsl/registry.py
"""Registro de primitivas plugin — escape hatch de extensibilidad.

Las 9 kinds nativas cubren ~90% de metodologías. Semántica runtime
genuinamente nueva (p.ej. "esperar evento externo") se agrega como plugin:
UN archivo aislado + ``@register_primitive("nombre")``, sin tocar el motor.
El YAML la usa vía ``kind: plugin, plugin: <nombre>, params: {...}``.

Contrato del executor:
    async def executor(step, state, runtime, qpath) -> None
    — muta ``state.vars``/``state.results``; puede levantar excepciones
    (on_error del step aplica); JAMÁS asume determinismo del modelo.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from orchestration.dsl.engine import EngineRuntime
from orchestration.dsl.schema import PluginStep

logger = logging.getLogger(__name__)

__all__ = ["register_primitive", "get_primitive", "known_primitives", "PrimitiveExecutor"]

# Estado del step accesible vía protocolo ligero (evita import circular de _State).
PrimitiveExecutor = Callable[[Any, Any, EngineRuntime, str], Awaitable[None]]

_PRIMITIVES: dict[str, PrimitiveExecutor] = {}


def register_primitive(name: str) -> Callable[[PrimitiveExecutor], PrimitiveExecutor]:
    """Decorador: registra un executor bajo ``name``. Duplicado → error."""

    def deco(fn: PrimitiveExecutor) -> PrimitiveExecutor:
        if name in _PRIMITIVES:
            raise ValueError(f"Primitiva plugin duplicada: '{name}'")
        _PRIMITIVES[name] = fn
        logger.debug("Primitiva plugin registrada: %s", name)
        return fn

    return deco


def get_primitive(name: str) -> PrimitiveExecutor | None:
    return _PRIMITIVES.get(name)


def known_primitives() -> list[str]:
    return sorted(_PRIMITIVES.keys())


def _plugin_step_typecheck(step: Any) -> bool:
    return isinstance(step, PluginStep)
