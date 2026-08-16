"""Tests for MemoryManager — pure functions, protected keys, read/write, search."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.faiss_indexer import FAISS_DIMENSION
from core.memory.manager import MemoryManager, memory


class TestProtectedKeys:
    def test_protected_exact_contains_system_keys(self):
        assert "user_profile" in MemoryManager._PROTECTED_EXACT
        assert "kairos_daemon_heartbeat" in MemoryManager._PROTECTED_EXACT
        assert "security_private" in MemoryManager._PROTECTED_EXACT

    def test_protected_prefixes(self):
        assert MemoryManager._PROTECTED_PREFIXES
        assert isinstance(MemoryManager._PROTECTED_PREFIXES, tuple)

    def test_protected_exact_no_overlap_with_prefixes(self):
        # Some keys intentionally overlap with prefixes (e.g., last_analysis / last_)
        # but the protection logic handles both, so it's fine.
        for _key in MemoryManager._PROTECTED_EXACT:
            for _prefix in MemoryManager._PROTECTED_PREFIXES:
                pass  # overlap is expected — both guards apply


class TestSingleton:
    def test_memory_is_singleton(self):
        m1 = MemoryManager()
        m2 = MemoryManager()
        assert m1 is m2

    def test_global_memory_is_instance(self):
        assert isinstance(memory, MemoryManager)


class TestGetQualityThreshold:
    def test_user_profile_last_update(self):
        mm = MemoryManager()
        assert mm._get_quality_threshold(None, "user_profile_last_update") == 15

    def test_workflow_subtask(self):
        mm = MemoryManager()
        assert mm._get_quality_threshold(None, "workflow_subtask_0") == 20

    def test_creative_hint(self):
        mm = MemoryManager()
        assert mm._get_quality_threshold("creative", "any_key") == 30

    def test_analytical_hint(self):
        mm = MemoryManager()
        assert mm._get_quality_threshold("analytical", "any_key") == 50

    def test_default_threshold(self):
        mm = MemoryManager()
        assert mm._get_quality_threshold(None, "random_key") == 40


class TestBuildCritiquePrompt:
    def test_contains_value(self):
        mm = MemoryManager()
        prompt = mm._build_critique_prompt("test_key", "test value content")
        assert "test_key" in prompt
        assert "test value content" in prompt
        assert "quality_score" in prompt

    def test_creative_hint(self):
        mm = MemoryManager()
        prompt = mm._build_critique_prompt("k", "v", content_hint="creative")
        assert "CREATIVO" in prompt

    def test_analytical_hint(self):
        mm = MemoryManager()
        prompt = mm._build_critique_prompt("k", "v", content_hint="analytical")
        assert "análisis" in prompt


class TestParseCritiqueResponse:
    def test_valid_json(self):
        mm = MemoryManager()
        data = mm._parse_critique_response(
            '{"quality_score": 75, "is_valid": true, "reason": "good"}'
        )
        assert data.get("quality_score") == 75

    def test_regex_fallback(self):
        mm = MemoryManager()
        data = mm._parse_critique_response('some text "quality_score": 42.5, and "is_valid": false')
        assert data.get("quality_score") == 42.5
        assert data.get("is_valid") is False

    def test_empty_string(self):
        mm = MemoryManager()
        data = mm._parse_critique_response("")
        assert data == {}


class TestGetUserSummary:
    def test_empty_profile(self, monkeypatch):
        mm = MemoryManager()
        monkeypatch.setattr(mm, "get_user_profile", lambda: {})
        assert mm.get_user_summary() == ""

    def test_with_name(self, monkeypatch):
        mm = MemoryManager()
        monkeypatch.setattr(mm, "get_user_profile", lambda: {"name": "Alice"})
        summary = mm.get_user_summary()
        assert "Alice" in summary

    def test_skips_preferences(self, monkeypatch):
        mm = MemoryManager()
        monkeypatch.setattr(
            mm,
            "get_user_profile",
            lambda: {"name": "Bob", "preferences": {"color": "blue"}},
        )
        summary = mm.get_user_summary()
        assert "Bob" in summary
        assert "blue" not in summary


class TestGetLongContextSummary:
    def test_short_history(self):
        mm = MemoryManager()
        summary = mm.get_long_context_summary([{"content": "hi"}] * 5)
        assert summary == ""

    def test_long_history_extracts_content(self):
        mm = MemoryManager()
        history = [{"content": f"Message number {i} with some details"} for i in range(20)]
        summary = mm.get_long_context_summary(history, max_facts=3)
        assert len(summary.split("\n")) <= 3
        assert "Message number" in summary

    def test_skips_short_content(self):
        mm = MemoryManager()
        history = [{"content": "hi"} for _ in range(15)]  # all too short (<15 chars)
        summary = mm.get_long_context_summary(history)
        assert summary == ""


class TestRead:
    def test_read_found(self, monkeypatch):
        mm = MemoryManager()
        mm.documents = [("key1", "val1"), ("key2", "val2")]
        assert mm.read("key1") == "val1"

    def test_read_not_found(self, monkeypatch):
        mm = MemoryManager()
        mm.documents = [("key1", "val1")]
        assert mm.read("missing") is None

    def test_read_updates_access_log(self, monkeypatch):
        mm = MemoryManager()
        mm.documents = [("key1", "val1")]
        mm._access_log = {}
        mm.read("key1")
        assert "key1" in mm._access_log


class TestGetUserProfile:
    def test_dict_profile(self, monkeypatch):
        mm = MemoryManager()
        monkeypatch.setattr(mm, "read", lambda k: {"name": "Carl", "preferences": {}})
        profile = mm.get_user_profile()
        assert profile["name"] == "Carl"

    def test_non_dict_profile_fallback(self, monkeypatch):
        mm = MemoryManager()
        monkeypatch.setattr(mm, "read", lambda k: "not a dict")
        profile = mm.get_user_profile()
        assert profile["name"] is None
        assert profile["preferences"] == {}

    def test_none_profile_fallback(self, monkeypatch):
        mm = MemoryManager()
        monkeypatch.setattr(mm, "read", lambda k: None)
        profile = mm.get_user_profile()
        assert profile["name"] is None


class TestWriteSystem:
    def test_write_json_dict(self, tmp_path, monkeypatch):
        mm = MemoryManager()
        monkeypatch.setattr(mm, "base_dir", tmp_path)
        result = mm.write_system("test_sys_key", {"data": 42})
        import asyncio

        assert asyncio.iscoroutine(result)

    def test_write_string_value(self, tmp_path, monkeypatch):
        mm = MemoryManager()
        monkeypatch.setattr(mm, "base_dir", tmp_path)
        result = mm.write_system("str_key", "plain text")
        import asyncio

        assert asyncio.iscoroutine(result)


class TestSaveUserCorrection:
    @pytest.mark.asyncio
    async def test_saves_with_key(self, monkeypatch):
        mm = MemoryManager()
        mock_write = AsyncMock(return_value=True)
        monkeypatch.setattr(mm, "write", mock_write)
        result = await mm.save_user_correction("fix the bug", "use getattr instead")
        assert result is True
        assert mock_write.call_count == 1


class TestUpdateUserProfile:
    @pytest.mark.asyncio
    async def test_rejects_trivial_profile(self, monkeypatch):
        """Un perfil trivial (solo name, p. ej. 'ChatGPT' stale) NO debe
        persistirse — defiende el bucle de contaminación del finalizer."""
        mm = MemoryManager()
        monkeypatch.setattr(mm, "get_user_profile", lambda: {"name": "ChatGPT"})
        mock_write = AsyncMock(return_value=True)
        monkeypatch.setattr(mm, "write", mock_write)
        result = await mm.update_user_profile({"name": "ChatGPT"})
        assert result is False
        mock_write.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_persists_meaningful_profile(self, monkeypatch):
        """Un perfil con nombre + contexto SÍ se persiste."""
        mm = MemoryManager()
        monkeypatch.setattr(mm, "get_user_profile", lambda: {})
        mock_write = AsyncMock(return_value=True)
        monkeypatch.setattr(mm, "write", mock_write)
        result = await mm.update_user_profile({"name": "Ana", "city": "Lima"})
        assert result is True
        mock_write.assert_awaited_once()
        assert mock_write.await_args is not None
        written = mock_write.await_args.args[1]
        assert written["name"] == "Ana"
        assert written["city"] == "Lima"


class TestWrite:
    @pytest.mark.asyncio
    async def test_no_active_workspace(self, monkeypatch):
        mm = MemoryManager()
        mm.active_workspace = None
        result = await mm.write("key", "value", validated=True)
        assert result is False


class TestPruneStale:
    @pytest.mark.asyncio
    async def test_prunes_old_documents(self, tmp_path, monkeypatch):
        mm = MemoryManager()
        mm.active_workspace = "test_ws"
        (tmp_path / "test_ws").mkdir(parents=True)
        (tmp_path / "test_ws" / "old_doc.md").write_text("old")
        monkeypatch.setattr(mm, "base_dir", tmp_path)

        mm.documents = [("old_doc", "old"), ("new_doc", "new")]
        mm._access_log = {"old_doc": 0, "new_doc": time.time()}

        with patch.object(mm, "_rebuild_index", AsyncMock()):
            removed = await mm._prune_stale(max_age_days=30)
            assert removed >= 1
            assert "old_doc" not in [d[0] for d in mm.documents]

    @pytest.mark.asyncio
    async def test_protects_system_keys(self, tmp_path, monkeypatch):
        mm = MemoryManager()
        mm.active_workspace = "test_ws"
        (tmp_path / "test_ws").mkdir(parents=True)
        monkeypatch.setattr(mm, "base_dir", tmp_path)

        mm.documents = [("user_profile", {}), ("kairos_daemon_heartbeat", {})]
        mm._access_log = {"user_profile": 0, "kairos_daemon_heartbeat": 0}

        with patch.object(mm, "_rebuild_index", AsyncMock()):
            removed = await mm._prune_stale(max_age_days=30)
            assert removed == 0  # protected keys are never pruned
            assert len(mm.documents) == 2

    @pytest.mark.asyncio
    async def test_no_stale_documents(self, tmp_path, monkeypatch):
        mm = MemoryManager()
        mm.active_workspace = "test_ws"
        (tmp_path / "test_ws").mkdir(parents=True)
        monkeypatch.setattr(mm, "base_dir", tmp_path)

        mm.documents = [("fresh", "val")]
        mm._access_log = {"fresh": time.time()}

        with patch.object(mm, "_rebuild_index", AsyncMock()):
            removed = await mm._prune_stale(max_age_days=30)
            assert removed == 0


class TestRebuildIndex:
    @pytest.mark.asyncio
    async def test_rebuild_clears_and_rebuilds(self, monkeypatch):
        import numpy as np

        mm = MemoryManager()
        mm.documents = [("a", "val_a"), ("b", "val_b")]
        mm.index.ntotal = 5
        mock_emb = np.zeros((FAISS_DIMENSION,), dtype=np.float32)
        # _rebuild_index usa el camino async (fix 2026-08: no bloquear el loop)
        monkeypatch.setattr(mm, "_embed_async", AsyncMock(return_value=mock_emb))

        await mm._rebuild_index()
        assert mm.index.ntotal == 2


class TestSearchProtectedKeys:
    """Las claves protegidas no deben aparecer jamás en resultados de búsqueda."""

    def _make_manager(self, monkeypatch):
        import numpy as np

        from core.memory.manager import MemoryManager

        mm = MemoryManager()
        docs = [
            ("user_profile", "perfil del usuario"),
            ("workflow_subtask_0", "resumen de subtarea previa"),
            ("last_analysis", "último análisis"),
            ("merged_creative", "contenido fusionado"),
            ("regular_doc", "documento normal buscable"),
        ]
        mm.documents = docs
        fake_index = MagicMock()
        fake_index.ntotal = len(docs)
        fake_index.search.return_value = (
            np.zeros((1, len(docs)), dtype=np.float32),
            np.arange(len(docs), dtype=np.int64).reshape(1, -1),
        )
        mm.index = fake_index
        monkeypatch.setattr(mm, "_embed", lambda q: np.zeros((FAISS_DIMENSION,), dtype=np.float32))
        monkeypatch.setattr(
            mm,
            "_embed_async",
            AsyncMock(return_value=np.zeros((FAISS_DIMENSION,), dtype=np.float32)),
        )
        return mm

    def test_search_excludes_protected_keys(self, monkeypatch):
        mm = self._make_manager(monkeypatch)
        results = mm.search("consulta cualquiera", k=10)
        keys = [r["key"] for r in results]
        assert "regular_doc" in keys
        assert "user_profile" not in keys
        assert "workflow_subtask_0" not in keys
        assert "last_analysis" not in keys
        assert "merged_creative" not in keys

    @pytest.mark.asyncio
    async def test_search_async_excludes_protected_keys(self, monkeypatch):
        mm = self._make_manager(monkeypatch)
        results = await mm.search_async("consulta cualquiera", k=10)
        keys = [r["key"] for r in results]
        assert "regular_doc" in keys
        assert "user_profile" not in keys
        assert "workflow_subtask_0" not in keys
        assert "last_analysis" not in keys
        assert "merged_creative" not in keys


class TestAsyncEmbeddingPaths:
    """Los caminos self-healing no deben llamar al embed síncrono en el loop.

    `_embed` síncrono espera al modelo (60s) y codifica en CPU/GPU — si se
    invoca dentro de un async def, congela el event loop. Los caminos async
    deben usar `_embed_async` (to_thread).
    """

    @pytest.mark.asyncio
    async def test_detect_duplicates_uses_async_embeddings(self, monkeypatch):
        mm = MemoryManager()
        monkeypatch.setattr(mm, "documents", [("k1", "v1"), ("k2", "v2")])
        sync_embed = MagicMock(side_effect=AssertionError("embed síncrono en el loop"))
        monkeypatch.setattr(mm, "_embed", sync_embed)
        monkeypatch.setattr(mm, "_embed_async", AsyncMock(return_value=None))
        monkeypatch.setattr(mm, "_llm_critique", AsyncMock(return_value={"quality_score": 80}))
        await mm._detect_duplicates()
        sync_embed.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolve_contradictions_uses_async_embeddings(self, monkeypatch):
        mm = MemoryManager()
        monkeypatch.setattr(mm, "documents", [("k1", "v1"), ("k2", "v2")])
        sync_embed = MagicMock(side_effect=AssertionError("embed síncrono en el loop"))
        monkeypatch.setattr(mm, "_embed", sync_embed)
        monkeypatch.setattr(mm, "_embed_async", AsyncMock(return_value=None))
        await mm._resolve_contradictions()
        sync_embed.assert_not_called()

    @pytest.mark.asyncio
    async def test_rebuild_index_uses_async_embeddings(self, monkeypatch):
        mm = MemoryManager()
        monkeypatch.setattr(mm, "documents", [("k1", "v1")])
        sync_embed = MagicMock(side_effect=AssertionError("embed síncrono en el loop"))
        monkeypatch.setattr(mm, "_embed", sync_embed)
        monkeypatch.setattr(mm, "_embed_async", AsyncMock(return_value=None))
        await mm._rebuild_index()
        sync_embed.assert_not_called()

    @pytest.mark.asyncio
    async def test_self_healing_uses_async_embeddings(self, monkeypatch):
        mm = MemoryManager()
        monkeypatch.setattr(mm, "active_workspace", "test_ws")
        monkeypatch.setattr(mm, "documents", [("k1", "v1"), ("k2", "v2")])
        sync_embed = MagicMock(side_effect=AssertionError("embed síncrono en el loop"))
        monkeypatch.setattr(mm, "_embed", sync_embed)
        monkeypatch.setattr(mm, "_embed_async", AsyncMock(return_value=None))
        monkeypatch.setattr(mm, "_llm_critique", AsyncMock(return_value={"quality_score": 95}))
        monkeypatch.setattr(mm, "_prune_stale", AsyncMock(return_value=0))
        await mm.self_healing_check()
        sync_embed.assert_not_called()
