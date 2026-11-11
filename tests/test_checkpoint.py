# tests/test_checkpoint.py — barrera durable pre-dispatch
"""CheckpointBarrier: ejecuta un flush durable ANTES de cada paso LLM;
un fallo del flush aborta fail-closed con CheckpointFailed (causa intacta).
Los snapshots automáticos usan un prefijo que el RESUME debe excluir."""

import pytest

from core.checkpoint import (
    CHECKPOINT_QUESTION_PREFIX,
    CheckpointBarrier,
    CheckpointFailed,
)


@pytest.mark.asyncio
async def test_before_step_calls_flush():
    calls = []

    async def flush():
        calls.append(1)

    b = CheckpointBarrier(flush)
    await b.before("stage_a")
    await b.before("stage_b")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_flush_failure_raises_checkpoint_failed_with_cause():
    boom = RuntimeError("DB down")

    async def flush():
        raise boom

    b = CheckpointBarrier(flush)
    with pytest.raises(CheckpointFailed) as ei:
        await b.before("etapa_2")
    assert ei.value.__cause__ is boom
    assert "etapa_2" in str(ei.value)


def test_resume_exclusion_prefix_is_stable():
    """El prefijo es contrato entre el snapshot y el filtro del resume."""
    assert CHECKPOINT_QUESTION_PREFIX.startswith("[")
    assert CHECKPOINT_QUESTION_PREFIX.rstrip().endswith("]")
