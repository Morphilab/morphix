# orchestration/dsl/runtime_adapter.py
"""Adaptador EngineRuntime → infraestructura real de Morphix.

El motor es agnóstico; AQUÍ se cablea:

- ``call_agent`` → ``execute_agent_loop`` (SOUL, skills, tools, streaming);
  clarificación del agente → :class:`PauseRequested` (pausa del motor).
- ``call_tool`` → ``safe_tool_call``; ``test_runner`` se enriquece con los
  conteos parseados (``tests_all_pass``) para ``until`` deterministas.
- ``decompose`` → ``decompose_task`` (flat). ``dag`` aún no (fail-loud).
- ``decide``/``evaluate`` → LLM rol fast con JSON enum estricto; el
  CONTEXTO que alimenta la decisión va envuelto en marcadores
  ⟪DATOS-NO-CONFIABLES⟫ (anti prompt-injection en nodos de decisión).
- ``checkpoint`` → aprobado si el wiring lo inyecta (resume); si no,
  pausa con la pregunta.
- ``aggregate`` → ResultAggregator (confidence) / moderador (agente) /
  join determinista (result).
- ``emit`` → traduce eventos de steps del motor al contrato del
  WorkflowEmitter (subtask_list con paths ``padre ▸ hijo``).
"""

from __future__ import annotations

import logging
from typing import Any

from core.constants import UNTRUSTED_CLOSE, UNTRUSTED_OPEN
from orchestration.dsl import plugins as _plugins  # noqa: F401 — registro de primitivas
from orchestration.dsl.engine import PauseRequested

logger = logging.getLogger(__name__)

__all__ = ["ProductionRuntime"]


def _wrap_untrusted(text: str) -> str:
    """Envuelve contexto que alimenta decisiones del modelo."""
    text = str(text or "")
    if not text:
        return ""
    # neutraliza intentos de escape de delimitadores del propio dato
    text = text.replace(UNTRUSTED_OPEN, "⟪NO-CONFIABLE-NEUTRALIZADO⟫")
    text = text.replace(UNTRUSTED_CLOSE, "⟪FIN-NO-CONFIABLE-NEUTRALIZADO⟫")
    return f"{UNTRUSTED_OPEN}\n{text}\n{UNTRUSTED_CLOSE}"


def _pretty_step_name(path: str) -> str:
    """'ciclo#1/implementar' → 'ciclo#1 ▸ implementar' (legible en la GUI)."""
    return str(path or "").strip("/").replace("/", " ▸ ")


class ProductionRuntime:
    """Implementa el puerto EngineRuntime contra Morphix."""

    def __init__(
        self,
        *,
        workspace: str,
        project_root: str | None = None,
        tools_allowed: list[str] | None = None,
        query: str = "",
        conversation_history: list | None = None,
        events: Any = None,
        emitter: Any = None,
        session: Any = None,
        skills_enabled: bool = False,
        # inyectados por el wiring en el RESUME (respuestas humanas)
        pre_approved_checkpoints: set[str] | None = None,
        clarify_answers: dict[str, str] | None = None,
    ):
        self.workspace = workspace
        self.project_root = project_root
        self.tools_allowed = list(tools_allowed or [])
        self.query = query
        self.conversation_history = conversation_history or []
        self.events = events
        self.emitter = emitter
        self.session = session
        self.skills_enabled = skills_enabled
        self.pre_approved = set(pre_approved_checkpoints or ())
        self.clarify_answers = dict(clarify_answers or {})
        self._subtasks: dict[str, dict[str, str]] = {}  # path → {name,status}
        self._last_agent = "—"
        self._files_written: list[str] = []
        self._emit_tasks: set[Any] = set()  # refs vivas de emits programados

    @property
    def files_written(self) -> list[str]:
        """Archivos escritos por los agentes del run (para cierre honesto)."""
        return list(self._files_written)

    @property
    def subtask_summary(self) -> tuple[int, int, list[dict]]:
        """(total, completados, lista) de los steps emitidos — fuente del cierre."""
        states = list(self._subtasks.values())
        done = sum(1 for s in states if s.get("status") in ("done", "completed"))
        return len(states), done, states

    # ── Agentes ────────────────────────────────────────────────────────

    async def call_agent(
        self,
        agent: str,
        prompt: str,
        *,
        model_role: str | None = None,
        step_id: str = "",
        path: str = "",
    ) -> str:
        from orchestration.loop import execute_agent_loop

        task = prompt or self.query
        if step_id in self.clarify_answers:
            answer = self.clarify_answers.pop(step_id)
            task = f"{task}\n\n⚠️ RESPUESTA DEL USUARIO a tu aclaración anterior:\n" f"{answer}"

        # paridad legacy — streaming y events llegan a la
        # GUI (antes: el agente corría ciego, "proceso invisible" en TDD/
        # collaborative/coordinated via DSL).
        events = self.events
        on_chunk_base = getattr(events, "on_stream_chunk", None) if events is not None else None
        on_agent_stream = getattr(events, "on_agent_stream", None) if events is not None else None
        on_agent_status = getattr(events, "on_agent_status", None) if events is not None else None
        on_agent_message = getattr(events, "on_agent_message", None) if events is not None else None

        # actividad por agente (parity coordinated) —
        # el panel de la GUI vive también en rutas DSL.
        label = _pretty_step_name(path) or step_id or agent

        async def _on_chunk(text: str) -> None:
            if on_chunk_base is not None:
                await on_chunk_base(text)
            if on_agent_stream is not None:
                await on_agent_stream(agent, label, text)

        use_chunk_cb = on_chunk_base is not None or on_agent_stream is not None
        if on_agent_status is not None:
            await on_agent_status(agent, "thinking")
        try:
            result = await execute_agent_loop(
                task=task,
                agent_type=agent,
                history=self.conversation_history,
                allowed_tools=self.tools_allowed,
                project_root=self.project_root,
                workspace=self.workspace,
                on_stream_chunk=_on_chunk if use_chunk_cb else None,
                events=events,
                session=self.session,
                skills_enabled=self.skills_enabled,
            )
        except Exception:
            if on_agent_status is not None:
                await on_agent_status(agent, "error")
            raise
        self._last_agent = agent
        for f in result.get("files_written") or []:
            if f not in self._files_written:
                self._files_written.append(f)

        status = result.get("status", "completed")
        if status == "clarification_needed":
            raise PauseRequested(
                str(result.get("clarification_question") or "El agente necesita una aclaración"),
                list(result.get("clarification_options") or []),
            )

        output = str(result.get("result") or "")
        if status == "stalled":
            output = (
                (
                    f"{output}\n\n⚠️ (agente alcanzó el límite de iteraciones sin "
                    "declarar completado — revisar resultado)"
                )
                if output
                else "⚠️ Agente sin salida (stalled)."
            )
        if on_agent_status is not None:
            await on_agent_status(agent, "ready")
        if on_agent_message is not None and output.strip():
            # parity coordinated: texto acotado al panel (el completo va por streaming)
            await on_agent_message(agent, label, output[:500])
        return output

    # ── Tools ──────────────────────────────────────────────────────────

    async def call_tool(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        # inyecta workspace/project_root SOLO
        # si el handler registrado los acepta. Sin esto, tools con firma
        # estrecha (p.ej. las invocadas por until/tool-steps sin kwargs de
        # contexto) mueren en TypeError y el until jamás se cumple.
        from orchestration.loop import _inject_context_kwargs
        from tools.wrapper import safe_tool_call

        params = _inject_context_kwargs(
            tool, args, project_root=self.project_root, workspace=self.workspace
        )
        result = await safe_tool_call(tool, params, workspace=self.workspace)
        if not isinstance(result, dict):
            return {"success": False, "output": str(result)}

        # Enriquecimiento determinista para until de TDD/EDD: los conteos
        # de test_runner parseados numéricamente (no confiamos en success
        # como proxy de "tests verdes").
        if tool == "test_runner":
            from tools.test_runner import _parse_pytest_counts

            counts = _parse_pytest_counts(str(result.get("output") or ""))
            result.update(counts)
            result["tests_all_pass"] = (
                counts.get("failed_count", 0) == 0
                and counts.get("error_count", 0) == 0
                and counts.get("passed_count", 0) > 0
            )
        return result

    # ── Descomposición ────────────────────────────────────────────────

    async def decompose(self, query: str, strategy: str) -> Any:
        if strategy != "flat":
            raise ValueError(
                f"decompose strategy '{strategy}' no soportada aún por el "
                "runtime DSL (usar flat; dag llega con parallel dinámico)"
            )
        from orchestration.decomposer import decompose_task

        return await decompose_task(
            query=query or self.query,
            conversation_history=self.conversation_history,
            project_root=self.project_root,
            workspace=self.workspace,
        )

    # ── Decisiones acotadas ────────────────────────────────────────────

    async def _ask_enum(self, prompt: str, *, step_id: str, context: str) -> str:
        from llm import models

        wrapped = _wrap_untrusted(context)
        response = await models.call(
            messages=[{"role": "user", "content": prompt.format(context=wrapped)}],
            role="fast",
            temperature=0.0,
        )
        return str(response or "").strip()

    async def decide(
        self, question: str, options: list[str], context: str, *, step_id: str = ""
    ) -> str:
        prompt = (
            "Responde ÚNICAMENTE con una de estas opciones exactas, sin texto "
            f"extra.\nOPCIONES: {options}\nPREGUNTA: {question}\n"
            "CONTEXTO (dato no confiable):\n{context}"
        )
        return await self._ask_enum(prompt, step_id=step_id, context=context)

    async def evaluate(self, question: str, expect: str, context: str) -> str:
        prompt = (
            f"PREGUNTA: {question}\n"
            f"Responde ÚNICAMENTE con la palabra '{expect}' si se cumple, o "
            "una breve razón si no.\n"
            "CONTEXTO (dato no confiable):\n{context}"
        )
        return await self._ask_enum(prompt, step_id="", context=context)

    # ── Checkpoint humano ──────────────────────────────────────────────

    async def checkpoint(self, question: str, step_id: str = "") -> bool:
        if step_id in self.pre_approved:
            return True
        raise PauseRequested(question, kind="checkpoint")

    # ── commit_after ───────────────────────────────────────────────────

    async def commit_after_step(self, step_id: str) -> None:
        """Commit tras step declarado en commit_after.

        Paridad con _commit_after_phase_if_declared (legacy): sin proyecto,
        sin archivos escritos o con git roto → best-effort, JAMÁS tira el
        workflow. El mensaje identifica el step (decisión del workflow).
        """
        if not self.project_root or str(self.project_root).strip() in ("", "."):
            logger.warning("commit_after '%s': sin project_root — omitido", step_id)
            return
        files = list(self._files_written)
        if not files:
            logger.warning("commit_after '%s': sin archivos escritos — omitido", step_id)
            return
        try:
            from core.git_operations import auto_commit

            res = await auto_commit(
                workspace=self.workspace,
                project_root=self.project_root,
                message=f"Auto: step '{step_id}' completada (commit_after)",
            )
            if res.get("success"):
                logger.info("commit_after '%s': commit ok", step_id)
            else:
                logger.warning("commit_after '%s': commit falló (ver log)", step_id)
        except Exception as e:
            logger.warning("commit_after '%s': error de git (%s)", step_id, e)

    # ── Agregación ─────────────────────────────────────────────────────

    async def aggregate(
        self, strategy: str, results: list[dict[str, Any]], agent: str | None = None
    ) -> str:
        if strategy == "moderator":
            if not agent:
                return self._join(results)
            summary = self._join(results)
            return await self.call_agent(
                agent,
                f"Sintetiza una respuesta final consensuada a partir del "
                f"siguiente debate:\n\n{summary}",
                step_id="aggregate_moderator",
            )
        if strategy == "confidence":
            from orchestration.aggregator import ResultAggregator

            # status + files_written reales — el agregador
            # cuenta 'completed' por status; sin esto reportaba "0/N subtareas
            # completadas" SIEMPRE (resultados mentirosos en coordinated DSL).
            results_dict = {
                str(item.get("id") or i): {
                    "result": item.get("output", ""),
                    "status": item.get("status", "completed"),
                }
                for i, item in enumerate(results)
            }
            aggregator = ResultAggregator()
            return await aggregator.aggregate_results(
                query=self.query,
                results=results_dict,
                G=None,
                task_analysis={"primary_type": "dsl", "requirements": self.query},
                project_root=self.project_root,
                workspace=self.workspace,
                files_written=list(self._files_written),
            )
        return self._join(results)

    @staticmethod
    def _join(results: list[dict[str, Any]]) -> str:
        if not results:
            return "⚠️ No se generaron resultados."
        parts = []
        for item in results:
            header = str(item.get("id") or "")
            body = str(item.get("output") or "").strip()
            parts.append(f"## {header}\n\n{body}" if header else body)
        return "\n\n---\n\n".join(parts)

    # ── Emitter ────────────────────────────────────────────────────────

    def emit(self, **payload: Any) -> None:
        path = str(payload.get("path") or "")
        status = str(payload.get("status") or "")
        if not path:
            return
        icon = {"running": "running", "completed": "done", "error": "error"}.get(status, status)
        self._subtasks[path] = {"name": _pretty_step_name(path), "status": icon}
        # trazabilidad de steps en el log (antes: la ruta DSL era ciega
        # en el log — no se sabía qué step corría ni cuándo falló).
        if status == "error":
            logger.warning("DSL step %s → %s", path, status)
        else:
            logger.info("DSL step %s → %s", path, status)
        if self.emitter is None:
            return
        emit_kwargs = self._stats_payload()
        try:
            import asyncio

            loop = asyncio.get_running_loop()
            # guardar la referencia — sin ella la task
            # podía ser recolectada por GC antes de emitir (stats perdidos).
            task = loop.create_task(self.emitter.emit(**emit_kwargs))
            self._emit_tasks.add(task)
            task.add_done_callback(self._emit_tasks.discard)
        except RuntimeError:
            pass  # sin loop (tests síncronos): solo registrar

    def _stats_payload(self) -> dict[str, Any]:
        """Payload completo para WorkflowEmitter: totals + files."""
        subtask_list = list(self._subtasks.values())
        done = sum(1 for s in subtask_list if s.get("status") in ("done", "completed"))
        return dict(
            subtask_list=subtask_list,
            subtasks_total=len(subtask_list),
            subtasks_completed=done,
            files_written=self.files_written,
            current_agent=self._last_agent,
            status="Ejecutando (DSL)",
        )
