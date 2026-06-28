"""Tests for KairosFlags — feature flags, env reload, daemon loop."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.feature_flags import KairosFlags


@pytest.fixture(autouse=True)
def reset_singleton():
    KairosFlags._instance = None
    yield
    KairosFlags._instance = None


def _make_settings():
    s = MagicMock()
    s.auto_fix_level = "medium"
    s.context_compression = True
    s.undercover_mode = False
    s.daemon_mode = True
    s.self_heal_interval = 60
    s.verbose_logging = False
    s.max_subtasks = 5
    s.tools_enabled = True
    s.allow_code_execution = True
    s.tool_max_retries = 3
    s.tool_backoff_base = 2.0
    s.tool_max_tokens_per_workflow = 10000
    s.tool_enable_token_budget = True
    s.agent_self_reflection = True
    s.hooks_enabled = True
    return s


class TestKairosFlagsGet:
    def test_get_returns_initialized_flag(self):
        with patch("core.config.settings", _make_settings()):
            k = KairosFlags()
            assert k.get("AUTO_FIX_LEVEL") == "medium"

    def test_get_env_override_bool(self):
        with (
            patch("core.config.settings", _make_settings()),
            patch.dict("os.environ", {"CONTEXT_COMPRESSION": "false"}, clear=True),
        ):
            k = KairosFlags()
            assert k.get("CONTEXT_COMPRESSION") is False

    def test_get_env_override_int(self):
        s = _make_settings()
        s.max_subtasks = 5
        with (
            patch("core.config.settings", s),
            patch.dict("os.environ", {"MAX_SUBTASKS": "10"}, clear=True),
        ):
            k = KairosFlags()
            assert k.get("MAX_SUBTASKS") == 10

    def test_get_env_override_str(self):
        s = _make_settings()
        s.auto_fix_level = "medium"
        with (
            patch("core.config.settings", s),
            patch.dict("os.environ", {"AUTO_FIX_LEVEL": "aggressive"}, clear=True),
        ):
            k = KairosFlags()
            assert k.get("AUTO_FIX_LEVEL") == "aggressive"

    def test_get_does_not_reload_dirty_flag(self):
        s = _make_settings()
        s.auto_fix_level = "medium"
        with (
            patch("core.config.settings", s),
            patch.dict("os.environ", {"AUTO_FIX_LEVEL": "aggressive"}, clear=True),
        ):
            k = KairosFlags()
            k.set("AUTO_FIX_LEVEL", "manual")
            assert k.get("AUTO_FIX_LEVEL") == "manual"

    def test_get_returns_default_for_missing_key(self):
        with patch("core.config.settings", _make_settings()):
            k = KairosFlags()
            assert k.get("NONEXISTENT") is None
            assert k.get("NONEXISTENT", "fallback") == "fallback"


class TestKairosFlagsSet:
    def test_set_stores_value(self):
        with patch("core.config.settings", _make_settings()):
            k = KairosFlags()
            k.set("AUTO_FIX_LEVEL", "aggressive")
            assert k.flags["AUTO_FIX_LEVEL"] == "aggressive"
            assert "AUTO_FIX_LEVEL" in k._dirty_flags


class TestKairosFlagsDaemon:
    @pytest.mark.asyncio
    async def test_daemon_loop_writes_heartbeat(self):
        with (
            patch("core.config.settings", _make_settings()),
            patch.dict("os.environ", {"DAEMON_MODE": "true"}),
        ):
            k = KairosFlags()
            mock_memory = MagicMock()
            mock_memory.write_system = AsyncMock()
            mock_memory.self_healing_check = AsyncMock()

            with patch("core.feature_flags.memory", mock_memory):
                task = asyncio.create_task(k.daemon_loop())
                await asyncio.sleep(0.05)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            mock_memory.write_system.assert_called()
            mock_memory.self_healing_check.assert_called()

    @pytest.mark.asyncio
    async def test_daemon_loop_skips_when_disabled(self):
        s = _make_settings()
        s.daemon_mode = False
        with patch("core.config.settings", s):
            k = KairosFlags()
            mock_memory = MagicMock()
            mock_memory.write_system = AsyncMock()
            mock_memory.self_healing_check = AsyncMock()

            with patch("core.feature_flags.memory", mock_memory):
                task = asyncio.create_task(k.daemon_loop())
                await asyncio.sleep(0.05)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            mock_memory.write_system.assert_not_called()
            mock_memory.self_healing_check.assert_not_called()
