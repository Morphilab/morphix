# tests/test_embedding_cache.py — caché de embeddings + exclusión de docs
"""LRU en-proceso de embeddings (el encode no se re-computa para la misma
entrada; no hay Redis). Los documentos excluidos del índice por fallo de
embedding quedan registrados en MemoryManager.last_switch_skipped."""

import numpy as np
import pytest

from core.embedding_provider import EmbeddingProvider


@pytest.fixture(autouse=True)
def _reset_cache():
    with EmbeddingProvider._cache_lock:
        EmbeddingProvider._cache.clear()
    EmbeddingProvider._cache_hits = 0
    EmbeddingProvider._cache_misses = 0
    yield
    with EmbeddingProvider._cache_lock:
        EmbeddingProvider._cache.clear()
    EmbeddingProvider._cache_hits = 0
    EmbeddingProvider._cache_misses = 0


@pytest.fixture
def fake_local(monkeypatch):
    """Backend local fake que cuenta invocaciones."""
    calls = {"n": 0}
    vec = np.ones(8, dtype="float32")

    def _encode_single_local(cls, text, kind="passage"):
        calls["n"] += 1
        return vec

    def _encode_batch_local(cls, texts, kind="passage"):
        calls["n"] += len(texts)
        return [vec.copy() for _ in texts]

    monkeypatch.setattr(
        EmbeddingProvider, "_resolve_backend", classmethod(lambda cls: ("local", None))
    )
    monkeypatch.setattr(
        EmbeddingProvider, "_encode_single_local", classmethod(_encode_single_local)
    )
    monkeypatch.setattr(EmbeddingProvider, "_encode_batch_local", classmethod(_encode_batch_local))
    return calls


def test_encode_segunda_llamada_es_cache_hit(fake_local):
    a1 = EmbeddingProvider.encode("hola mundo")
    a2 = EmbeddingProvider.encode("hola mundo")
    assert fake_local["n"] == 1, "el segundo encode re-computó"
    assert np.array_equal(a1, a2)


def test_encode_devuelve_copias_no_la_cached(fake_local):
    a1 = EmbeddingProvider.encode("texto")
    a1[:] = 99.0  # el llamador muta su copia
    a2 = EmbeddingProvider.encode("texto")
    assert float(a2[0]) != 99.0, "la mutación del llamador corrompió la caché"


def test_kind_distinto_es_clave_distinta(fake_local):
    EmbeddingProvider.encode("texto", kind="passage")
    EmbeddingProvider.encode("texto", kind="query")
    assert fake_local["n"] == 2


def test_fingerprint_distinto_invalida(fake_local):
    EmbeddingProvider.encode("texto")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(EmbeddingProvider, "fingerprint", classmethod(lambda cls: "deadbeef"))
        EmbeddingProvider.encode("texto")
    assert fake_local["n"] == 2


def test_encode_batch_misses_y_hits(fake_local):
    out1 = EmbeddingProvider.encode_batch(["a", "b"])
    assert fake_local["n"] == 2 and len(out1) == 2
    out2 = EmbeddingProvider.encode_batch(["a", "b", "c"])
    assert fake_local["n"] == 3, "los hits re-computaron"
    assert all(x is not None for x in out2)


def test_cache_stats(fake_local):
    EmbeddingProvider.encode("x1")
    EmbeddingProvider.encode("x1")
    stats = EmbeddingProvider.cache_stats()
    assert stats["size"] == 1 and stats["hits"] == 1 and stats["misses"] == 1


def test_cache_lru_cap():
    EmbeddingProvider._EMBED_CACHE_MAX = 3
    try:
        for i in range(5):
            EmbeddingProvider._cache_put(("k", i), np.zeros(2, dtype="float32"))
        with EmbeddingProvider._cache_lock:
            assert len(EmbeddingProvider._cache) == 3
    finally:
        EmbeddingProvider._EMBED_CACHE_MAX = 2048


async def test_memory_switch_registra_excluidos(tmp_path, monkeypatch):
    """Un doc cuyo embedding falla queda excluido del índice PERO registrado
    en last_switch_skipped y advertido en el log."""
    from core.memory.manager import MemoryManager

    mm = MemoryManager()
    monkeypatch.setattr(mm, "base_dir", tmp_path)
    ws = tmp_path / "ws_m6"
    ws.mkdir()
    (ws / "bueno.md").write_text("contenido sano", encoding="utf-8")
    (ws / "malo.md").write_text("contenido que rompe", encoding="utf-8")

    def _embed(text):
        if "rompe" in text:
            raise RuntimeError("modelo caído")
        # dimensión = FAISS_DIMENSION del índice (1024)
        return np.ones(1024, dtype="float32")

    monkeypatch.setattr(mm, "_embed", _embed)
    await mm.switch_workspace("ws_m6")

    assert mm.last_switch_skipped == ["malo"]
    assert {k for k, _ in mm.documents} == {"bueno"}
