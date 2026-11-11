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
    async def test_prunes_old_documents(self, monkeypatch):
        from core.memory.manager import MemoryManager

        mgr = MemoryManager()
        mgr.active_workspace = "main"
        mgr.base_dir = MagicMock()
        mgr.documents = [("old_doc", "stale content"), ("recent_doc", "fresh content")]
        mgr._access_log = {
            "old_doc": time.time() - (31 * 86400),
            "recent_doc": time.time(),
        }
        monkeypatch.setattr(mgr, "_embed", MagicMock(return_value=None), raising=False)

        with patch.object(mgr, "_rebuild_index", new_callable=AsyncMock):
            removed = await mgr._prune_stale(max_age_days=30)
            assert removed == 1
            remaining = [k for k, _ in mgr.documents]
            assert "old_doc" not in remaining
            assert "recent_doc" in remaining

    @pytest.mark.asyncio
    async def test_protects_system_keys(self, monkeypatch):
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
        monkeypatch.setattr(mgr, "_embed", MagicMock(return_value=None), raising=False)

        with patch.object(mgr, "_rebuild_index", new_callable=AsyncMock):
            removed = await mgr._prune_stale(max_age_days=30)
            assert removed == 0
            assert len(mgr.documents) == 3

    @pytest.mark.asyncio
    async def test_no_stale_documents(self, monkeypatch):
        from core.memory.manager import MemoryManager

        mgr = MemoryManager()
        mgr.active_workspace = "main"
        mgr.base_dir = MagicMock()
        mgr.documents = [("fresh", "content")]
        mgr._access_log = {"fresh": time.time()}
        monkeypatch.setattr(mgr, "_embed", MagicMock(return_value=None), raising=False)

        with patch.object(mgr, "_rebuild_index", new_callable=AsyncMock):
            removed = await mgr._prune_stale(max_age_days=30)
            assert removed == 0
            assert len(mgr.documents) == 1


class TestRebuildIndex:
    @pytest.mark.asyncio
    async def test_rebuild_clears_and_rebuilds(self, monkeypatch):
        import numpy as np

        from core.faiss_indexer import FAISS_DIMENSION
        from core.memory.manager import MemoryManager

        mgr = MemoryManager()
        mgr.documents = [("doc1", "hello world")]
        mgr.active_workspace = "main"

        # _rebuild_index usa el camino async — patch de CLASE: parchear la
        # INSTANCIA del singleton deja el attr en __dict__ y sombrea patches
        # de clase de tests posteriores (doctrina del 09-01).
        monkeypatch.setattr(
            MemoryManager,
            "_embed_async",
            AsyncMock(return_value=np.zeros(FAISS_DIMENSION, dtype=np.float32)),
        )

        await mgr._rebuild_index()
        assert mgr.index.ntotal == 1


class TestBackendDimChange:
    """El backend de embeddings puede cambiar EN CALIENTE (toggle de offline
    en la GUI: openai/DeepInfra 4096 → ollama/nomic 768; breaker; salud del
    proveedor). El índice de memoria se crea UNA vez en __init__: sin
    reconstrucción, el primer write tras el cambio muere en el assert de
    FAISS (d == self.d) — el log de arranque lo cazó en user_profile."""

    @pytest.mark.asyncio
    async def test_write_reconstruye_indice_al_cambiar_la_dim(self, tmp_path, monkeypatch):
        import numpy as np

        from core import embedding_provider as ep
        from core.memory.manager import MemoryManager

        dims = {"current": 4096}
        monkeypatch.setattr(
            ep.EmbeddingProvider, "dimension", classmethod(lambda cls: dims["current"])
        )

        async def _fake_embed_async(self, text, kind="passage"):
            return np.ones(dims["current"], dtype="float32")

        def _fake_embed(self, text, kind="passage"):
            return np.ones(dims["current"], dtype="float32")

        monkeypatch.setattr(MemoryManager, "_embed_async", _fake_embed_async)
        monkeypatch.setattr(MemoryManager, "_embed", _fake_embed)

        mgr = MemoryManager()
        # El singleton puede arrastrar overrides de INSTANCIA de tests previos
        # (monkeypatch sobre el singleton deja el bound method en __dict__ al
        # restaurar y sombrea patches de clase — footgun documentado): se
        # limpian para que el patch de CLASE gobierne.
        for _attr in ("_embed", "_embed_async", "_llm_critique"):
            mgr.__dict__.pop(_attr, None)
        mgr.active_workspace = "main"
        mgr.base_dir = tmp_path / "memory"
        initial_dim = mgr.index.d
        new_dim = 768 if initial_dim != 768 else 1024

        dims["current"] = new_dim  # toggle a una dimensión distinta
        ok = await mgr.write("user_profile", {"name": "x"}, validated=True)

        assert ok is True, "el write debe reconstruir el índice, no morir en FAISS"
        assert mgr.index.d == new_dim, "el índice reconstruido usa la dim del backend nuevo"
        assert mgr.index.ntotal == 1
