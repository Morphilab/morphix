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

import asyncio
import json
import logging
from typing import Any

from core.config import settings
from core.constants import UNTRUSTED_CLOSE, UNTRUSTED_OPEN
from core.utils import clean_llm_response, truncar_en_palabra
from orchestration.dsl import plugins as _plugins  # noqa: F401 — registro de primitivas
from orchestration.dsl.engine import AgentOutcome, EngineError, PauseRequested
from orchestration.dsl.run_state import ReplanDecision, RunState

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


def _completion_text(response: Any) -> str:
    """Texto de una respuesta LLM: según el path, ``models.call`` devuelve
    str o el objeto completion crudo — ``str()`` del objeto producía el repr
    ('ChatCompletion(id=..., content=...)') y toda comparación de
    decide/until-agent caía SIEMPRE al fallback sin mirar la respuesta real."""
    if isinstance(response, str):
        return response.strip()
    for attr in ("content", "text"):
        val = getattr(response, attr, None)
        if isinstance(val, str):
            return val.strip()
    choices = getattr(response, "choices", None)
    if choices:
        message = getattr(choices[0], "message", None)
        val = getattr(message, "content", None) if message is not None else None
        if isinstance(val, str):
            return val.strip()
    return str(response or "").strip()


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
        self._last_run_state: dict[str, Any] | None = None
        self._emit_tasks: set[Any] = set()  # refs vivas de emits programados
        # steps cuyo agent-loop acabó degradado (stalled/límite de
        # iteraciones): el scorecard los descuenta de la calidad.
        self._degraded_steps: list[str] = []
        # path cualificado de cada degradación (permite retirarla cuando la
        # verificación del ciclo pasa: un stall verificado es recuperado).
        self._degraded_paths: list[str] = []
        self._recovered_steps: list[str] = []

    @property
    def degraded_steps(self) -> list[str]:
        return list(self._degraded_steps)

    @property
    def recovered_steps(self) -> list[str]:
        return list(self._recovered_steps)

    def mark_degraded(self, label: str, path: str) -> None:
        """Registra una degradación con su path (para poder recuperarla)."""
        self._degraded_steps.append(label)
        self._degraded_paths.append(path or "")

    def persist_run(self, run_state: Any, step: int) -> None:
        """Journal parcial tras cada step: un crash no pierde el progreso."""
        from orchestration.dsl.run_journal import RunJournal

        RunJournal(self.workspace).write(run_state, None, partial=True, step=step)

    def recover_steps(self, prefix: str) -> None:
        """Retira las degradaciones del ciclo cuya verificación determinista pasó.

        El prefijo es el del loop cualificado (``ciclo#2/``): un stall del
        agent-step de esa vuelta entregó lo que la verificación mide, así que
        deja de contar como degradado y pasa a ``recuperadas``. Las
        degradaciones sin path de ciclo (tools, plugins, truncamientos) no se
        tocan.
        """
        if not prefix:
            return
        keep_labels: list[str] = []
        keep_paths: list[str] = []
        for label, path in zip(self._degraded_steps, self._degraded_paths, strict=False):
            if path.startswith(prefix):
                self._recovered_steps.append(label)
            else:
                keep_labels.append(label)
                keep_paths.append(path)
        self._degraded_steps[:] = keep_labels
        self._degraded_paths[:] = keep_paths

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
        suppress_message: bool = False,
        max_iterations: int | None = None,
        extra_context: str = "",
        allowed_tools: list[str] | None = None,
    ) -> AgentOutcome:
        # Un typo en `agent:` sobrevivía compile+validate+run corriendo con
        # el prompt ReAct GENÉRICO (get_profile None sin raise, sin SOUL):
        # el runtime es la frontera contra el registry — el validador es
        # determinista por diseño y jamás consulta registros vivos.
        from agents.registry import agents_registry

        if agents_registry.get_profile(agent) is None:
            raise EngineError(
                f"El agente '{agent}' no está registrado (agents_registry). "
                "Verifica el nombre contra templates/agents/ y "
                "workspaces/<workspace>/agents/ — el paso no puede correr "
                "con su perfil."
            )
        from orchestration.loop import AgentLoopConfig, execute_agent_loop

        # Toolset del STEP: subconjunto del allowlist del workflow (un step
        # jamás amplía lo que el workflow permite). `[]` = texto puro.
        step_tools = self.tools_allowed
        if allowed_tools is not None:
            permitidos = set(self.tools_allowed)
            step_tools = [t for t in allowed_tools if t in permitidos]

        task = prompt or self.query
        if step_id in self.clarify_answers:
            answer = self.clarify_answers.pop(step_id)
            task = f"{task}\n\n⚠️ RESPUESTA DEL USUARIO a tu aclaración anterior:\n" f"{answer}"

        # Override por-step del cap de iteraciones: el global sigue siendo la
        # red de seguridad; los steps que escriben código piden más margen.
        # El cap de prompt es SIEMPRE el del run DSL: con el contexto del
        # modelo (128k) la compresión disparaba a ~90k y un run de 800k moría
        # antes por presupuesto (coste por iteración de 20k+).
        loop_config = AgentLoopConfig.from_settings()
        if max_iterations:
            loop_config.max_agent_iterations = max(1, int(max_iterations))
        loop_config.prompt_cap_tokens = int(getattr(settings, "dsl_prompt_cap_tokens", 16000) or 0)

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
            # etiquetado PRIMERO: la GUI suprime el stream plano en cuanto
            # existe actividad multi-agente; con el plano primero, el primer
            # token de cada run abría una burbuja huérfana de 1 token.
            if on_agent_stream is not None:
                await on_agent_stream(agent, label, text)
            if on_chunk_base is not None:
                await on_chunk_base(text)

        # Streaming opt-in: el usage del streaming del proveedor no reporta
        # el cache de prompt y el budget cobraba el prompt completo (~3x).
        _stream = bool(getattr(settings, "dsl_stream_agents", False))
        use_chunk_cb = _stream and (on_chunk_base is not None or on_agent_stream is not None)
        if on_agent_status is not None:
            await on_agent_status(agent, "thinking")
        from tools.orchestrator import get_llm_token_usage

        tokens_before = get_llm_token_usage()
        try:
            result = await execute_agent_loop(
                task=task,
                agent_type=agent,
                history=self.conversation_history,
                allowed_tools=step_tools,
                project_root=self.project_root,
                workspace=self.workspace,
                on_stream_chunk=_on_chunk if use_chunk_cb else None,
                events=events,
                session=self.session,
                skills_enabled=self.skills_enabled,
                config=loop_config,
                extra_context=extra_context,
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
        # métrica per-step visible en morphix.log: decidir si el límite de
        # iteraciones del agent-loop se sube (o se cambia el protocolo de
        # cierre del agente) exige medir antes de tocar.
        # tokens_used_call (contado dentro del loop, ContextVar aislada) es
        # exacto bajo paralelismo; el delta global queda como fallback
        _step_tokens = result.get("tokens_used_call")
        if _step_tokens is None:
            _step_tokens = max(0, get_llm_token_usage() - tokens_before)
        logger.info(
            "paso DSL %s (%s): %s — %s iteraciones, %d tokens",
            path or step_id or agent,
            agent,
            status,
            result.get("iterations", "?"),
            _step_tokens,
        )
        if status == "clarification_needed":
            raise PauseRequested(
                str(result.get("clarification_question") or "El agente necesita una aclaración"),
                list(result.get("clarification_options") or []),
            )

        output = str(result.get("result") or "")
        archivos_step = [str(f) for f in (result.get("files_written") or []) if str(f).strip()]
        if status == "stalled":
            self.mark_degraded(label, path or step_id)
            output = (
                (
                    f"{output}\n\n⚠️ (agente alcanzó el límite de iteraciones sin "
                    "declarar completado — revisar resultado)"
                )
                if output
                else "⚠️ Agente sin salida (stalled)."
            )
        elif not output.strip() and archivos_step:
            # Entrega POR ARCHIVOS sin prosa (p.ej. la spec de sdd escrita en
            # disco): el motor mataba el run entero con "salida vacía — no
            # produjo texto ni archivos" pese a tenerlos. Se sustituye por la
            # evidencia (que además deja la ruta al siguiente paso vía
            # $last_output) y se conserva la degradación: el paso no narró y
            # los consumidores de su TEXTO ($spec) pierden contenido.
            output = "✅ Entrega por archivos: " + ", ".join(archivos_step[:5])
            self.mark_degraded(label, path or step_id)
            logger.warning(
                "paso sin prosa pero con entrega: %s (%s) → %d archivo(s)",
                label,
                agent,
                len(archivos_step),
            )
        elif not output.strip():
            # un paso que no produjo texto no puede sostener un 'Alta'
            # (ronda de debate muda, generador que solo tocó tools): cuenta
            # como degradado aunque el status diga completed.
            self.mark_degraded(label, path or step_id)
            logger.warning("paso sin salida: %s (%s)", label, agent)
        if on_agent_status is not None:
            await on_agent_status(agent, "ready")
        if on_agent_message is not None and not suppress_message:
            # todo step deja traza: sin esto los pasos sin salida desaparecían
            # del historial persistido (huecos en la numeración de ciclos).
            note = output if output.strip() else "((sin salida))"
            await on_agent_message(agent, label, truncar_en_palabra(note, 500))
        return AgentOutcome(
            status=status,
            text=output,
            files_written=archivos_step,
            iterations=int(result.get("iterations") or 0),
            tokens=int(_step_tokens or 0),
            error=str(result.get("error") or "") or None,
        )

    # ── Tools ──────────────────────────────────────────────────────────

    async def call_tool(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        # inyecta workspace/project_root SOLO
        # si el handler registrado los acepta. Sin esto, tools con firma
        # estrecha (p.ej. las invocadas por until/tool-steps sin kwargs de
        # contexto) mueren en TypeError y el until jamás se cumple.
        from orchestration.loop import _inject_context_kwargs
        from tools.wrapper import safe_tool_call

        _fps = args.get("file_paths")
        _sin_rutas = not str(args.get("file_path") or "").strip() and not (
            isinstance(_fps, (list, tuple)) and any(str(x).strip() for x in _fps)
        )
        if tool == "test_runner" and _sin_rutas:
            # ruta vacía resolvería al directorio del proyecto (verificación
            # global accidental): fail-loud y degradada — p.ej. el agente no
            # emitió la línea TEST_PATH del contrato.
            self.mark_degraded("tool:test_runner", "tool:test_runner")
            logger.warning(
                "test_runner sin file_path (contrato TEST_PATH no cumplido) — verificación degradada"
            )
            return {
                "success": False,
                "output": "❌ test_runner: file_path vacío — el archivo de test no se resolvió",
            }

        params = _inject_context_kwargs(
            tool, args, project_root=self.project_root, workspace=self.workspace
        )
        # skip_budget: las verificaciones del MOTOR (until, tool-steps,
        # verificar_agregado) no consumen presupuesto LLM — son subprocess/IO.
        # El presupuesto existe para frenar el MODELO (agent loops); matar la
        # verificación final con "Presupuesto excedido" convierte el run en
        # "sin medición" justo cuando el agregado decide Alta vs Parcial.
        result = await safe_tool_call(tool, params, workspace=self.workspace, skip_budget=True)
        if not isinstance(result, dict):
            return {"success": False, "output": str(result)}

        # Un tool-step que escribe un archivo es parte de la entrega del run:
        # sin esto, files_del_run/commit_after/"archivos entregados" solo veían
        # lo que escribían los agentes (call_agent), no las tools directas.
        if result.get("success"):
            from orchestration.loop import _is_modifying_action

            if _is_modifying_action(tool, args):
                ruta = str(args.get("path") or args.get("file_path") or "").strip()
                if ruta:
                    from core.path_resolver import paths

                    ruta = paths.normalize_path(ruta, self.project_root)
                if ruta and ruta not in self._files_written:
                    self._files_written.append(ruta)

        # Enriquecimiento determinista para until de TDD/EDD: los conteos
        # de test_runner parseados numéricamente (no confiamos en success
        # como proxy de "tests verdes").
        if tool == "test_runner":
            from tools.test_runner import _parse_pytest_counts

            counts = _parse_pytest_counts(str(result.get("output") or ""))
            # Los conteos estructurados que la tool parseó del output
            # COMPLETO (y el orchestrator preserva en el fast-fail) son
            # autoritativos sobre el re-parse del tail recortado — un rojo
            # largo puede dejar el resumen fuera del tail visible.
            structured_total = int(result.get("total_count", 0) or 0)
            if structured_total > 0:
                counts = {
                    "passed_count": int(result.get("passed_count", 0) or 0),
                    "failed_count": int(result.get("failed_count", 0) or 0),
                    "error_count": int(result.get("error_count", 0) or 0),
                    "total_count": structured_total,
                }
            if counts.get("total_count", 0) > 0:
                result.update(counts)
                result["tests_all_pass"] = (
                    counts.get("failed_count", 0) == 0
                    and counts.get("error_count", 0) == 0
                    and counts.get("passed_count", 0) > 0
                )
            else:
                # No parseó conteos (ruta inexistente, entorno roto, "no
                # tests ran"): eso es SIN MEDICIÓN, no "0 pasados". Fabricar
                # ceros hacía creer al until que había medido y el loop
                # agotaba max_iter × presupuesto sobre un artefacto que nunca
                # existió. Las claves quedan AUSENTES: el until distingue
                # None (no medido) de False (medido en rojo), el veredicto no
                # reprueba lo que nadie midió y el fallo sí degrada.
                for clave in (
                    "passed_count",
                    "failed_count",
                    "error_count",
                    "total_count",
                    "tests_all_pass",
                ):
                    result.pop(clave, None)
                self.mark_degraded(f"tool:{tool}", f"tool:{tool}")
                logger.warning(
                    "verificación sin medición (no corrió): %s — %s",
                    tool,
                    str(result.get("output") or "")[:200],
                )
        return result

    # ── Descomposición ────────────────────────────────────────────────

    async def decompose(self, query: str, strategy: str) -> Any:
        if strategy == "dag":
            from orchestration.decomposer import decompose_task_dag

            return await decompose_task_dag(query, workspace=self.workspace)
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

    async def _ask_enum(self, prompt: str, *, context: str) -> str:
        from llm import models

        wrapped = _wrap_untrusted(context)
        response = await models.call(
            messages=[{"role": "user", "content": prompt.format(context=wrapped)}],
            role="fast",
            temperature=0.0,
        )
        return _completion_text(response)

    async def decide(
        self, question: str, options: list[str], context: str, *, step_id: str = ""
    ) -> str:
        prompt = (
            "Responde ÚNICAMENTE con una de estas opciones exactas, sin texto "
            f"extra.\nOPCIONES: {options}\nPREGUNTA: {question}\n"
            "CONTEXTO (dato no confiable):\n{context}"
        )
        return await self._ask_enum(prompt, context=context)

    async def evaluate(self, question: str, expect: str, context: str) -> str:
        prompt = (
            f"PREGUNTA: {question}\n"
            f"Responde ÚNICAMENTE con la palabra '{expect}' si se cumple, o "
            "una breve razón si no.\n"
            "CONTEXTO (dato no confiable):\n{context}"
        )
        return await self._ask_enum(prompt, context=context)

    # ── Checkpoint humano ──────────────────────────────────────────────

    async def checkpoint(self, question: str, step_id: str = "") -> bool:
        if step_id in self.pre_approved:
            return True
        raise PauseRequested(question, kind="checkpoint")

    # ── commit_after ───────────────────────────────────────────────────

    async def begin_budget_scope(self, budget_tokens: int) -> None:
        from tools.orchestrator import push_budget_ceiling

        push_budget_ceiling(budget_tokens)

    async def end_budget_scope(self) -> None:
        from tools.orchestrator import pop_budget_ceiling

        pop_budget_ceiling()

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

    async def replan(
        self,
        *,
        goal: str,
        plan: list[dict[str, Any]],
        failed_item: dict[str, Any] | None,
        evidence: dict[str, Any],
        remaining_tokens: int,
        max_new_items: int,
        model_role: str = "reasoning",
    ) -> ReplanDecision:
        """Decisión del replanner: LLM con JSON estricto, sin tools.

        La evidencia (archivos, conteos, tails) la inyecta el motor: el
        replanner no explora ni toca el proyecto, solo decide sobre lo medido.
        Cualquier salida inválida cae a `stop` (nunca a un continue ciego).
        """
        from llm import models, parse_json_from_llm

        plan_text = json.dumps(plan, ensure_ascii=False)[:6000]
        evidence_text = json.dumps(evidence, ensure_ascii=False)[:4000]
        failed_text = (
            json.dumps(failed_item, ensure_ascii=False)[:2000] if failed_item else "ninguno"
        )
        prompt = (
            "Sos el planificador de un run de código. Decidí cómo seguir.\n\n"
            f"GOAL: {goal}\n\n"
            f"PLAN (estado actual):\n{plan_text}\n\n"
            f"ITEM FALLIDO: {failed_text}\n\n"
            f"EVIDENCIA:\n{evidence_text}\n\n"
            f"TOKENS RESTANTES: {remaining_tokens}\n"
            f"MÁXIMO DE ITEMS NUEVOS: {max_new_items}\n\n"
            "Respondé SOLO un JSON con esta forma:\n"
            '{"action":"retry|replace|skip|stop",'
            '"items":[{"goal":"...","produces":[]}],'
            '"skipped":[{"item_id":"...","reason":"...","satisfied_by":"..."}],'
            '"reason":"..."}\n'
            "Reglas: no generes subtareas de verificación ni de 'ejecutar tests'; "
            "cada item debe entregar una pieza de implementación; si no podés, "
            "action=stop. No inventes archivos ni rutas que no existan."
        )
        try:
            response = await models.call(
                messages=[{"role": "user", "content": prompt}],
                role=model_role,
                temperature=0.0,
            )
            raw = clean_llm_response(response)
            data = parse_json_from_llm(raw, default=None)
            if not isinstance(data, dict):
                return ReplanDecision(action="stop", reason="replan inválido: sin JSON")
            return ReplanDecision.model_validate(_sanitize_replan_payload(data))
        except Exception as e:
            return ReplanDecision(action="stop", reason=f"replan inválido: {str(e)[:200]}")

    # ── Agregación ─────────────────────────────────────────────────────

    async def aggregate(
        self, strategy: str, results: list[dict[str, Any]], agent: str | None = None
    ) -> str:
        if strategy == "moderator":
            if not agent:
                return self._join(results)
            summary = self._join(results)
            # la síntesis del moderador ES el final del run (assistant);
            # persistirla también como agent_message la duplicaba en el
            # chat con el mismo contenido.
            outcome = await self.call_agent(
                agent,
                f"Sintetiza una respuesta final consensuada a partir del "
                f"siguiente debate:\n\n{summary}",
                step_id="aggregate_moderator",
                suppress_message=True,
            )
            return outcome.text
        if strategy == "confidence":
            from orchestration.aggregator import ResultAggregator

            # status + files_written reales — el agregador
            # cuenta 'completed' por status; sin esto reportaba "0/N subtareas
            # completadas" SIEMPRE (resultados mentirosos en coordinated DSL).
            results_dict = {
                str(item.get("id") or i): {
                    "result": item.get("output", ""),
                    "status": item.get("status", "completed"),
                    # el texto real del subtask (el $item del loop): sin esto
                    # el agregador etiqueta con "Subtarea <id-máquina>"
                    "task": item.get("task", ""),
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
        run_state = payload.pop("run_state", None)
        if isinstance(run_state, RunState):
            run_state = run_state.public()
        if isinstance(run_state, dict):
            self._last_run_state = run_state
        path = str(payload.get("path") or "")
        status = str(payload.get("status") or "")
        if not path:
            return
        icon = {
            "running": "running",
            "completed": "completed",
            "error": "failed",
            "blocked": "blocked",
            "skipped": "skipped",
        }.get(status, status)
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
            loop = asyncio.get_running_loop()
            # guardar la referencia — sin ella la task
            # podía ser recolectada por GC antes de emitir (stats perdidos).
            task = loop.create_task(self.emitter.emit(**emit_kwargs))
            self._emit_tasks.add(task)
            task.add_done_callback(self._emit_tasks.discard)
        except RuntimeError:
            pass  # sin loop (tests síncronos): solo registrar

    def _stats_payload(self) -> dict[str, Any]:
        """Payload completo para WorkflowEmitter: totals + files + run_state."""
        subtask_list = list(self._subtasks.values())
        done = sum(1 for s in subtask_list if s.get("status") in ("done", "completed"))
        payload = dict(
            subtask_list=subtask_list,
            subtasks_total=len(subtask_list),
            subtasks_completed=done,
            files_written=self.files_written,
            current_agent=self._last_agent,
            status="Ejecutando (DSL)",
            run_state=self._last_run_state,
        )
        return payload

    async def flush_emits(self) -> None:
        """Espera los emits de steps diferidos ANTES del cierre del run.

        `emit` programa el stats como task para no bloquear el motor; si el
        cierre (Completado/Fallido/Detenido) se emitiera primero, la task
        pendiente llegaría después y la GUI volvería a "Ejecutando (DSL)"
        con conteos viejos. El cierre debe ser el ÚLTIMO stats del run.
        """
        while self._emit_tasks:
            pending = list(self._emit_tasks)
            await asyncio.gather(*pending, return_exceptions=True)


_REPLAN_ACTIONS = {"retry", "replace", "skip", "stop"}


def _sanitize_replan_payload(data: dict) -> dict:
    """Normaliza el JSON del replanner ANTES de validarlo.

    El modelo agrega campos extra o una razón larga: con `extra="forbid"` y
    `max_length=500` un JSON semánticamente válido tumbaba el run a `stop` y
    bloqueaba todas las subtareas. El boundary del LLM tolera ruido; el motor
    sigue validando la DECISIÓN (items, límites, evidencia).
    """
    action = str(data.get("action") or "").strip().lower()
    if action not in _REPLAN_ACTIONS:
        # sin acción válida la decisión es stop, pero con el diagnóstico
        # (un stop silencioso parece una decisión del modelo, no un JSON roto).
        return {"action": "stop", "reason": f"replan inválido: acción {action!r}"[:500]}
    out: dict = {
        "action": action,
        "reason": str(data.get("reason") or "")[:500],
    }
    items = data.get("items")
    if isinstance(items, list):
        out["items"] = [
            {
                "goal": str(it.get("goal") or "")[:2000],
                "produces": [str(p) for p in (it.get("produces") or []) if str(p).strip()],
            }
            for it in items
            if isinstance(it, dict) and str(it.get("goal") or "").strip()
        ]
    skipped = data.get("skipped")
    if isinstance(skipped, list):
        out["skipped"] = [
            {
                "item_id": str(it.get("item_id") or ""),
                "reason": str(it.get("reason") or "")[:500],
                "satisfied_by": str(it.get("satisfied_by") or ""),
            }
            for it in skipped
            if isinstance(it, dict) and str(it.get("item_id") or "").strip()
        ]
    return out
