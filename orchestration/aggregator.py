"""
ResultAggregator - Síntesis FINAL optimizada (con protección contra vacíos)
"""

import logging
from typing import Any

from core.utils import clean_llm_response
from llm import models

logger = logging.getLogger(__name__)


class ResultAggregator:
    """Handles intelligent result aggregation and synthesis"""

    @staticmethod
    async def aggregate_results(
        query: str,
        results: dict,
        G: Any,
        task_analysis: dict,
        files_written: list[str] | None = None,
        project_root: str | None = None,
        workspace: str = "main",
    ) -> str:
        if not results:
            return "⚠️ No se generaron resultados."

        # User correction detection (high-specificity words,
        # avoiding common terms like "no" that cause false positives)
        if any(
            kw in query.lower()
            for kw in ["corrige", "equivocado", "error en", "arregla", "fix", "mal hecho"]
        ):
            from core.memory.manager import memory as memory_manager

            await memory_manager.save_user_correction(query, "corrección guardada")

        # Construir resultados de forma clara (1 o N subtareas)
        results_text = ""
        for node, data in sorted(results.items()):
            task_desc = G.nodes[node].get("task", f"Subtarea {node}")
            content = str(data.get("result", data)).strip()
            if content:
                label = (
                    f"--- SUBTAREA {node + 1}: {task_desc} ---"
                    if len(results) > 1
                    else f"--- Resultado: {task_desc} ---"
                )
                results_text += f"\n\n{label}\n{content}\n"

        if not results_text.strip():
            return "⚠️ No se pudo procesar la información de las subtareas."

        files_block = ""
        if files_written:
            files_block = (
                "\nArchivos realmente creados/modificados en el proyecto:\n"
                + "\n".join(f"- {f}" for f in files_written)
                + "\n\n⚠️ REGLAS ESTRICTAS para mencionar archivos:\n"
                "- SOLO menciona archivos que aparezcan en esta lista.\n"
                "- NO inventes nombres de archivo ni estructuras de directorios.\n"
                "- NO digas que el proyecto está 'incompleto' o que 'falta' algún archivo.\n"
                "- NO sugieras crear archivos adicionales.\n"
                "- Describe SOLO lo que existe, sin evaluar ni juzgar.\n"
            )

        # Read actual file contents from disk so the aggregator has the REAL code
        actual_files_text = ""
        if files_written and project_root and workspace:
            try:
                from core.path_resolver import paths as _paths

                for fname in files_written[:10]:
                    resolved = _paths.memory_dir(workspace) / project_root / fname
                    if resolved.exists():
                        content = resolved.read_text(encoding="utf-8")
                        if len(content) > 6000:
                            content = content[:6000] + "\n... [truncado]"
                        actual_files_text += (
                            f"\n--- Contenido REAL de '{fname}' en disco ---\n{content}\n"
                        )
            except Exception:
                pass

        prompt = f"""Combina los resultados de las subtareas en **UNA sola respuesta final** coherente, clara y útil.

Consulta original del usuario:
{query}

Resultados de las subtareas:
{results_text}
{actual_files_text}
{files_block}
Instrucciones OBLIGATORIAS para la respuesta final:
- Usa TODA la información relevante de las subtareas.
- Elimina completamente cualquier repetición.
- Estructura la respuesta de forma natural y fácil de leer (usa párrafos cortos y viñetas cuando ayude).
- Sé directo, profesional y conciso.
- No uses frases introductorias como "Según los resultados", "En resumen", "Aquí tienes la síntesis", etc.
- No agregues encabezados como "Síntesis" o "Respuesta final".
- Si hay información contradictoria o incompleta, menciónalo de forma honesta y útil.
- Termina con un cierre práctico o recomendación cuando corresponda."""

        try:
            if project_root:
                from orchestration.loop import execute_agent_loop

                loop_result = await execute_agent_loop(
                    task=prompt,
                    agent_type="developer",
                    allowed_tools=["file_manager"],
                    project_root=project_root,
                    workspace=workspace,
                )
                final = clean_llm_response(
                    loop_result.get("result", str(loop_result))
                    if isinstance(loop_result, dict)
                    else str(loop_result)
                )
            else:
                response = await models.call(
                    messages=[{"role": "user", "content": prompt}],
                    role="fast",
                    temperature=0.3,
                )
                final = clean_llm_response(response)

            # Strong protection against empty or useless responses
            bad_phrases = [
                "no se incluye información",
                "no hay información",
                "no pude",
                "no tengo suficiente",
                "no se proporcionó",
            ]
            if not final.strip() or any(phrase in final.lower() for phrase in bad_phrases):
                logger.warning("Síntesis LLM vacía o inútil → usando fallback estructurado")
                output = f"**Consulta:** {query}\n\n"
                if files_written:
                    output += "**Archivos creados:**\n"
                    output += "\n".join(f"- {f}" for f in files_written) + "\n\n"
                for node, data in results.items():
                    task_desc = G.nodes[node].get("task", f"Subtarea {node}")
                    output += f"### {task_desc}\n{str(data.get('result', data)).strip()}\n\n"
                return output

            return final

        except Exception as e:
            logger.error(f"Error en síntesis: {e}")
            output = f"**Consulta:** {query}\n\n"
            if files_written:
                output += "**Archivos creados:**\n"
                output += "\n".join(f"- {f}" for f in files_written) + "\n\n"
            for node, data in results.items():
                task_desc = G.nodes[node].get("task", f"Subtarea {node}")
                output += f"### {task_desc}\n{str(data.get('result', data)).strip()}\n\n"
            return output


# Instancia global
result_aggregator = ResultAggregator()
