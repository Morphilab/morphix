# features/maestro/services/task_analyzer.py
"""
Task Analyzer — detección de tipo de tarea con caché LRU.
"""

import logging
from typing import Any

from core.lru_cache import LRUCache
from core.utils import clean_llm_response
from llm import models, parse_json_from_llm

logger = logging.getLogger(__name__)

_task_cache = LRUCache(max_size=500, ttl=300)


class TaskAnalyzer:
    @staticmethod
    async def analyze_task(query: str, is_follow_up: bool = False) -> dict[str, Any]:
        """Precise detection with LRU cache."""
        cache_key = f"followup:{query}" if is_follow_up else query
        cached = _task_cache.get(cache_key)
        if cached is not None:
            logger.debug("TaskAnalyzer → resultado desde caché")
            return cached

        follow_context = ""
        if is_follow_up:
            follow_context = (
                "\n⚠️ CONTEXTO: Esta es una conversación DE CONTINUACIÓN sobre un proyecto existente. "
                "Los archivos ya fueron creados en turnos anteriores. "
                "El usuario quiere MODIFICAR, EXTENDER o CORREGIR código ya existente. "
                "La complejidad debe ser menor que si fuera un proyecto nuevo.\n"
            )

        prompt = f"""Responde **ÚNICAMENTE** con un JSON válido. Sin texto extra.

TAREA: "{query}"
{follow_context}
{{
    "primary_type": "simple_conversation|creativo|analista|ejecutor|planificador|investigador|mixed",
    "complexity": "simple|medium|complex",
    "is_direct_code_execution": true/false,
    "requires_full_orchestration": true/false
}}

Reglas estrictas:
- Solo marca "simple_conversation" y "requires_full_orchestration": false si es saludo, pregunta directa sobre el usuario o conversación muy casual.
- Si la tarea pide generar, listar, elegir, proponer, analizar o múltiples pasos → "requires_full_orchestration": true
"""

        try:
            response = await models.call(
                messages=[{"role": "user", "content": prompt}],
                role="fast",
                temperature=0.0,
            )

            raw = clean_llm_response(response)
            data = parse_json_from_llm(raw)
            data = data if isinstance(data, dict) else {}

            data.setdefault("primary_type", "mixed")
            data.setdefault("complexity", "medium")
            data.setdefault("is_direct_code_execution", False)
            data.setdefault("estimated_steps", 2)
            data.setdefault("requires_synthesis", True)

            # Save to cache
            _task_cache.set(cache_key, data)

            logger.info(
                f"✅ TaskAnalyzer completado → {data.get('primary_type')} | "
                f"orchestration={data.get('requires_full_orchestration')} | "
                f"code_execution={data.get('is_direct_code_execution')}"
            )
            return data

        except Exception as e:
            logger.error(f"Error en TaskAnalyzer: {e}", exc_info=True)
            return {
                "primary_type": "mixed",
                "requires_full_orchestration": True,
                "is_direct_code_execution": False,
                "estimated_steps": 3,
            }
