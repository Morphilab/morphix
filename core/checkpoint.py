# core/checkpoint.py — barrera durable pre-dispatch
"""CheckpointBarrier ejecuta un flush durable ANTES de cada paso LLM.
Un fallo del flush ⇒ CheckpointFailed con la causa encadenada: el caller
debe abortar fail-closed (nunca despachar trabajo sobre estado no salvado).

Los snapshots automáticos de pipeline usan CHECKPOINT_QUESTION_PREFIX como
question — el resume lo EXCLUYE para que un checkpoint interno nunca se
presente como una pausa que espera respuesta humana."""

from collections.abc import Awaitable, Callable

CHECKPOINT_QUESTION_PREFIX = "[auto-checkpoint]"


class CheckpointFailed(RuntimeError):
    """El flush durable falló antes del dispatch — abortar fail-closed."""


class CheckpointBarrier:
    def __init__(self, flush: Callable[[], Awaitable[None]]):
        self._flush = flush

    async def before(self, step_name: str) -> None:
        try:
            await self._flush()
        except Exception as e:
            raise CheckpointFailed(f"checkpoint previo a '{step_name}' falló: {e}") from e


def is_checkpoint_question(question: str | None) -> bool:
    """True si la 'question' de una PausedSession es snapshot interno."""
    return bool(question) and str(question).startswith(CHECKPOINT_QUESTION_PREFIX)
