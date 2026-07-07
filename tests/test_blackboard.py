# tests/test_blackboard.py
"""Tests for SharedBlackboard — phase namespaces, snapshot/restore, DB sync."""

import json
from unittest.mock import patch

import pytest

from orchestration.workflows.blackboard import SharedBlackboard


class TestPhaseNamespaces:
    @pytest.mark.asyncio
    async def test_write_read_with_phase(self):
        bb = SharedBlackboard()
        await bb.write("schema", {"tables": ["users"]}, phase="design")
        result = await bb.read("schema", phase="design")
        assert result == {"tables": ["users"]}

    @pytest.mark.asyncio
    async def test_read_without_phase_finds_any(self):
        bb = SharedBlackboard()
        await bb.write("x", 1, phase="a")
        await bb.write("y", 2, phase="b")
        assert await bb.read("x") == 1
        assert await bb.read("y") == 2

    @pytest.mark.asyncio
    async def test_phases_do_not_collide(self):
        bb = SharedBlackboard()
        await bb.write("key", "design_value", phase="design")
        await bb.write("key", "impl_value", phase="implement")
        assert await bb.read("key", phase="design") == "design_value"
        assert await bb.read("key", phase="implement") == "impl_value"

    @pytest.mark.asyncio
    async def test_read_phase(self):
        bb = SharedBlackboard()
        await bb.write("a", 1, phase="design")
        await bb.write("b", 2, phase="design")
        await bb.write("c", 3, phase="implement")
        design = await bb.read_phase("design")
        assert design == {"a": 1, "b": 2}

    @pytest.mark.asyncio
    async def test_list_phases(self):
        bb = SharedBlackboard()
        await bb.write("x", 1, phase="a")
        await bb.write("y", 2, phase="b")
        phases = await bb.list_phases()
        assert phases == ["a", "b"]

    @pytest.mark.asyncio
    async def test_list_keys_scoped(self):
        bb = SharedBlackboard()
        await bb.write("a", 1, phase="p1")
        await bb.write("b", 2, phase="p1")
        await bb.write("c", 3, phase="p2")
        keys = await bb.list_keys(phase="p1")
        assert set(keys) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_clear_phase(self):
        bb = SharedBlackboard()
        await bb.write("x", 1, phase="old")
        await bb.write("y", 2, phase="keep")
        await bb.clear_phase("old")
        assert await bb.read("x") is None
        assert await bb.read("y") == 2

    @pytest.mark.asyncio
    async def test_delete_all_phases(self):
        bb = SharedBlackboard()
        await bb.write("x", 1, phase="a")
        await bb.write("x", 2, phase="b")
        deleted = await bb.delete("x")
        assert deleted is True
        assert await bb.read("x") is None


class TestCrossPhaseContext:
    @pytest.mark.asyncio
    async def test_excludes_current_phase(self):
        bb = SharedBlackboard()
        await bb.write("design_done", "API schema ready", phase="design")
        await bb.write("impl_started", "endpoints created", phase="implement")
        ctx = await bb.get_cross_phase_context(exclude_phase="implement")
        assert "design" in ctx
        assert "API schema ready" in ctx
        assert "implement" not in ctx

    @pytest.mark.asyncio
    async def test_empty_when_only_one_phase(self):
        bb = SharedBlackboard()
        await bb.write("x", 1, phase="only")
        ctx = await bb.get_cross_phase_context(exclude_phase="only")
        assert ctx == ""

    @pytest.mark.asyncio
    async def test_get_phase_context(self):
        bb = SharedBlackboard()
        await bb.write("key1", "val1", phase="design")
        await bb.write("key2", "val2", phase="design")
        ctx = await bb.get_phase_context("design")
        assert "design" in ctx
        assert "key1" in ctx


class TestSnapshotRestore:
    def test_snapshot_serializable(self):
        bb = SharedBlackboard()
        bb._phases = {"design": {"k1": {"x": 1}}, "impl": {"k2": "hello"}}
        bb._write_count = 5
        snap = bb.snapshot()
        assert snap["write_count"] == 5
        assert snap["phases"]["design"]["k1"] == {"x": 1}
        json.dumps(snap)

    def test_restore_rebuilds_state(self):
        bb = SharedBlackboard()
        bb.restore({"phases": {"a": {"k": "v"}}, "write_count": 3})
        assert bb.entry_count == 3
        assert bb._phases == {"a": {"k": "v"}}

    def test_restore_empty_does_nothing(self):
        bb = SharedBlackboard()
        bb.restore({})
        assert bb.entry_count == 0


class TestDBSync:
    @pytest.mark.asyncio
    async def test_sync_to_db_handles_error_gracefully(self):
        """sync_to_db no debe crashear si la DB no está disponible."""
        bb = SharedBlackboard()
        await bb.write("k1", {"nested": True}, phase="design")

        with patch(
            "core.database.get_async_session_factory",
            side_effect=RuntimeError("DB offline"),
        ):
            await bb.sync_to_db("sess-1")

        assert await bb.read("k1", phase="design") == {"nested": True}

    @pytest.mark.asyncio
    async def test_sync_from_db_handles_error_gracefully(self):
        """sync_from_db no debe crashear si la DB no está disponible."""
        bb = SharedBlackboard()

        with patch(
            "core.database.get_async_session_factory",
            side_effect=RuntimeError("DB offline"),
        ):
            found = await bb.sync_from_db("sess-1")

        assert found is False

    @pytest.mark.asyncio
    async def test_sync_from_db_returns_false_when_no_entries(self):
        """sync_from_db con excepción interna retorna False."""
        bb = SharedBlackboard()

        with patch(
            "core.database.get_async_session_factory",
            side_effect=RuntimeError("DB not initialized"),
        ):
            found = await bb.sync_from_db("sess-1")

        assert found is False


class TestBackwardCompatibility:
    @pytest.mark.asyncio
    async def test_write_without_phase_uses_default(self):
        bb = SharedBlackboard()
        await bb.write("key", "val")
        assert await bb.read("key", phase="default") == "val"

    @pytest.mark.asyncio
    async def test_get_agent_context_still_works(self):
        bb = SharedBlackboard()
        await bb.write("task_1", "done", phase="design")
        ctx = await bb.get_agent_context()
        assert "task_1" in ctx

    @pytest.mark.asyncio
    async def test_entry_count_tracks_all_writes(self):
        bb = SharedBlackboard()
        await bb.write("a", 1, phase="x")
        await bb.write("b", 2, phase="y")
        assert bb.entry_count == 2
