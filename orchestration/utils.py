"""
Workflow Utils - Funciones compartidas de limpieza y scorecard
"""

import logging
import re
import time

logger = logging.getLogger(__name__)


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
    """Genera scorecard con tokens reales de ToolOrchestrator"""
    duration = round(time.monotonic() - start_time, 2)

    total_tokens = 0
    for r in results.values():
        if isinstance(r, dict) and "result" in r:
            result_data = r["result"]
            if isinstance(result_data, dict) and "tokens_used" in result_data:
                total_tokens += result_data["tokens_used"]
            else:
                total_tokens += len(str(result_data)) // 4

    return {
        "subtasks": len(results),
        "completadas": sum(1 for r in results.values() if r.get("status") == "completed"),
        "recuperadas": 0,
        "fallidas": sum(1 for r in results.values() if r.get("status") == "failed"),
        "tokens": total_tokens,
        "tiempo": f"{duration}s",
        "calidad": "Alta",
        "tipo_tarea": task_analysis.get("primary_type", "executive"),
        "complejidad": task_analysis.get("complexity", "simple"),
    }
