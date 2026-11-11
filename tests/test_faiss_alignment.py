"""Invariantes de alineación índice FAISS ↔ documents del MemoryManager.

- Si un embedding falla en rebuild, el doc se excluye de AMBAS estructuras.
- El mapeo id→clave estable sobrevive a rebuilds (IndexIDMap2).
"""

from unittest.mock import MagicMock

import faiss
import numpy as np
import pytest

from core.faiss_indexer import FAISS_DIMENSION
from core.memory.manager import MemoryManager


def _aligned_manager(tmp_path):
    """Manager aislado (sin singleton) con índice IDMap2 y embedder mockeado."""
    mm = MemoryManager.__new__(MemoryManager)
    mm.base_dir = tmp_path
    mm.active_workspace = "ws"
    mm.documents = []
    mm.index = faiss.IndexIDMap2(faiss.IndexFlatL2(FAISS_DIMENSION))
    mm._ids = {}
    mm._id_to_key = {}
    mm._next_id = 0
    mm._access_log = {}
    mm.embedder = MagicMock()
    mm.embedder.wait_until_ready.return_value = True
    mm.embedder.encode = lambda t, kind="passage": np.ones(FAISS_DIMENSION, dtype="float32")
    return mm


@pytest.mark.asyncio
async def test_rebuild_keeps_index_and_documents_aligned(monkeypatch, tmp_path):
    """Si un embedding falla, el doc debe EXCLUIRSE también de documents."""
    m = _aligned_manager(tmp_path)
    m.documents = [("a", "doc a"), ("b", "doc b"), ("c", "doc c")]

    calls = {"n": 0}

    async def flaky_embed(text):
        calls["n"] += 1
        if calls["n"] == 2:  # segundo embedding falla
            return None
        return np.zeros(FAISS_DIMENSION, dtype="float32")

    monkeypatch.setattr(m, "_embed_async", flaky_embed)
    await m._rebuild_index()
    assert m.index.ntotal == len(
        m.documents
    ), f"INVARIANTE roto: ntotal={m.index.ntotal} vs documents={len(m.documents)}"
    assert [k for k, _ in m.documents] == ["a", "c"]
    assert set(m._ids) == {"a", "c"}
    assert m._id_to_key[m._ids["a"]] == "a"


@pytest.mark.asyncio
async def test_search_resolves_by_stable_ids_after_rebuild(monkeypatch, tmp_path):
    """Tras un rebuild con exclusión, search resuelve por id estable (no posicional).

    Nota: MemoryManager es singleton (__new__ sobrecargado) — toda mutación
    de la instancia usa monkeypatch para restaurarse al terminar.
    """
    m = _aligned_manager(tmp_path)
    vecs = {
        "a": np.ones(FAISS_DIMENSION, dtype="float32"),
        "b": np.full(FAISS_DIMENSION, -1.0, dtype="float32"),
    }
    m.embedder.encode = lambda t, kind="passage": vecs.get(str(t)[:1], vecs["a"])

    assert await m.write("a", "alpha doc", validated=True)
    assert await m.write("b", "beta doc", validated=True)

    async def failing_embed(text):
        return None  # todo falla → rebuild vacío alineado

    monkeypatch.setattr(m, "_embed_async", failing_embed)
    await m._rebuild_index()
    assert len(m.documents) == 0
    assert m.search("alpha", k=5) == []
