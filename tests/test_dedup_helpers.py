# tests/test_dedup_helpers.py
"""Helpers extraídos de duplicaciones (Grupo G de la remediación)."""

from unittest.mock import AsyncMock, patch

import pytest


def test_recent_history_context_builds_summary():
    from orchestration.decomposer import _recent_history_context

    history = [
        {"role": "user", "content": "hola " * 60},
        {"role": "assistant", "content": "respuesta"},
        {"role": "tool", "content": "ignorada"},
        {"role": "system", "content": "ignorada"},
    ]
    out = _recent_history_context(history)
    assert "[user]" in out
    assert "[assistant]" in out
    assert "ignorada" not in out
    assert len(out) < 400  # contenido truncado a 200 chars


def test_recent_history_context_empty():
    from orchestration.decomposer import _recent_history_context

    assert _recent_history_context([]) == ""


@pytest.mark.asyncio
async def test_rate_limit_hint_appends_when_low():
    from orchestration.decomposer import _append_rate_limit_hint

    with patch("core.rate_limiter.get_rate_limiter") as mock_rl:
        mock_rl.return_value.remaining = AsyncMock(return_value=3)
        prompt = await _append_rate_limit_hint("base", threshold=10, hint="MAX 2", log_msg="x")
    assert prompt == "baseMAX 2"


@pytest.mark.asyncio
async def test_rate_limit_hint_noop_when_high():
    from orchestration.decomposer import _append_rate_limit_hint

    with patch("core.rate_limiter.get_rate_limiter") as mock_rl:
        mock_rl.return_value.remaining = AsyncMock(return_value=50)
        prompt = await _append_rate_limit_hint("base", threshold=10, hint="MAX 2", log_msg="x")
    assert prompt == "base"


@pytest.mark.asyncio
async def test_rate_limit_hint_swallows_failure():
    from orchestration.decomposer import _append_rate_limit_hint

    with patch("core.rate_limiter.get_rate_limiter", side_effect=RuntimeError("boom")):
        prompt = await _append_rate_limit_hint("base", threshold=10, hint="MAX 2", log_msg="x")
    assert prompt == "base"


def test_rewrite_postgres_url_variants():
    from core.database import rewrite_postgres_url

    assert rewrite_postgres_url("postgresql://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert rewrite_postgres_url("postgres://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert rewrite_postgres_url("postgresql+asyncpg://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"


def test_bootstrap_pattern_copies_by_glob(tmp_path):

    from core.workspaces import Workspaces

    templates = tmp_path / "tpl"
    dest = tmp_path / "dst"
    templates.mkdir()
    (templates / "a.yaml").write_text("a")
    (templates / "b.yaml").write_text("b")
    (templates / "c.txt").write_text("c")

    Workspaces._bootstrap_pattern(dest, templates, "*.yaml", "cosas")

    assert (dest / "a.yaml").exists()
    assert (dest / "b.yaml").exists()
    assert not (dest / "c.txt").exists()


def test_bootstrap_pattern_skips_existing(tmp_path):
    from core.workspaces import Workspaces

    templates = tmp_path / "tpl"
    dest = tmp_path / "dst"
    templates.mkdir()
    dest.mkdir()
    (templates / "a.yaml").write_text("nuevo")
    (dest / "a.yaml").write_text("ya-existe")

    Workspaces._bootstrap_pattern(dest, templates, "*.yaml", "cosas")

    assert (dest / "a.yaml").read_text() == "ya-existe"
