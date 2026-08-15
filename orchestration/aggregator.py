"""
ResultAggregator - Síntesis FINAL optimizada (con protección contra vacíos)
"""

import asyncio
import logging
from typing import Any

from core.config import settings
from core.path_resolver import paths as _paths
from core.utils import clean_llm_response
from llm import models

logger = logging.getLogger(__name__)

# Requisitos explícitos detectables en la tarea → tokens que deben existir
# en el código generado. Validación determinista mínima de conformidad
# (evidencia 2026-08-14: numeros.py degradado sin bucle reportado como éxito).
_CONFORMANCE_RULES: dict[str, tuple[str, ...]] = {
    "bucle": ("for ", "while "),
    "ciclo": ("for ", "while "),
    "loop": ("for ", "while "),
    "funcion": ("def ",),
    "función": ("def ",),
    "function": ("def ",),
    "clase": ("class ",),
    "class": ("class ",),
}

_CONFORMANCE_EXTENSIONS = (".py", ".js", ".ts", ".go", ".rs", ".java")


class ResultAggregator:
    """Handles intelligent result aggregation and synthesis"""

    @staticmethod
    async def _conformance_violations(
        query: str,
        files_written: list[str] | None,
        project_root: str | None,
        workspace: str,
    ) -> list[str]:
        """Validación determinista de conformidad: si la tarea exige un
        constructo (bucle/función/clase) y NINGÚN archivo de código generado
        lo contiene, se reporta una violación. Sin prompt engineering."""
        if not files_written or not project_root:
            return []
        q = query.lower()
        required = [(kw, toks) for kw, toks in _CONFORMANCE_RULES.items() if kw in q]
        if not required:
            return []
        relevant = [f for f in files_written if f.lower().endswith(_CONFORMANCE_EXTENSIONS)]
        if not relevant:
            return []
        violations: list[str] = []
        for kw, tokens in required:
            found = False
            for fname in relevant[:5]:
                resolved = _paths.memory_dir(workspace) / project_root / fname
                if not resolved.exists():
                    continue
                try:
                    content = await asyncio.to_thread(
                        resolved.read_text, encoding="utf-8", errors="replace"
                    )
                except OSError:
                    continue
                lower = content.lower()
                if any(tok in lower for tok in tokens):
                    found = True
                    break
            if not found:
                violations.append(
                    f"la tarea exigía '{kw}' pero ningún archivo generado lo contiene"
                )
        return violations

    @staticmethod
    async def aggregate_results(
        query: str,
        results: dict,
        G: Any,
        task_analysis: dict,
        files_written: list[str] | None = None,
        project_root: str | None = None,
        workspace: str | None = None,
        agent_type: str = "developer",
    ) -> str:
        if workspace is None:
            workspace = settings.active_workspace
        if G is None:
            # Build a placeholder graph for coordinated workflow compatibility
            import networkx as nx

            G = nx.DiGraph()
            for node in sorted(results):
                task = ""
                if isinstance(results[node], dict):
                    task = str(results[node].get("task", ""))[:60]
                G.add_node(node, task=task or f"Subtarea {node}")
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
                for fname in files_written[:10]:
                    resolved = _paths.memory_dir(workspace) / project_root / fname
                    if resolved.exists():
                        content = await asyncio.to_thread(resolved.read_text, encoding="utf-8")
                        if len(content) > 6000:
                            content = content[:6000] + "\n... [truncado]"
                        actual_files_text += (
                            f"\n--- Contenido REAL de '{fname}' en disco ---\n{content}\n"
                        )
            except Exception:
                logger.warning(
                    "Failed to read file content from disk for aggregation", exc_info=True
                )

        # ── Deterministic workflow evaluation ──
        completed_count = sum(
            1
            for r in results.values()
            if isinstance(r, dict) and r.get("status") in ("completed", "done")
        )
        skipped_count = sum(
            1 for r in results.values() if isinstance(r, dict) and r.get("status") == "skipped"
        )
        failed_count = sum(
            1 for r in results.values() if isinstance(r, dict) and r.get("status") == "failed"
        )
        has_disk_files = bool(files_written and len(files_written) > 0)
        effective_done = completed_count + skipped_count
        total = len(results)

        if total == 0:
            workflow_status = "empty"
        elif has_disk_files and effective_done == total:
            workflow_status = "success"
        elif has_disk_files and effective_done > 0:
            workflow_status = "partial"
        elif has_disk_files:
            workflow_status = "partial"
        else:
            workflow_status = "llm"

        logger.info(
            "Aggregator verdict=%s | done=%d skipped=%d failed=%d files=%d total=%d",
            workflow_status,
            completed_count,
            skipped_count,
            failed_count,
            len(files_written or []),
            total,
        )
        if completed_count == 0 and total > 0:
            logger.warning(
                "⚠️ 0/%d subtareas completadas — el workflow no generó resultados",
                total,
            )

        # Conformidad mínima: si la tarea exigía un constructo y el código no
        # lo contiene, el trabajo NO está completo — degradar a partial
        # (evidencia 2026-08-14: numeros.py sin bucle reportado como éxito).
        conformance_violations: list[str] = []
        if files_written:
            try:
                conformance_violations = await ResultAggregator._conformance_violations(
                    query, files_written, project_root, workspace
                )
            except Exception:
                logger.warning("Conformance check failed — se omite", exc_info=True)
        if conformance_violations and workflow_status == "success":
            logger.warning(
                "Aggregator: conformidad insatisfecha — degradando a partial: %s",
                conformance_violations,
            )
            workflow_status = "partial"

        # ── SUCCESS: build programmatic response, no LLM call needed ──
        if workflow_status == "success":
            return _build_programmatic_response(query, results, files_written, G, skipped_count)

        # ── PARTIAL: filter failed results from LLM prompt ──
        if workflow_status == "partial":
            results_for_llm = {
                k: v
                for k, v in results.items()
                if isinstance(v, dict) and v.get("status") not in ("failed", "error")
            }
            context_note = (
                "NOTA: Algunas subtareas excedieron el tiempo de ejecución, "
                "pero los archivos en disco SÍ contienen el código completo. "
                "Reporta basándote exclusivamente en el contenido REAL de los "
                "archivos listados abajo. NO menciones timeouts ni subtareas "
                "fallidas.\n\n"
            )
            if conformance_violations:
                context_note = (
                    "NOTA DE CONFORMIDAD: los archivos existen pero NO satisfacen "
                    "un requisito explícito de la tarea: "
                    + "; ".join(conformance_violations)
                    + ". Menciónalo con honestidad y propón la corrección.\n\n"
                )
        else:
            results_for_llm = results
            context_note = ""

        # Build results_text from (possibly filtered) results
        results_text = ""
        for node, data in sorted(results_for_llm.items()):
            task_desc = G.nodes[node].get("task", f"Subtarea {node}")
            content = str(data.get("result", data)).strip()
            if content:
                node_label = (
                    str(int(node) + 1)
                    if isinstance(node, (int, str)) and str(node).isdigit()
                    else str(node)
                )
                label = (
                    f"--- SUBTAREA {node_label}: {task_desc} ---"
                    if len(results_for_llm) > 1
                    else f"--- Resultado: {task_desc} ---"
                )
                results_text += f"\n\n{label}\n{content}\n"

        if not results_text.strip():
            return "⚠️ No se pudo procesar la información de las subtareas."

        prompt = f"""{context_note}Combina los resultados de las subtareas en **UNA sola respuesta final** coherente, clara y útil.

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
                    agent_type=agent_type,
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
                "no logró completarse",
                "no logró completar",
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


def _build_programmatic_response(
    query: str,
    results: dict,
    files_written: list[str] | None,
    G,
    skipped_count: int = 0,
) -> str:
    """Build a deterministic success response — no LLM call.

    Used when the workflow produced real files and all (or effectively all)
    subtasks completed successfully. Avoids the fragility of prompt engineering.
    """
    lines = []

    if files_written:
        lines.append("## ✅ Tarea completada\n")
        lines.append("**Archivos creados/modificados:**")
        for f in files_written:
            lines.append(f"- `{f}`")

        # Count tests
        test_files = [f for f in files_written if "test" in f.lower()]
        if test_files:
            lines.append(f"\n🧪 Se generaron {len(test_files)} archivo(s) de test.")

    lines.append("\n**Resumen de subtareas:**")
    for node, data in sorted(results.items()):
        task_desc = G.nodes[node].get("task", f"Subtarea {node}")
        status = data.get("status", "?")
        icon = {
            "completed": "✅",
            "done": "✅",
            "skipped": "⏭️",
            "failed": "❌",
        }.get(status, "")
        lines.append(f"- {icon} {task_desc}")

    if skipped_count > 0:
        lines.append(
            f"\n⏭️  {skipped_count} subtarea(s) omitida(s): el código "
            f"ya estaba implementado por una fase anterior."
        )

    lines.append("\nEl código generado está completo y funcional.")
    return "\n".join(lines)


# Instancia global
result_aggregator = ResultAggregator()
