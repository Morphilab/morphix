"""WorkflowRunner — lógica async de ejecución/reanudación de workflows.

Extraída de MaestroTab (spec §5) para poder testearla sin Qt.
El tab se suscribe a los callbacks para actualizar la UI.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from orchestration.context import PAUSED_MARKER, Session

logger = logging.getLogger(__name__)


class WorkflowRunner:
    """Ejecuta workflows en background con callbacks de UI."""

    def __init__(self) -> None:
        self.on_system: Callable[[str], Awaitable[None]] | None = None
        self.on_assistant: Callable[[str], Awaitable[None]] | None = None
        self.on_pause: Callable[[Session, str], Awaitable[None]] | None = None
        self.streaming_check: Callable[[], bool] | None = None
        # Fin de ejecución (éxito, fallo o cancelación): resetea UI dependiente
        # del último stats (p.ej. rutas sin emit terminal como coordinated).
        self.on_finish: Callable[[], None] | None = None
        # ⏹ — persistencia de la conversación parcial (el
        # host la conecta; el runner no conoce la BD ni el historial de UI).
        self.on_cancel_persist: Callable[[Session], Awaitable[None]] | None = None

    async def run(self, session: Session) -> None:
        """Corre run_full_workflow y reporta el resultado."""
        from orchestration.workflows.orchestrator import WorkflowOrchestrator

        try:
            final = await WorkflowOrchestrator.run_full_workflow(session=session)
            ctx = session.context

            if final == PAUSED_MARKER:
                question = ctx.last_clarification or "¿Podrías clarificar?"
                if self.on_system:
                    await self.on_system(f"⏸️ Pausa: {question}")
                if self.on_pause:
                    await self.on_pause(session, question)
                return

            had_streaming = bool(self.streaming_check and self.streaming_check())
            if final and final.strip():
                if self.on_assistant:
                    await self.on_assistant(final)
            elif not had_streaming:
                if self.on_system:
                    await self.on_system("⚠️ El workflow no produjo respuesta.")
        except asyncio.CancelledError:
            # ⏹ del usuario — CancelledError es BaseException,
            # el `except Exception` de abajo no lo veía: ni mensaje ni persistencia
            # (la conversación detenida desaparecía del Historial).
            logger.info("Workflow cancelado por el usuario (⏹)")
            if self.on_system:
                await self.on_system("⏹ Ejecución detenida. Se conserva la conversación parcial.")
            if self.on_cancel_persist is not None:
                try:
                    await self.on_cancel_persist(session)
                except Exception:
                    logger.warning("Persistencia de run cancelado falló", exc_info=True)
        except Exception as e:
            logger.error(f"Error en workflow: {e}", exc_info=True)
            if self.on_system:
                await self.on_system(f"❌ Error: {e}")
        finally:
            if self.on_finish:
                self.on_finish()

    async def resume(self, session: Session, answer: str) -> None:
        """Reanuda un workflow pausado tras clarificación."""
        from orchestration.workflows.orchestrator import WorkflowOrchestrator

        try:
            final = await WorkflowOrchestrator.resume_workflow(session=session, answer=answer)
            if final == PAUSED_MARKER:
                question = session.context.last_clarification or "¿Podrías clarificar?"
                if self.on_system:
                    await self.on_system(f"⏸️ Pausa adicional: {question}")
                if self.on_pause:
                    await self.on_pause(session, question)
                return
            if final and final.strip() and self.on_assistant:
                await self.on_assistant(final)
        except asyncio.CancelledError:
            logger.info("Resume cancelado por el usuario (⏹)")
            if self.on_system:
                await self.on_system("⏹ Ejecución detenida. Se conserva la conversación parcial.")
            if self.on_cancel_persist is not None:
                try:
                    await self.on_cancel_persist(session)
                except Exception:
                    logger.warning("Persistencia de resume cancelado falló", exc_info=True)
        except Exception as e:
            logger.error(f"Error resumiendo workflow: {e}", exc_info=True)
            if self.on_system:
                await self.on_system(f"❌ Error: {e}")
        finally:
            if self.on_finish:
                self.on_finish()

    async def run_direct_agent(self, session: Session, query: str, agent: str) -> str | None:
        """Conversación directa 1:1 con un agente (function-calling nativo).

        Retorna la respuesta (o None) para que el tab la persista sin
        escanear el history.
        """
        from agents.registry import agents_registry as _reg
        from core.workspaces import begin_workflow_run, end_workflow_run, get_global_workspaces
        from orchestration.loop import execute_agent_loop
        from tools.specs import expand_allowed_tools

        token = begin_workflow_run()
        from tools.orchestrator import (
            bind_approval_from_events,
            clear_approval_callback,
            set_file_write_run,
        )

        set_file_write_run(token)
        # Paridad con el orquestador: el chat directo también engancha el
        # callback de aprobación que provee el host. Sin esto, toda acción
        # peligrosa (bash_manager, code_exec, delete, apply) se denegaba
        # fail-closed por "no hay UI de aprobación" aunque la GUI la tuviera.
        bind_approval_from_events(session.events)
        try:
            agent_profile = _reg.get_profile(agent)
            agent_tools = agent_profile.get("tools", []) if agent_profile else []
            workspace = get_global_workspaces().current

            async def _stream(text: str) -> None:
                if session.events and session.events.on_stream_chunk:
                    await session.events.on_stream_chunk(text)

            effective_tools: list[str] | None = None
            loop_result: dict | None = None
            if agent_tools:
                # Chat directo: el PERFIL del agente ES el límite de tools.
                # Antes se intersectaba con el allowlist del workflow activo —
                # el valor de un combo OCULTO en modo chat: el mismo agente
                # respondía con tools distintas según el workflow residual de
                # la sesión, sin forma de verlo. El allowlist sigue aplicando
                # en los RUNS de workflow (donde el workflow es visible y
                # elegido); la política projectless de FS se mantiene.
                effective_tools = expand_allowed_tools(agent_tools) or []

                # sin proyecto real no hay directorio de trabajo legítimo:
                # las tools de FS leerían/escribirían la raíz del repo entera
                from tools.specs import drop_fs_scoped_tools, has_real_project

                if effective_tools and not has_real_project(session.context.project_root):
                    filtradas = drop_fs_scoped_tools(effective_tools)
                    if filtradas != effective_tools:
                        if self.on_system:
                            await self.on_system(
                                "ℹ️ Sin proyecto activo: tools de archivos "
                                "deshabilitadas para este chat."
                            )
                        effective_tools = filtradas

                loop_result = await execute_agent_loop(
                    task=query,
                    agent_type=agent,
                    history=list(session.context.conversation_history),
                    allowed_tools=effective_tools,
                    workspace=workspace,
                    project_root=session.context.project_root,
                    on_stream_chunk=_stream,
                    events=session.events,
                    skills_enabled=True,
                )
                response = (
                    loop_result.get("result", str(loop_result))
                    if isinstance(loop_result, dict)
                    else str(loop_result)
                )
            else:
                from agents.service import AgentsService

                response = await AgentsService.execute_agent(
                    agent,
                    query,
                    list(session.context.conversation_history),
                    on_stream_chunk=_stream,
                )

            if response and response.strip() and self.on_assistant:
                await self.on_assistant(response)
            elif self.on_system:
                status = loop_result.get("status", "?") if isinstance(loop_result, dict) else "N/A"
                await self.on_system(f"⚠️ El agente no produjo respuesta. Estado: {status}")
            return response
        except Exception as e:
            logger.error(f"Error en agente directo: {e}", exc_info=True)
            if self.on_system:
                await self.on_system(f"❌ Error: {e}")
            return None
        finally:
            clear_approval_callback()
            end_workflow_run(token)
