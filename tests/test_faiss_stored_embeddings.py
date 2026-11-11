# tests/test_faiss_stored_embeddings.py — dimensión derivada + embeddings almacenados
"""FAISSIndexer resuelve la dimensión del backend efectivo (adiós al
hardcode 1024) y guarda los embeddings alineados en RAM: rebuild y
remove_prefix dejan de re-encodear O(n) documentos."""

import logging

import numpy as np

from core.embedding_provider import EmbeddingProvider
from core.faiss_indexer import FAISS_DIMENSION, FAISSIndexer


class _CountingEmbedder:
    """Embedder determinista que CUENTA los encodes — el assertions de
    'no re-encodea' se hacen sobre este contador."""

    def __init__(self, dim: int = FAISS_DIMENSION):
        self.dim = dim
        self.encodes = 0

    def wait_until_ready(self, timeout: float = 60) -> bool:
        return True

    def get_instance(self):
        return self

    def encode(self, text, kind="passage"):
        self.encodes += 1
        return np.ones(self.dim, dtype="float32")

    def dimension(self):
        return self.dim


class _EmbedderSinDim:
    def wait_until_ready(self, timeout: float = 60) -> bool:
        return True

    def get_instance(self):
        return self

    def encode(self, text, kind="passage"):
        return np.ones(FAISS_DIMENSION, dtype="float32")


# ── Dimensión derivada ────────────────────────────────────────────────────


def test_dimension_derivada_del_embedder():
    ix = FAISSIndexer(embedder=_CountingEmbedder(dim=768))
    assert ix.index.d == 768


def test_dimension_explicita_pisa_la_derivada():
    ix = FAISSIndexer(dimension=42, embedder=_CountingEmbedder(dim=768))
    assert ix.index.d == 42


def test_dimension_cae_al_provider_cuando_el_embedder_no_declara(monkeypatch):
    monkeypatch.setattr(EmbeddingProvider, "dimension", classmethod(lambda cls: 2048))
    ix = FAISSIndexer(embedder=_EmbedderSinDim())
    assert ix.index.d == 2048


def test_dimension_desconocida_cae_al_default_con_warning(monkeypatch, caplog):
    """Ni embedder ni provider declaran dim: fallback 1024 + warning loud —
    los chunks de un backend de otra dim serán rechazados, pero VISIBLE."""
    monkeypatch.setattr(EmbeddingProvider, "dimension", classmethod(lambda cls: 0))
    with caplog.at_level(logging.WARNING, logger="core.faiss_indexer"):
        ix = FAISSIndexer(embedder=_EmbedderSinDim())
    assert ix.index.d == FAISS_DIMENSION
    assert "DESCONOCIDA" in caplog.text


def test_load_deriva_la_dimension_esperada_del_backend(tmp_path, monkeypatch):
    """Un cache guardado con dim 768 y un backend que declara 768: el
    load espera 768 (no el hardcode 1024) y carga limpio."""
    emb = _CountingEmbedder(dim=768)
    ix = FAISSIndexer(embedder=emb)
    ix.add_embedding("a.py:L1", {"file": "a.py"}, np.ones(768, dtype="float32"))
    cache = tmp_path / "cache"
    ix.save(cache)

    loaded = FAISSIndexer.load(cache, embedder=emb)
    assert loaded.index.d == 768
    assert loaded.document_count == 1


# ── Embeddings almacenados: rebuild y remove sin re-encode ───────────────


def _mk_with_two_docs(emb):
    ix = FAISSIndexer(embedder=emb)
    vec = np.ones(emb.dim, dtype="float32")
    ix.add_embedding("a.py:L1", {"file": "a.py"}, vec)
    ix.add_embedding("b.py:L1", {"file": "b.py"}, vec)
    return ix


def test_rebuild_no_re_encodea_lo_almacenado():
    emb = _CountingEmbedder()
    ix = _mk_with_two_docs(emb)
    assert emb.encodes == 0  # add_embedding es pre-calculado
    ix.rebuild_index()
    assert emb.encodes == 0  # usa los embeddings almacenados
    assert ix.index.ntotal == 2 and ix.document_count == 2


def test_remove_prefix_no_re_encodea_y_filtra_correcto():
    emb = _CountingEmbedder()
    ix = _mk_with_two_docs(emb)
    removed = ix.remove_prefix("a.py:")
    assert removed == 1
    assert emb.encodes == 0
    assert [k for k, _ in ix.documents] == ["b.py:L1"]
    assert ix.index.ntotal == 1
    assert len(ix._embeddings) == 1  # lista alineada también


def test_remove_prefixes_una_realineacion_para_n_prefijos():
    emb = _CountingEmbedder()
    ix = FAISSIndexer(embedder=emb)
    vec = np.ones(emb.dim, dtype="float32")
    for name in ("a.py", "b.py", "c.py", "d.py"):
        ix.add_embedding(f"{name}:L1", {"file": name}, vec)
    removed = ix.remove_prefixes(["a.py:", "b.py:", "c.py:"])
    assert removed == 3
    assert emb.encodes == 0
    assert [k for k, _ in ix.documents] == ["d.py:L1"]
    assert ix.index.ntotal == 1


def test_rebuild_reencodea_solo_lo_que_no_tiene_vector():
    """Un documento cuyo embedding no está almacenado (embedder legacy que
    añadió por `add`... aquí lo forzamos limpiando la lista) re-encodea."""
    emb = _CountingEmbedder()
    ix = _mk_with_two_docs(emb)
    ix._embeddings = []  # simula un path sin embeddings almacenados
    ix.rebuild_index()
    assert emb.encodes == 2  # fallback histórico: re-encodeó ambos
    assert ix.index.ntotal == 2


def test_load_puebla_embeddings_y_remove_posterior_no_re_encodea(tmp_path):
    emb = _CountingEmbedder()
    ix = _mk_with_two_docs(emb)
    cache = tmp_path / "cache"
    ix.save(cache)

    loaded = FAISSIndexer.load(cache, embedder=emb)
    assert len(loaded._embeddings) == 2  # reconstruidos vía index.reconstruct
    emb.encodes = 0
    removed = loaded.remove_prefix("a.py:")
    assert removed == 1
    assert emb.encodes == 0  # el remove tras load TAMPOCO re-encodea


def test_rebuild_excluye_documento_con_dim_incoherente(caplog):
    """Un vector de otra dim (backend cambiado a mitad) queda fuera de AMBAS
    estructuras — ntotal == documents se preserva."""
    emb = _CountingEmbedder(dim=FAISS_DIMENSION)
    ix = FAISSIndexer(embedder=emb)
    ix.add_embedding("ok.py:L1", {"file": "ok.py"}, np.ones(FAISS_DIMENSION, dtype="float32"))
    ix._embeddings.append(np.ones(64, dtype="float32"))  # huérfano de otra dim
    ix.documents.append(("raro.py:L1", {"file": "raro.py"}))
    with caplog.at_level(logging.WARNING, logger="core.faiss_indexer"):
        ix.rebuild_index()
    assert ix.index.ntotal == 1
    assert ix.document_count == 1
    assert [k for k, _ in ix.documents] == ["ok.py:L1"]


def test_meta_model_refleja_el_backend_efectivo(tmp_path, monkeypatch):
    """El campo model del stamp describe QUIÉN produjo los vectores: con un
    backend remoto, settings.embed_model (el local) miente sobre la
    procedencia. El fingerprint sigue siendo el campo validado."""
    import json

    from core.embedding_provider import EmbeddingProvider, OpenAIEmbedBackend

    monkeypatch.setattr(
        EmbeddingProvider,
        "_resolve_backend",
        lambda: ("openai", OpenAIEmbedBackend("nvidia/nemotron-3-embed-1b")),
    )
    emb = _CountingEmbedder(dim=8)
    ix = FAISSIndexer(embedder=emb, dimension=8)
    ix.add_embedding("a.py:L1", {"file": "a.py"}, np.ones(8, dtype="float32"))
    cache = tmp_path / "cache"
    ix.save(cache)
    meta = json.loads((cache / "index.meta.json").read_text())
    assert meta["model"] == "nvidia/nemotron-3-embed-1b"
