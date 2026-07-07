# tests/test_memory_consolidation.py
"""Tests for memory consolidation: duplicate detection, contradiction resolution, pruning."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestAccessTracking:
    def test_read_updates_access_log(self):
        from core.memory.manager import MemoryManager

        mgr = MemoryManager()
        mgr.documents = [("test_key", "test_value")]
        mgr._access_log = {}
        mgr.active_workspace = "main"

        result = mgr.read("test_key")
        assert result == "test_value"
        assert mgr._access_log["test_key"] > 0

    def test_read_unknown_key(self):
        from core.memory.manager import MemoryManager

        mgr = MemoryManager()
        mgr.documents = []
        mgr._access_log = {}
        mgr.active_workspace = "main"

        result = mgr.read("nonexistent")
        assert result is None
        assert "nonexistent" not in mgr._access_log


class TestPruneStale:
    @pytest.mark.asyncio
    async def test_prunes_old_documents(self):
        from core.memory.manager import MemoryManager

        mgr = MemoryManager()
        mgr.active_workspace = "main"
        mgr.base_dir = MagicMock()
        mgr.documents = [("old_doc", "stale content"), ("recent_doc", "fresh content")]
        mgr._access_log = {
            "old_doc": time.time() - (31 * 86400),
            "recent_doc": time.time(),
        }
        mgr._embed = MagicMock(return_value=None)

        with patch.object(mgr, "_rebuild_index", new_callable=AsyncMock):
            removed = await mgr._prune_stale(max_age_days=30)
            assert removed == 1
            remaining = [k for k, _ in mgr.documents]
            assert "old_doc" not in remaining
            assert "recent_doc" in remaining

    @pytest.mark.asyncio
    async def test_protects_system_keys(self):
        from core.memory.manager import MemoryManager

        mgr = MemoryManager()
        mgr.active_workspace = "main"
        mgr.base_dir = MagicMock()
        mgr.documents = [
            ("user_profile", "profile data"),
            ("kairos_daemon_heartbeat", "beat"),
            ("security_private", "secret"),
        ]
        mgr._access_log = {
            "user_profile": time.time() - (60 * 86400),
            "kairos_daemon_heartbeat": time.time() - (60 * 86400),
            "security_private": time.time() - (60 * 86400),
        }
        mgr._embed = MagicMock(return_value=None)

        with patch.object(mgr, "_rebuild_index", new_callable=AsyncMock):
            removed = await mgr._prune_stale(max_age_days=30)
            assert removed == 0
            assert len(mgr.documents) == 3

    @pytest.mark.asyncio
    async def test_no_stale_documents(self):
        from core.memory.manager import MemoryManager

        mgr = MemoryManager()
        mgr.active_workspace = "main"
        mgr.base_dir = MagicMock()
        mgr.documents = [("fresh", "content")]
        mgr._access_log = {"fresh": time.time()}
        mgr._embed = MagicMock(return_value=None)

        with patch.object(mgr, "_rebuild_index", new_callable=AsyncMock):
            removed = await mgr._prune_stale(max_age_days=30)
            assert removed == 0
            assert len(mgr.documents) == 1


class TestRebuildIndex:
    @pytest.mark.asyncio
    async def test_rebuild_clears_and_rebuilds(self):
        import numpy as np

        from core.faiss_indexer import FAISS_DIMENSION
        from core.memory.manager import MemoryManager

        mgr = MemoryManager()
        mgr.documents = [("doc1", "hello world")]
        mgr.active_workspace = "main"

        mgr._embed = MagicMock(return_value=np.zeros(FAISS_DIMENSION, dtype=np.float32))

        await mgr._rebuild_index()
        assert mgr.index.ntotal == 1
