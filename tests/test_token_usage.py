"""Test del helper público de lectura de tokens LLM del budget ContextVar."""

from unittest.mock import MagicMock, patch

import pytest

from tools.orchestrator import get_llm_token_usage


@pytest.mark.asyncio
async def test_returns_zero_without_budget_state():
    assert get_llm_token_usage() == 0


@pytest.mark.asyncio
async def test_reads_state_total():
    from tools.orchestrator import ToolOrchestrator

    ToolOrchestrator.reset_token_budget()
    try:
        from tools.orchestrator import _token_budget_ctx

        state = _token_budget_ctx.get()
        assert state is not None
        state.total = 1234
        assert get_llm_token_usage() == 1234
    finally:
        ToolOrchestrator.reset_token_budget()


@pytest.mark.asyncio
async def test_survives_invalid_contextvar():
    with patch(
        "tools.orchestrator._token_budget_ctx",
        MagicMock(get=MagicMock(return_value=None)),
    ):
        assert get_llm_token_usage() == 0
