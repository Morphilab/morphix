"""
Workflow Utils - Funciones compartidas de limpieza y scorecard
"""

import logging
import re
import time

logger = logging.getLogger(__name__)


async def apply_undercover(text: str) -> str:
    """Aplica anti-distillation/watermark de forma uniforme a las salidas.

    PR 6 — todas las rutas de salida (development, coordinated, collaborative,
    tdd, direct tool, simple conversation, resume) pasan por este helper.
    """
    from core.security.undercover_mode import undercover

    return await undercover.get_safe_response_async(text)


def clean_generated_code(raw_code: str) -> str:
    """Ultra-aggressive cleanup for generated code"""
    code = raw_code.strip()
    code = re.sub(r"```(?:python)?\s*", "", code)
    code = re.sub(r"```\s*$", "", code)
    code = re.sub(r"^.*?Aquí.*?(?:código|code)[:\s]*", "", code, flags=re.IGNORECASE | re.DOTALL)
    code = re.sub(r"^.*?El código es[:\s]*", "", code, flags=re.IGNORECASE | re.DOTALL)
    code = re.sub(r"^.*?Python simple[:\s]*", "", code, flags=re.IGNORECASE | re.DOTALL)
    code = re.sub(
        r'if\s+__name__\s*==\s*["\']__main__["\']\s*:[\s\S]*?$', "", code, flags=re.IGNORECASE
    )
    return code.strip()


from typing import Any


def generate_scorecard(
    results: dict,
    G: Any,
    final_content: str,
    query: str,
    task_analysis: dict,
    start_time: float,
    enc: Any = None,
) -> dict:
    """Genera scorecard con tokens reales de ToolOrchestrator.

    PR 4 — honestidad: `calidad` se computa determinísticamente
    (success/partial/failure, mismo criterio que ResultAggregator):
    - todos completados → "Alta"
    - mezcla → "Parcial"
    - todos fallidos → "Baja"
    - sin resultados → "Sin evaluar"
    """
    duration = round(time.monotonic() - start_time, 2)

    from tools.orchestrator import get_llm_token_usage

    real_tokens = get_llm_token_usage()
    if real_tokens:
        total_tokens = real_tokens
    else:
        total_tokens = 0
        for r in results.values():
            if isinstance(r, dict) and "result" in r:
                result_data = r["result"]
                if isinstance(result_data, dict) and "tokens_used" in result_data:
                    total_tokens += result_data["tokens_used"]
                else:
                    total_tokens += len(str(result_data)) // 4

    completed = sum(
        1 for r in results.values() if isinstance(r, dict) and r.get("status") == "completed"
    )
    failed = sum(1 for r in results.values() if isinstance(r, dict) and r.get("status") == "failed")
    total_results = len(results)
    if total_results == 0:
        calidad = "Sin evaluar"
    elif failed == 0 and completed == total_results:
        calidad = "Alta"
    elif completed == 0:
        calidad = "Baja"
    else:
        calidad = "Parcial"

    return {
        "subtasks": len(results),
        "completadas": completed,
        "recuperadas": 0,
        "fallidas": failed,
        "tokens": total_tokens,
        "tiempo": f"{duration}s",
        "calidad": calidad,
        "tipo_tarea": task_analysis.get("primary_type", "executive"),
        "complejidad": task_analysis.get("complexity", "simple"),
    }
