"""
Workflow Orchestrator — dispatch DSL + bots.

Solo quedan tres caminos: el motor DSL (docs `version: 1`), el chat canónico
de bots (bots_runner y su guard canónico) y el fast-path de tool directa
(DSL-aware). Los docs sin `version: 1` se rechazan con mensaje accionable;
las pausas de orígenes legacy (tdd/pipeline/coordinated/generic) responden
que ya no son reanudables.
"""

import datetime
import hashlib
import json
import logging
import re
import time

from core.checkpoint import CHECKPOINT_QUESTION_PREFIX
from core.config import settings
from core.database import bound_schema, get_async_session
from core.models import PausedSession
from core.workspaces import begin_workflow_run, end_workflow_run, get_global_workspaces
from orchestration import pauses
from orchestration.context import (
    PAUSED_MARKER,
    Session,
    WorkflowContext,
    WorkflowEvents,
    emit_system,
)
from orchestration.dsl.run_journal import RunJournal
from orchestration.dsl.run_state import RunState, build_run_report
from orchestration.emitter import WorkflowEmitter
from orchestration.finalizer import finalize_workflow
from orchestration.loader import expand_dsl_project_root, load_workflow_document
from tools.wrapper import safe_tool_call

logger = logging.getLogger(__name__)

TOOL_CALL_PATTERN = re.compile(r"^\s*(.+):\s*([\w_]+)\s*,?\s*(.*)$", re.IGNORECASE)


def _parse_direct_tool_command(query: str) -> dict | None:
    """Devuelve {tool_name, action, params} si la consulta es un comando de herramienta.
    Validates that the parsed tool_name exists in the tools registry to avoid
    false positives on natural language (e.g., 'navega y analiza : URL')."""
    match = TOOL_CALL_PATTERN.match(query.strip())
    if not match:
        return None
    tool_name = match.group(1).lower()
    action = match.group(2).lower()
    params_str = match.group(3)

    # Validate tool exists in registry (prevents false positives on natural language)
    from tools.registry import tools_registry

    if tools_registry.get_tool(tool_name) is None:
        return None

    params = {}
    if params_str:
        for part in params_str.split(","):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                params[k.strip()] = v.strip().strip("'\"")
    return {"tool_name": tool_name, "action": action, "params": params}


def _apply_preset_budget(cw) -> int | None:
    """Aplica ``budget_tokens`` del preset al presupuesto ACTIVO del run.

    El preset acota SU coste (solo baja el techo global — un preset no
    puede ampliar la política de la plataforma). Devuelve el valor aplicado
    para el log, o None si el preset no declara techo.
    """
    budget = getattr(getattr(cw, "dsl", None), "budget_tokens", None)
    if not budget:
        return None
    from tools.orchestrator import set_token_budget_max

    set_token_budget_max(int(budget))
    return int(budget)


async def _apply_projectless_tool_policy(
    tools_allowed: list[str], project_root: str | None, ctx: WorkflowContext, events
) -> list[str]:
    """Sin proyecto real (None/'.'), las tools de FS salen del allowlist.

    El fallback project_root='.' NO significa "sin lugar": es la raíz
    completa de Morphix, con lectura/escritura sobre TODO (incluidos los
    proyectos de otros workflows). El run sin proyecto sigue siendo posible
    — conversacional: web, memoria y skills.
    """
    from tools.specs import drop_fs_scoped_tools, has_real_project

    if has_real_project(project_root):
        return tools_allowed
    filtradas = drop_fs_scoped_tools(tools_allowed) or []
    if filtradas != tools_allowed:
        await emit_system(
            events,
            "ℹ️ Sin proyecto activo: tools de archivos deshabilitadas para este "
            "run (selecciona un proyecto para usarlas).",
        )
        # traza auditable: el aviso por events solo vive en la GUI
        logger.info(
            "sin proyecto real: tools de FS fuera del allowlist (%s)",
            ", ".join(t for t in tools_allowed if t not in filtradas),
        )
        ctx.allowed_tools = filtradas
    return filtradas


def _resolve_resume_project_root(cw, ctx, paused_data: dict) -> str | None:
    """Precedencia del project_root en el resume: lo persistido en la pausa
    (la resolución ORIGINAL del run) manda; el ctx lo porta (el recovery lo
    restaura); el root del doc expandido es fallback para pausas legadas
    sin project_root persistido. El fresh usa doc→ctx; el resume invierte.
    """
    persisted = (paused_data or {}).get("project_root")
    if persisted:
        return str(persisted)
    if getattr(ctx, "project_root", None):
        return str(ctx.project_root)
    project = getattr(getattr(cw, "dsl", None), "project", None)
    return expand_dsl_project_root(getattr(project, "root", None))


def _doc_sha256(raw: dict) -> str:
    """Huella estable del doc crudo: el resume la compara contra la pausa
    para avisar que el workflow fue editado mientras estaba pausado."""
    return hashlib.sha256(
        json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def _dsl_scorecard(
    run_state: RunState | dict,
    *,
    tokens: int,
    elapsed_s: float,
    wall_s: float | None = None,
    requiere_archivos: bool = False,
    archivos_escritos: int = 0,
    degradadas: int = 0,
    recuperadas: int = 0,
) -> dict:
    """Scorecard del run DSL por SUBTAREA, con entrega y proceso separados.

    ``calidad_entrega`` mide el plan (delivered/verified vs failed/unmeasured/
    pending) y el gate del agregado; ``calidad_proceso`` mide degradaciones y
    fases no entregadas (trazabilidad, spec). ``calidad`` es Alta solo si
    ambas lo son: un sdd con la entrega verde y trazabilidad sin medir es
    Parcial con motivo, no un falso Alta ni un falso Fallido.
    """
    state = run_state if isinstance(run_state, RunState) else RunState.from_dict(run_state)
    counts = state.counts()
    process_bad = degradadas + sum(
        1 for p in state.plan if p.kind != "subtask" and p.status in ("failed", "unmeasured")
    )
    aggregate_failed = any(
        p.kind != "subtask"
        and p.status == "failed"
        and ("agregado" in p.id or (p.verification is not None and p.verification.passed is False))
        for p in state.plan
    )
    entrega = "Alta"
    if counts["total"] == 0:
        if any(p.kind != "subtask" and p.status == "failed" for p in state.plan):
            entrega = "Parcial"
    elif (
        counts["delivered"] != counts["total"]
        or counts["failed"]
        or counts["unmeasured"]
        or counts["pending"]
    ):
        entrega = "Parcial"
    if aggregate_failed or (requiere_archivos and archivos_escritos == 0):
        entrega = "Parcial"
    proceso = "Alta" if process_bad == 0 else "Parcial"
    calidad = "Alta" if entrega == "Alta" and proceso == "Alta" else "Parcial"
    motivos: list[str] = []
    if counts["pending"]:
        motivos.append(f"{counts['pending']} subtarea(s) pendiente(s)")
    if counts["failed"]:
        motivos.append(f"{counts['failed']} subtarea(s) fallida(s)")
    if counts["unmeasured"]:
        motivos.append(f"{counts['unmeasured']} subtarea(s) sin medición")
    if counts["skipped"]:
        motivos.append(f"{counts['skipped']} subtarea(s) descartada(s)")
    if aggregate_failed:
        motivos.append("verificación del agregado en rojo")
    if requiere_archivos and archivos_escritos == 0:
        motivos.append("sin archivos entregados")
    motivo = "; ".join(motivos) or None
    card = {
        "run_id": state.run_id,
        "subtasks": counts["total"],
        "completadas": counts["delivered"],
        "entregadas": counts["delivered"],
        "verificadas": counts["verified"],
        "fallidas": counts["failed"],
        "sin_medicion": counts["unmeasured"],
        "pendientes": counts["pending"],
        "saltadas": counts["skipped"],
        "replanificadas": len(state.replans),
        "recuperadas": recuperadas,
        "degradadas": degradadas,
        "unidad": "subtareas",
        "pasos": {
            "total": len(state.plan),
            "completados": sum(1 for p in state.plan if p.status == "delivered"),
        },
        "calidad_entrega": entrega,
        "calidad_proceso": proceso,
        "calidad": calidad,
        "tokens": tokens,
        "tiempo": f"{round(elapsed_s, 2)}s",
        "tipo_tarea": "dsl_workflow",
        "complejidad": "medium",
    }
    if wall_s is not None:
        card["wall_s"] = f"{round(wall_s, 2)}s"
    if motivo:
        card["motivo"] = motivo
    return card


def _append_quality_warning(
    final_output: str,
    scorecard: dict,
    degraded_labels: list[str] | None = None,
) -> str:
    """Abre el mensaje con la calidad REAL del run.

    El agregador puede decir 'success' (subtareas completadas) mientras la
    verificación del agregado quedó SIN MEDIR (timeout) o hay stalls: el
    scorecard es la fuente de verdad y el chat debe decirlo, no taparlo con
    "completo y funcional". Va al INICIO: el repositorio recorta cada mensaje
    a 8.000 chars y una advertencia al final se perdía en los joins largos.
    """
    if scorecard.get("calidad") == "Alta":
        return final_output
    partes: list[str] = []
    degradadas = int(scorecard.get("degradadas") or 0)
    if degradadas:
        partes.append(f"{degradadas} paso(s) degradado(s)")
    recuperadas = int(scorecard.get("recuperadas") or 0)
    if recuperadas:
        partes.append(f"{recuperadas} recuperado(s)")
    if scorecard.get("motivo"):
        partes.append(str(scorecard["motivo"]))
    detalle = f" ({'; '.join(partes)})" if partes else ""
    etiquetas = [str(x) for x in (degraded_labels or []) if str(x).strip()]
    if etiquetas:
        detalle += "\nPasos: " + ", ".join(etiquetas[:6])
    warning = (
        f"⚠️ **Calidad: {scorecard.get('calidad', 'Parcial')}**{detalle}. "
        "Revisa el scorecard del run y los tests del proyecto antes de dar la tarea por completa."
        "\n\n---\n\n"
    )
    return warning + final_output


class WorkflowOrchestrator:
    """Dispatch DSL/bots — ver docstring del módulo."""

    @staticmethod
    async def _run_direct_tool(
        direct_tool: dict,
        query: str,
        start_time: float,
        conversation_history: list,
        events: WorkflowEvents,
        conversation_id: int | None = None,
        emitter: "WorkflowEmitter | None" = None,
    ) -> str:
        workspaces = get_global_workspaces()
        emitter = emitter or WorkflowEmitter(None)
        params = direct_tool["params"]
        params["workspace"] = workspaces.current
        params["action"] = direct_tool["action"]

        tool_result = await safe_tool_call(
            tool_name=direct_tool["tool_name"],
            parameters=params,
            role="agent",
        )

        if tool_result.get("success"):
            raw_output = tool_result.get("output", str(tool_result))
            if isinstance(raw_output, dict) and "output" in raw_output:
                output = raw_output["output"]
            elif isinstance(raw_output, str):
                output = raw_output
            else:
                output = str(raw_output)
        else:
            output = f"❌ Error en herramienta: {tool_result.get('output', 'desconocido')}"

        # Protección de salida uniforme (anti-distillation/watermark).
        from orchestration.utils import apply_undercover

        output = await apply_undercover(output)

        await emit_system(
            events,
            f"🛠️ Herramienta ejecutada: {direct_tool['tool_name']} {direct_tool['action']}",
        )

        elapsed = round(time.monotonic() - start_time, 1)
        tool_ok = bool(tool_result.get("success"))
        await emitter.emit(
            # La unidad de entrega es la tool invocada: el subtask por defecto
            # del emitter (la query en pending) mentía tras el cierre.
            subtask_list=[
                {
                    "name": f"Tool directa: {direct_tool['tool_name']}",
                    "status": "completed" if tool_ok else "failed",
                }
            ],
            subtasks_total=1,
            subtasks_completed=1 if tool_ok else 0,
            current_agent="—",
            status="Completado (tool directa)" if tool_ok else "Fallido (tool directa)",
        )
        await finalize_workflow(
            query=query,
            final_output=output,
            conversation_history=conversation_history,
            conversation_id=conversation_id,
            scorecard={"subtasks": 0, "tokens": 0, "tiempo": f"{elapsed}s"},
            subtasks_list=[f"Tool directa: {direct_tool['tool_name']}"],
            task_analysis={"primary_type": "tool_direct", "requires_full_orchestration": False},
            events=events,
        )
        return output

    @staticmethod
    async def run_full_workflow(
        session: Session,
        *,
        persist: bool = True,
        bot_context: dict | None = None,
        active_workflow: str | None = None,
    ) -> str | None:
        """Execute the full workflow pipeline.

        Receives a Session with unified context and events. Delegates routing
        to _dispatch_route to keep cyclomatic complexity low.

        ``bot_context``: identidad de bot EXPLÍCITA para turnos
        que corren fuera de un chat canónico (p.ej. respuestas de sala en
        sesión miembro). Cuando se provee, precede a la detección por
        canonical_owner_of. Con ``dm_enabled=False`` el turno tiene SOUL,
        memoria y modelo del bot, pero NO send_to_bot.

        ``active_workflow``: plantilla por-run. Cuando se
        provee, sobreescribe ``ctx.active_workflow`` de modo que cada sesión
        Maestro despacha su propia plantilla sin mutar el global.

        Returns:
            str: final response, or None if the request was blocked.
        """
        ctx = session.context
        events = session.events
        query = ctx.query

        if active_workflow is not None:
            ctx.active_workflow = active_workflow

        # Las @menciones del usuario solo identifican contexto — jamás
        # auto-envían ni reenvían verbatim (middleware suave de bots).
        from orchestration.bots_dispatch import annotate_query_mentions

        query = await annotate_query_mentions(query, ctx)
        start_time = time.monotonic()

        # ── Token de run activo ──
        # Mientras esté tomado, switch_workspace() se niega (deny-by-default;
        # force=True escapa). Envuelve TODO el cuerpo incluido el bound_schema
        # del run. resume_workflow NO toma token: es continuación, y como el
        # contador es in-memory (0 tras restart del proceso) exigirlo rompería
        # los resumes posteriores a un restart.
        token = begin_workflow_run()
        from tools.orchestrator import set_file_write_run

        set_file_write_run(token)
        try:
            # ── Binding schema↔ejecución ──
            # El workspace efectivo se resuelve UNA vez al inicio y liga el schema
            # para TODA la ejecución: un workflow largo conserva su workspace aunque
            # otra corrutina cambie el global (set_async_schema) a mitad. Envuelve
            # TODO el cuerpo (incluidos los returns tempranos de la ruta directa)
            # para que cualquier ejecución real corra bound.
            workspace = get_global_workspaces().current
            async with bound_schema(workspace):
                # ── Checks de seguridad ──
                from core.security.undercover_mode import undercover

                if not await undercover.check_query(query):
                    await emit_system(events, "❌ Solicitud bloqueada por razones de seguridad.")
                    return None

                # ── Tool orchestrator setup (compartido con resume_workflow) ──
                WorkflowOrchestrator._configure_tool_runtime(events)

                try:
                    # ── Contrato normalizado: emitter adjunto al session ──
                    from orchestration.emitter import WorkflowEmitter

                    session.emitter = WorkflowEmitter(session)

                    # ── Ruta directa: comando de herramienta (fast path) ──
                    # El comando directo pasa por el allowlist del workflow
                    # activo ANTES de ejecutarse (antes saltaba la plantilla).
                    direct_tool = _parse_direct_tool_command(query)
                    if direct_tool:
                        from tools.specs import tool_matches_allowlist

                        workspaces = get_global_workspaces()
                        from orchestration.dsl.compiler import is_dsl_document

                        _raw_doc = load_workflow_document(workspaces.current, ctx.active_workflow)
                        if _raw_doc is None or not is_dsl_document(_raw_doc):
                            msg = (
                                "❌ Comando directo bloqueado: el workflow activo "
                                "no existe o no es DSL (formato legacy retirado)."
                            )
                            await emit_system(events, msg)
                            return msg
                        # DSL: allowlist leída del documento crudo
                        direct_allowed = list(((_raw_doc.get("tools") or {}).get("allowed")) or [])
                        if not tool_matches_allowlist(direct_tool["tool_name"], direct_allowed):
                            msg = (
                                f"❌ Comando directo bloqueado: '{direct_tool['tool_name']}' no está "
                                f"en tools.allowed del workflow '{ctx.active_workflow}'."
                            )
                            await emit_system(events, msg)
                            return msg
                        return await WorkflowOrchestrator._run_direct_tool(
                            direct_tool,
                            query,
                            start_time,
                            ctx.conversation_history,
                            events,
                            ctx.conversation_id,
                            session.emitter or WorkflowEmitter(None),
                        )

                    # ── Dispatch according to active template / workflow ──
                    return await WorkflowOrchestrator._dispatch_route(
                        session=session,
                        query=query,
                        ctx=ctx,
                        events=events,
                        start_time=start_time,
                        persist=persist,
                        bot_context=bot_context,
                    )
                finally:
                    WorkflowOrchestrator._clear_tool_runtime()
        finally:
            end_workflow_run(token)

    @staticmethod
    def _configure_tool_runtime(events: WorkflowEvents) -> None:
        """Resetea token budget y re-engancha approval (compartido run/resume).

        Multi-sesión: el callback de approval se guarda en un ContextVar
        (no en el atributo de clase), de modo que runs concurrentes no se pisan.

        El callback existe SI HAY HOST que lo registre — la GUI siempre
        provee events.on_approval_required. No se gatea por daemon_mode:
        ese gate mezclaba "heartbeat Kairos activo" con "no hay humano para
        aprobar" y dejaba una GUI fresca (default daemon_mode=true) sin UI
        de aprobación — cada acción peligrosa fallaba en silencio.
        Headless/MCP-standalone sigue fail-closed (sin callback ⇒
        approval_unavailable, opt-in ALLOW_UNATTENDED_DANGEROUS).
        """
        from tools.orchestrator import ToolOrchestrator, bind_approval_from_events

        ToolOrchestrator.reset_token_budget()
        bind_approval_from_events(events)
        if events is None or events.on_approval_required is None:
            if settings.daemon_mode:
                logger.debug(
                    "Sin host de aprobación (headless/daemon): acciones peligrosas "
                    "fail-closed via D1a"
                )

    @staticmethod
    def _clear_tool_runtime() -> None:
        """Limpia el handler de approval al terminar el workflow."""
        from tools.orchestrator import clear_approval_callback

        clear_approval_callback()

    @staticmethod
    async def _dispatch_route(
        session: Session,
        query: str,
        ctx: WorkflowContext,
        events: WorkflowEvents,
        start_time: float,
        *,
        persist: bool = True,
        bot_context: dict | None = None,
    ) -> str:
        """Evaluate the template and active workflow to choose the execution route.

        Rutas REALES:

        1. Chat canónico de un bot (``canonical_owner_of``) →
           ``bots_dispatch.dispatch_canonical_turn`` (bots_runner,
           transport='canonical'), sin plantillas de workflow.
        2. Documento DSL (campo ``version: 1``) → ``_run_dsl_workflow``
           (compiler → validator → engine).
        3. Documento inexistente o formato legacy (sin ``version``) → error
           accionable (el legacy ya no existe; hint de ``cli new``).

        El fast-path de tool directa (``_parse_direct_tool_command``) se
        resuelve ANTES de llegar aquí (ver ``run_full_workflow``).
        """
        conversation_id = ctx.conversation_id

        # ── Chat canónico de bot — ANTES de tocar plantillas: SIEMPRE
        # conversa por bots_runner con su identidad, INDEPENDIENTE del
        # workflow activo (cargar la plantilla por ctx.active_workflow mataba
        # turnos de bot cuando el nombre no existía).
        if conversation_id is not None:
            from core.bots_chat import canonical_owner_of

            owner = await canonical_owner_of(int(conversation_id))
            if owner:
                logger.info(
                    "Chat canónico de @%s — turno por bots_runner (sin workflow)",
                    owner["slug"],
                )
                from orchestration.bots_dispatch import dispatch_canonical_turn

                return await dispatch_canonical_turn(
                    owner=owner,
                    query=query,
                    ctx=ctx,
                    events=events,
                    start_time=start_time,
                    persist=persist,
                    # el bus de stats de la pane: el turno del bot debe pintar
                    # su fase/estado en la sección Actividad (antes no-op).
                    emitter=session.emitter,
                )

        # ── Resolver workspace + plantilla (validada contra schema) ──
        workspaces = get_global_workspaces()

        # ── Ruta DSL (motor nuevo): el documento tiene campo `version` ──
        # Se decide ANTES de la validación legacy (extra=forbid rechazaría
        # el campo). Fail-loud en compile/validate, no en runtime interno.
        from orchestration.dsl.compiler import is_dsl_document

        raw_doc = load_workflow_document(workspaces.current, ctx.active_workflow)
        if raw_doc is not None and is_dsl_document(raw_doc):
            return await WorkflowOrchestrator._run_dsl_workflow(
                session,
                query=query,
                ctx=ctx,
                events=events,
                start_time=start_time,
                raw=raw_doc,
                persist=persist,
            )

        # ── Retiro del legacy: solo existen docs DSL. ──
        # Los presets de producto son DSL; un doc sin `version: 1` solo puede
        # ser un YAML de usuario en formato antiguo → rechazo accionable.
        if raw_doc is None:
            msg = (
                f"❌ El workflow '{ctx.active_workflow}' no existe en el "
                f"workspace '{workspaces.current}'. Créalo con "
                "'python -m orchestration.dsl.cli new <nombre>' o elige otro."
            )
            await emit_system(events, msg)
            return msg
        msg = (
            f"❌ El workflow '{ctx.active_workflow}' usa el formato legacy "
            "(sin 'version: 1'), que fue retirado. Migra el preset al "
            "DSL (python -m orchestration.dsl.cli new) y reintenta."
        )
        await emit_system(events, msg)
        logger.warning("dispatch: doc legacy rechazado (%s)", ctx.active_workflow)
        return msg

    # ── Ruta DSL (motor nuevo) ────────────────────────────────────────

    @staticmethod
    async def _run_dsl_workflow(
        session: Session,
        *,
        query: str,
        ctx: WorkflowContext,
        events: WorkflowEvents,
        start_time: float,
        raw: dict,
        persist: bool = True,
    ) -> str:
        """Compila, valida y ejecuta un workflow DSL en el motor.

        Fail-loud de compilación/validación ANTES de ejecutar. Pausas →
        PausedSession origin='dsl' con el snapshot del motor (posición
        cualificada por camino); el resume re-entra por `_resume_dsl`.
        """
        from orchestration.dsl.compiler import FileSystemCatalog, compile_workflow
        from orchestration.dsl.runtime_adapter import ProductionRuntime
        from orchestration.dsl.validator import validate_workflow

        workspace = get_global_workspaces().current
        emitter = session.emitter or WorkflowEmitter(None)
        workflow_name = str(raw.get("name") or ctx.active_workflow or "dsl")

        try:
            cw = compile_workflow(raw, catalog=FileSystemCatalog(workspace))
        except Exception as e:
            msg = f"❌ Workflow DSL '{workflow_name}' no compila: {e}"
            await emit_system(events, msg)
            logger.error("DSL compile error en '%s': %s", workflow_name, e)
            return msg

        errores = validate_workflow(cw)
        if errores:
            msg = f"❌ Workflow DSL '{workflow_name}' con errores semánticos:\n" + "\n".join(
                f"• {e}" for e in errores
            )
            await emit_system(events, msg)
            return msg

        tools_allowed = list(cw.dsl.tools.allowed or [])
        ctx.allowed_tools = tools_allowed
        ctx.skills_enabled = cw.dsl.skills
        from orchestration.loader import expand_dsl_project_root

        project_root = expand_dsl_project_root(cw.dsl.project.root) or ctx.project_root or "."
        ctx.project_root = project_root
        # la ruta DSL no logueaba el workflow: el nombre no aparecía en el log.
        logger.info(
            "📌 Workflow DSL activo: %s (workspace: %s, project: %s)",
            workflow_name,
            workspace,
            project_root,
        )

        if cw.dsl.project.required and str(project_root).strip() in ("", "."):
            mensaje = (
                "❌ Este workflow requiere un proyecto seleccionado. "
                "Crea o importa uno (memory/<workspace>/code_projects/) y "
                "selecciónalo antes de Orquestar."
            )
            await emit_system(events, mensaje)
            logger.warning("DSL '%s': project.required sin proyecto", workflow_name)
            return mensaje

        tools_allowed = await _apply_projectless_tool_policy(
            tools_allowed, project_root, ctx, events
        )
        preset_budget = _apply_preset_budget(cw)
        if preset_budget:
            # El techo EFECTIVO puede ser menor que el declarado: el preset
            # solo BAJA (set_token_budget_max aplica min()). Loguear solo el
            # valor declarado esconde cuál número gobierna de verdad el run.
            from tools.orchestrator import get_token_budget_max

            techo_efectivo = get_token_budget_max()
            if 0 < techo_efectivo < preset_budget:
                logger.info(
                    "presupuesto del preset '%s': %d tokens NO aplica — techo "
                    "efectivo del run: %d (la política global "
                    "TOOL_MAX_TOKENS_PER_WORKFLOW es menor y manda)",
                    workflow_name,
                    preset_budget,
                    techo_efectivo,
                )
            else:
                logger.info(
                    "presupuesto del preset '%s': %d tokens (techo del run)",
                    workflow_name,
                    preset_budget,
                )

        runtime = ProductionRuntime(
            workspace=workspace,
            project_root=project_root,
            tools_allowed=tools_allowed,
            query=query,
            conversation_history=ctx.conversation_history,
            events=events,
            emitter=emitter,
            session=session,
            skills_enabled=cw.dsl.skills,
        )

        return await WorkflowOrchestrator._finish_dsl_run(
            cw=cw,
            runtime=runtime,
            query=query,
            ctx=ctx,
            events=events,
            emitter=emitter,
            start_time=start_time,
            workflow_name=workflow_name,
            resume=None,
            answer=None,
            persist=persist,
            doc_sha256=_doc_sha256(raw),
        )

    @staticmethod
    async def _resume_dsl(
        session: Session,
        *,
        paused_data: dict,
        question: str,
        answer: str,
        start_time: float,
    ) -> str | None:
        """Reanuda un workflow DSL pausado (origin='dsl' en paused_state)."""
        from orchestration.dsl.compiler import FileSystemCatalog, compile_workflow
        from orchestration.dsl.runtime_adapter import ProductionRuntime
        from orchestration.dsl.validator import validate_workflow

        ctx = session.context
        events = session.events
        emitter = session.emitter or WorkflowEmitter(None)
        workspace = ctx.workspace or get_global_workspaces().current
        workflow_name = str(paused_data.get("workflow") or "dsl")
        query = str(paused_data.get("query") or ctx.query or "")

        raw = load_workflow_document(workspace, workflow_name)
        if raw is None:
            msg = f"❌ El workflow DSL '{workflow_name}' ya no existe (¿editado/borrado?)."
            await emit_system(events, msg)
            return msg

        try:
            cw = compile_workflow(raw, catalog=FileSystemCatalog(workspace))
        except Exception as e:
            msg = f"❌ Workflow DSL '{workflow_name}' ya no compila: {e}"
            await emit_system(events, msg)
            return msg

        # paridad con el fresh: la edición durante la pausa puede dejar el
        # doc semánticamente roto — el gate corre ANTES del motor.
        errores = validate_workflow(cw)
        if errores:
            msg = (
                f"❌ Workflow DSL '{workflow_name}' quedó con errores semánticos "
                "tras una edición:\n" + "\n".join(f"• {e}" for e in errores)
            )
            await emit_system(events, msg)
            return msg

        stored_sha = str(paused_data.get("doc_sha256") or "")
        if stored_sha and stored_sha != _doc_sha256(raw):
            await emit_system(
                events,
                "⚠️ El workflow fue editado desde la pausa — steps renombrados "
                "pueden re-ejecutarse y los ítems del loop desalinearse; el "
                "snapshot retoma por camino cualificado.",
            )
            logger.warning("resume '%s': doc editado desde la pausa", workflow_name)

        snapshot = paused_data.get("snapshot") or {}
        paused_step = str(paused_data.get("paused_step") or snapshot.get("paused_step") or "")
        paused_kind = str(
            paused_data.get("paused_kind") or snapshot.get("paused_kind") or "checkpoint"
        )

        project_root = _resolve_resume_project_root(cw, ctx, paused_data)
        tools_allowed = await _apply_projectless_tool_policy(
            list(cw.dsl.tools.allowed or []), project_root, ctx, events
        )
        _apply_preset_budget(cw)
        runtime = ProductionRuntime(
            workspace=workspace,
            project_root=project_root,
            tools_allowed=tools_allowed,
            query=query,
            conversation_history=ctx.conversation_history,
            events=events,
            emitter=emitter,
            session=session,
            skills_enabled=cw.dsl.skills,
        )
        if paused_kind == "checkpoint" and answer.strip():
            runtime.pre_approved.add(paused_step)
        elif paused_kind == "clarification" and paused_step:
            runtime.clarify_answers[paused_step] = answer

        return await WorkflowOrchestrator._finish_dsl_run(
            cw=cw,
            runtime=runtime,
            query=query,
            ctx=ctx,
            events=events,
            emitter=emitter,
            start_time=start_time,
            workflow_name=workflow_name,
            resume=snapshot,
            answer=answer,
            persist=True,
            doc_sha256=_doc_sha256(raw),
        )

    @staticmethod
    async def _finish_dsl_run(
        *,
        cw,
        runtime,
        query: str,
        ctx: WorkflowContext,
        events: WorkflowEvents,
        emitter,
        start_time: float,
        workflow_name: str,
        resume: dict | None,
        answer: str | None,
        persist: bool,
        doc_sha256: str | None = None,
    ) -> str:
        """Ejecuta el motor y traduce el resultado al contrato del resto."""
        from orchestration.dsl.engine import WorkflowEngine
        from orchestration.dsl.run_state import RunState

        engine = WorkflowEngine(runtime)
        from tools.orchestrator import get_token_budget_max

        result = await engine.run(
            cw,
            inputs={"query": query},
            resume=resume,
            default_budget=get_token_budget_max(),
        )

        # El motor programa los emits de steps como tasks (no bloquean el
        # run): se drenan ANTES de cualquier cierre para que el ÚLTIMO stats
        # del run sea el terminal. Sin esto, una task pendiente llegaba
        # después del cierre y la GUI volvía a "Ejecutando (DSL)" con conteos
        # viejos (chip ● encendido y progreso incompleto tras terminar).
        # getattr: runtimes fake de tests no implementan el flush.
        flush = getattr(runtime, "flush_emits", None)
        if callable(flush):
            await flush()
        run_state = RunState.from_dict(result.run_state or {})
        run_state_public = run_state.public()

        if result.status == "paused":
            question = result.paused_question or "El workflow necesita tu confirmación."
            if ctx.conversation_id is None:
                # Primera corrida de una pane: la conversación nace en el
                # finalize, que jamás corre en una pausa — sin crearla aquí,
                # todo el trabajo del run se pierde (la pausa no tiene dónde
                # persistirse y el mensaje de fallo se pinta como respuesta).
                # Misma vía de creación que el finalizer.
                try:
                    from core.repositories.conversation_repository import ConversationRepository
                    from core.utils import truncar_en_palabra

                    ctx.conversation_id = await ConversationRepository.save(
                        title=truncar_en_palabra(query, 100),
                        user_message=query,
                        tags="maestro",
                        conversation_history=[{"role": "user", "content": query}],
                    )
                except Exception:
                    msg = (
                        "❌ El workflow DSL pidió una pausa humana pero no hay "
                        "conversación para persistirla (contexto headless)."
                    )
                    await emit_system(events, msg)
                    logger.warning("pausa sin conversación: falló su creación", exc_info=True)
                    return msg
            try:
                await pauses.save_paused_session(
                    conv_id=int(ctx.conversation_id),
                    query=query,
                    question=question,
                    options=list(result.paused_options or []),
                    paused_state={
                        "origin": "dsl",
                        "workflow": workflow_name,
                        "query": query,
                        # el recovery reconstruye el ctx desde la pausa: sin
                        # esta clave, el resume corría sin proyecto real y la
                        # política projectless dropeaba TODAS las tools de FS.
                        "project_root": ctx.project_root,
                        "doc_sha256": doc_sha256,
                        "snapshot": result.snapshot or {},
                        "paused_step": result.paused_step_id or "",
                        "paused_kind": result.paused_kind,
                    },
                )
            except Exception as e:
                await pauses.warn_pause_not_persisted(events, "dsl", e)
            return PAUSED_MARKER

        if result.status == "stopped":
            msg = f"⏹ Run detenido: {result.stop_reason or 'plan inconsistente'}"
            await emit_system(events, msg)
            await emitter.emit(
                status="Detenido",
                subtask_list=list(runtime._subtasks.values()),
                subtasks_total=runtime.subtask_summary[0],
                subtasks_completed=runtime.subtask_summary[1],
                files_written=list(getattr(runtime, "files_written", [])),
                run_state=run_state_public,
            )
            logger.warning("run DSL '%s' detenido: %s", workflow_name, result.stop_reason)
            final_msg = msg
            if persist:
                from tools.orchestrator import get_llm_token_usage

                scorecard = _dsl_scorecard(
                    run_state,
                    tokens=get_llm_token_usage(),
                    elapsed_s=time.monotonic() - start_time,
                    wall_s=time.monotonic() - start_time,
                    degradadas=len(getattr(runtime, "degraded_steps", []) or []),
                    recuperadas=len(getattr(runtime, "recovered_steps", []) or []),
                )
                scorecard["calidad"] = "Fallido"
                scorecard["calidad_entrega"] = "Fallido"
                scorecard["calidad_proceso"] = "Alta"
                scorecard["motivo"] = result.stop_reason or "plan inconsistente"
                final_msg = build_run_report(run_state, scorecard, msg)
                RunJournal(get_global_workspaces().current).write(run_state, scorecard)
                try:
                    await finalize_workflow(
                        query=query,
                        final_output=final_msg,
                        conversation_history=ctx.conversation_history,
                        conversation_id=ctx.conversation_id,
                        scorecard=scorecard,
                        subtasks_list=[s["name"] for s in runtime._subtasks.values()],
                        task_analysis={
                            "requires_full_orchestration": True,
                            "primary_type": "dsl",
                            "requirements": query,
                        },
                        events=events,
                        project_root=ctx.project_root,
                        workspace=get_global_workspaces().current,
                        files_written=list(getattr(runtime, "files_written", [])),
                        status="stopped",
                    )
                except Exception:
                    logger.warning("persistencia del run detenido error", exc_info=True)
            return final_msg

        if result.status == "failed":
            msg = f"❌ Workflow DSL '{workflow_name}' falló: {result.failure}"
            await emit_system(events, msg)
            await emitter.emit(
                status="Fallido",
                subtask_list=list(runtime._subtasks.values()),
                subtasks_total=runtime.subtask_summary[0],
                subtasks_completed=runtime.subtask_summary[1],
                files_written=list(getattr(runtime, "files_written", [])),
                run_state=run_state_public,
            )
            # un run fallido deja huella auditable: antes solo existía en la
            # burbuja del chat (un run se esfumó de log y BD por completo)
            logger.error("run DSL '%s' falló: %s", workflow_name, result.failure)
            final_msg = msg
            if persist:
                try:
                    from tools.orchestrator import get_llm_token_usage

                    subtask_list = list(runtime._subtasks.values())
                    scorecard = _dsl_scorecard(
                        run_state,
                        tokens=get_llm_token_usage(),
                        elapsed_s=time.monotonic() - start_time,
                        wall_s=time.monotonic() - start_time,
                        degradadas=len(getattr(runtime, "degraded_steps", []) or []),
                        recuperadas=len(getattr(runtime, "recovered_steps", []) or []),
                    )
                    scorecard["calidad"] = "Fallido"
                    scorecard["calidad_entrega"] = "Fallido"
                    final_msg = build_run_report(run_state, scorecard, msg)
                    RunJournal(get_global_workspaces().current).write(run_state, scorecard)
                    await finalize_workflow(
                        query=query,
                        final_output=final_msg,
                        conversation_history=ctx.conversation_history,
                        conversation_id=ctx.conversation_id,
                        scorecard=scorecard,
                        subtasks_list=[s["name"] for s in subtask_list],
                        task_analysis={
                            "requires_full_orchestration": True,
                            "primary_type": "dsl",
                            "requirements": query,
                        },
                        events=events,
                        project_root=ctx.project_root,
                        workspace=get_global_workspaces().current,
                        files_written=list(getattr(runtime, "files_written", [])),
                        status="failed",
                    )
                except Exception:
                    logger.warning("persistencia del run fallido error", exc_info=True)
            return final_msg

        final_output = str(result.final_output or "⚠️ El workflow terminó sin salida.")
        subtask_list = list(runtime._subtasks.values())
        # cierre honesto — archivos reales del runtime y
        # scorecard derivado de estados reales (antes: completadas=len(subtasks)
        # y files_written=[] mentían a BD y al agregador).
        files_written = list(getattr(runtime, "files_written", []))
        subtasks_total, subtasks_completed, _ = runtime.subtask_summary
        await emitter.emit(
            status="Completado (DSL)",
            subtask_list=subtask_list,
            subtasks_total=subtasks_total,
            subtasks_completed=subtasks_completed,
            files_written=files_written,
            run_state=run_state_public,
        )

        if persist:
            from tools.orchestrator import get_llm_token_usage

            real_tokens = get_llm_token_usage()
            scorecard = _dsl_scorecard(
                run_state,
                tokens=real_tokens,
                elapsed_s=time.monotonic() - start_time,
                requiere_archivos=bool(cw.dsl.project.required),
                archivos_escritos=len(files_written),
                wall_s=time.monotonic() - start_time,
                degradadas=len(getattr(runtime, "degraded_steps", []) or []),
                recuperadas=len(getattr(runtime, "recovered_steps", []) or []),
            )
            logger.info(
                "run DSL '%s' (run %s): %s tokens, %s degradadas, calidad %s",
                workflow_name,
                scorecard["run_id"],
                scorecard["tokens"],
                scorecard["degradadas"],
                scorecard["calidad"],
            )
            final_output = build_run_report(run_state, scorecard, final_output)
            RunJournal(get_global_workspaces().current).write(run_state, scorecard)
            await finalize_workflow(
                query=query,
                final_output=final_output,
                conversation_history=ctx.conversation_history,
                conversation_id=ctx.conversation_id,
                scorecard=scorecard,
                subtasks_list=[s["name"] for s in subtask_list],
                task_analysis={
                    "requires_full_orchestration": True,
                    "primary_type": "dsl",
                    "requirements": query,
                },
                events=events,
                project_root=ctx.project_root,
                workspace=get_global_workspaces().current,
                files_written=files_written,
            )
        return final_output

    @staticmethod
    async def get_unresolved_pause(conv_id: int) -> dict | None:
        """Pausa de clarificación sin resolver para una conversación.

        Recovery GUI: permite re-armar la pausa tras cerrar la pane o reiniciar
        la app. Mismos filtros que ``resume_workflow`` (solo filas sin resolver,
        excluye snapshots ``[auto-checkpoint]``; la fila más reciente). La
        conversación vive por-schema — el llamador debe estar bound al
        workspace correcto (la GUI lo está al cargar la conversación).
        Retorna dict desacoplado de la sesión ORM:
        ``{id, question, options, paused_state}``, o None si no hay pausa.
        """
        async with get_async_session() as db_session:
            from sqlalchemy import select

            stmt = (
                select(PausedSession)
                .where(PausedSession.conversation_id == conv_id)  # type: ignore[arg-type]
                .where(PausedSession.resolved_at == None)  # type: ignore[union-attr,arg-type]  # noqa: E711
                .where(
                    ~PausedSession.clarification_question.like(  # type: ignore[attr-defined]
                        f"{CHECKPOINT_QUESTION_PREFIX}%"
                    )
                )
                .order_by(PausedSession.created_at.desc())  # type: ignore[attr-defined]
                .limit(1)
            )
            result = await db_session.execute(stmt)
            paused = result.scalar()
            if paused is None:
                return None
            return {
                "id": paused.id,
                "question": paused.clarification_question,
                "options": paused.clarification_options,
                "paused_state": paused.paused_state,
            }

    @staticmethod
    async def resume_workflow(session: Session, answer: str) -> str | None:
        """Resume a paused workflow after the user provides a clarification answer.

        - Restaura approval + token budget (como run_full_workflow).
        - Respeta el origen del paused_state: un coordinated pausado continúa
          como coordinated, no como development.
        - La fila se consume SOLO cuando el resume corrió: el marking va
          DESPUÉS del dispatch — un resume que revienta (doc roto, BD) deja
          la pausa reanudable en vez de un snapshot huérfano.
        """
        ctx = session.context
        events = session.events
        conv_id = ctx.conversation_id
        # El recovery (_rearm_paused_session) arma un Session mínimo SIN
        # emitter: sin este attach, el resume corría con WorkflowEmitter(None)
        # (no-op de stats) aunque la pane tuviera GUI viva. Cubre ambos
        # orígenes (dsl y bot) antes del dispatch.
        if session.emitter is None:
            session.emitter = WorkflowEmitter(session)
        emitter = session.emitter
        start_time = time.monotonic()

        # ── Binding al schema ORIGINAL del workflow pausado ──
        # Las filas PausedSession viven por-schema: buscar la sesión ya exige
        # estar ligado al workspace de la pausa ANTES del SELECT. La GUI reutiliza
        # la misma Session pausada, así que ctx.workspace conserva el original;
        # fallback defensivo al global actual (nunca a settings.active_workspace,
        # que NO se sincroniza en el switch).
        workspace = ctx.workspace or get_global_workspaces().current
        async with bound_schema(workspace):
            WorkflowOrchestrator._configure_tool_runtime(events)
            try:
                async with get_async_session() as db_session:
                    from sqlalchemy import select

                    stmt = (
                        select(PausedSession)
                        .where(PausedSession.conversation_id == conv_id)  # type: ignore[arg-type]
                        .where(PausedSession.resolved_at == None)  # type: ignore[arg-type]  # noqa: E711
                        # los snapshots [auto-checkpoint] NO son pausas
                        # humanas — el resume jamás debe retomarlos.
                        .where(
                            ~PausedSession.clarification_question.like(  # type: ignore[attr-defined]
                                f"{CHECKPOINT_QUESTION_PREFIX}%"
                            )
                        )
                        .order_by(PausedSession.created_at.desc())  # type: ignore[attr-defined]
                        .limit(1)
                    )
                    result = await db_session.execute(stmt)
                    paused = result.scalar()
                    if paused is None:
                        logger.warning(f"No paused session found for conversation {conv_id}")
                        return None

                    # leer SIN consumir: la fila solo se resuelve cuando el
                    # dispatch del resume termina.
                    paused_id = paused.id
                    paused_state_raw = paused.paused_state
                    question = paused.clarification_question

                paused_data = paused_state_raw
                if isinstance(paused_data, str):
                    paused_data = json.loads(paused_data)

                # ── Motor DSL: reanudar el snapshot del engine ──
                if paused_data.get("origin") == "dsl":
                    out = await WorkflowOrchestrator._resume_dsl(
                        session,
                        paused_data=paused_data,
                        question=question,
                        answer=answer,
                        start_time=start_time,
                    )
                # Origen bot: continúa el turno del bot vía el agent loop
                # con SU identidad.
                elif paused_data.get("origin") == "bot":
                    from orchestration.bots_dispatch import resume_bot_turn

                    out = await resume_bot_turn(
                        conv_id=conv_id,
                        events=events,
                        start_time=start_time,
                        paused_data=paused_data,
                        question=question,
                        answer=answer,
                        workspace=workspace,
                        # el resume del bot también pinta su turno en Actividad
                        emitter=emitter,
                    )
                else:
                    # ── Retiro del legacy: orígenes antiguos ──
                    out = (
                        "⏸️ Esta pausa pertenece a una ejecución del orquestador "
                        "legacy y ya no es reanudable. "
                        "Vuelve a lanzar el workflow — los presets actuales son DSL."
                    )
                    await emit_system(events, out)
                    logger.warning(
                        "resume: pausa legacy no reanudable (origin=%s)",
                        paused_data.get("origin"),
                    )

                # El dispatch RETORNÓ: la pausa se consumió de verdad.
                async with get_async_session() as db_session:
                    row = await db_session.get(PausedSession, paused_id)
                    if row is not None and row.resolved_at is None:
                        row.clarification_answer = answer
                        row.resolved_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
                        db_session.add(row)
                return out
            finally:
                WorkflowOrchestrator._clear_tool_runtime()
