# orchestration/bots_clock.py — reloj de rutinas Bot Mode
"""Corre ``scheduler_tick`` periódicamente (daemon registrado) e inyecta el
executor real del prompt:

- deliver='bot-chat' → encola en pending_turns (wake lo ejecuta; un solo
  carril, atribución '[Rutina …]', machine-local).
- deliver='history'  → turno directo en conversación dedicada '⏰ <nombre>'
  vía WorkflowOrchestrator con deny-list de tools.

Intervalos: kairos flag ``bots.routines_interval_seconds`` (default 30s).
"""

import asyncio
import logging
from datetime import UTC, datetime

import sqlalchemy as sa

from core import bots_routines
from core.feature_flags import kairos
from orchestration.context import (
    WorkflowEvents,
    clarification_denied,
)

logger = logging.getLogger(__name__)


async def _conversation_for_history(name: str, bot_slug: str | None) -> int:
    """Conversación dedicada visible por la rutina (título estable).

    INSERT atómico con ON CONFLICT contra el
    índice único parcial uq_conversation_routine_title — el
    SELECT-then-INSERT previo duplicaba conversaciones '⏰ <nombre>' si dos
    ticks del scheduler corrían a la vez (lock de sesión, sí; lock de fila
    de conversación, no)."""
    from core.database import get_async_session

    title = f"⏰ {name}"[:100]
    async with get_async_session() as s:
        await s.execute(
            sa.text(
                "INSERT INTO conversation (title, created_at) VALUES (:t, :now) "
                "ON CONFLICT (title) WHERE title LIKE '⏰ %' DO NOTHING"
            ),
            {"t": title, "now": datetime.now(UTC).replace(tzinfo=None)},
        )
        conv_id = (
            await s.execute(
                sa.text("SELECT id FROM conversation WHERE title=:t ORDER BY id LIMIT 1"),
                {"t": title},
            )
        ).scalar()
        return int(conv_id or 0)


async def execute_prompt(prompt: str, deliver: str, slug: str | None, name: str) -> None:
    if deliver == "bot-chat":
        await bots_routines.enqueue_bot_chat_delivery(slug or "", prompt)
        return

    conv_id = await _conversation_for_history(name, slug)
    from core.workspaces import get_global_workspaces

    if slug:
        # La rutina corre como TURNO DEL BOT (su plantilla
        # gobierna tools/modelo). transport='routine' ⇒ sin pausas y sin
        # protocolo DM (dm_enabled=False) — la deny-list se cumple por
        # transporte.
        from orchestration.bots_runner import run_bot_turn

        final = await run_bot_turn(
            slug,
            f"[Rutina {name}] {prompt}",
            conv_id,
            transport="routine",
            dm_enabled=False,
        )
    else:
        # rutina history SIN bot = prompt temporizado al asistente
        # (execute_agent_loop) — JAMÁS un workflow: project.required del
        # preset mataba toda rutina history con "❌ requiere proyecto".
        from core.workspaces import get_global_workspaces
        from orchestration.loop import execute_agent_loop

        conv_id_final = conv_id
        _flag_token = clarification_denied.set(True)  # una rutina JAMÁS pausa
        try:
            result = await execute_agent_loop(
                task=f"[Rutina {name}] {prompt}",
                agent_type="conversacional",
                history=[],
                allowed_tools=None,
                workspace=get_global_workspaces().current,
                project_root=None,
                events=WorkflowEvents(),
            )
        finally:
            clarification_denied.reset(_flag_token)
        final = str(result.get("result") or "") if isinstance(result, dict) else str(result or "")

    from core.database import get_async_session as gas
    from core.models import Message

    # deny-list aplicada a nivel prompt-session es responsabilidad de los gates;
    # aquí persistimos el intercambio como cualquier entrega canónica:
    now = datetime.now(UTC).replace(tzinfo=None)
    async with gas() as s:
        s.add(
            Message(
                conversation_id=conv_id,
                role="user",
                content=f"[Rutina {name}] {prompt}"[:8000],
                timestamp=now,
            )
        )
        if isinstance(final, str) and final.strip():
            s.add(
                Message(
                    conversation_id=conv_id, role="assistant", content=final[:8000], timestamp=now
                )
            )


async def routines_loop(interval_s: float | None = None) -> None:
    if interval_s is None:
        interval_s = float(kairos.get("bots.routines_interval_seconds") or 30.0)
    logger.info("bots routines loop ON (%.1fs)", interval_s)
    while True:
        try:
            res = await bots_routines.scheduler_tick(run_prompt=execute_prompt)
            if res.get("ran") or res.get("failed"):
                logger.info("rutinas tick: %s", res)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("routines tick error (continúa): %s", e)
        await asyncio.sleep(interval_s)


__all__ = ["routines_loop", "execute_prompt"]
