"""FAISSIndexer: remove_prefix y deletions selectivas, persistencia atómica
(save/load) y rebuild condicional en self-healing."""

import faiss
import numpy as np
import pytest

from core.faiss_indexer import FAISS_DIMENSION, FAISSIndexer


class _FakeEmbedder:
    """Embedder determinista: vector de ones (rápido, sin modelo real)."""

    def wait_until_ready(self, timeout: float = 60) -> bool:
        return True

    def get_instance(self):
        return self

    def encode(self, text, kind="passage"):
        return np.ones(FAISS_DIMENSION, dtype="float32")


# ═══════════ remove_prefix ═══════════


def test_remove_prefix_selective_and_rebuilds():
    ix = FAISSIndexer(embedder=_FakeEmbedder())
    ix.add("utils.py:L1", {"file": "utils.py"})
    ix.add("utils.py:L2", {"file": "utils.py"})
    ix.add("main.py:L1", {"file": "main.py"})

    ix.remove_prefix("utils.py:")

    keys = [k for k, _ in ix.documents]
    assert keys == ["main.py:L1"], f"prefijo no removido selectivamente: {keys}"
    assert (
        ix.index.ntotal == len(ix.documents) == 1
    ), f"índice desalineado tras remove_prefix: ntotal={ix.index.ntotal}"
    results = ix.search("main", k=3)
    assert [r["key"] for r in results] == ["main.py:L1"]


def test_remove_prefix_nonexistent_is_noop():
    ix = FAISSIndexer(embedder=_FakeEmbedder())
    ix.add("a.py:L1", {})
    ix.remove_prefix("zzz:")
    assert len(ix.documents) == 1 and ix.index.ntotal == 1


# ═══════════ deletions propagadas en codebase_indexer ═══════════


@pytest.mark.asyncio
async def test_deleted_files_removed_on_reindex(tmp_path, monkeypatch):
    from core.codebase_indexer import CodebaseIndexer

    ws_dir = tmp_path / "ws"
    proj = ws_dir / "code_projects" / "proj"
    proj.mkdir(parents=True)
    (proj / "keep.py").write_text("x = 1\n", encoding="utf-8")
    gone = proj / "gone.py"
    gone.write_text("y = 2\n", encoding="utf-8")

    import core.path_resolver as pr

    monkeypatch.setattr(pr, "MEMORY_BASE", tmp_path)
    monkeypatch.setattr(CodebaseIndexer, "_cache_dir", lambda self: tmp_path / "cache_idx")

    cbi = CodebaseIndexer(project_root="code_projects/proj", workspace="ws")
    cbi.indexer = FAISSIndexer(embedder=_FakeEmbedder())

    # Indexar ambos archivos
    cbi.index_project(force=True)

    hashes_after_first = dict(cbi._file_hashes)
    assert "gone.py" in hashes_after_first

    # Borrar gone.py y reindexar
    gone.unlink()
    cbi.index_project()

    assert "gone.py" not in cbi._file_hashes, "hash de archivo borrado es eterno"
    stale_keys = [k for k, _ in cbi.indexer.documents if str(k).startswith("gone.py:")]
    assert not stale_keys, f"chunks stale de borrado: {stale_keys}"


# ═══════════ save atómico + load validado ═══════════


def test_save_atomic_and_load_validates(tmp_path):
    ix = FAISSIndexer(embedder=_FakeEmbedder())
    ix.add("doc1", {"v": 1})
    ix.add("doc2", {"v": 2})
    ix.save(tmp_path)

    loaded = FAISSIndexer.load(tmp_path)
    assert loaded.document_count == 2
    assert loaded.index.ntotal == 2


def test_load_rejects_dimension_mismatch(tmp_path):
    ix = FAISSIndexer(dimension=FAISS_DIMENSION, embedder=_FakeEmbedder())
    ix.add("d", {})
    ix.save(tmp_path)

    # Índice VÁLIDO pero con dimensión distinta (save consistente con checksums)
    other = FAISSIndexer(dimension=8, embedder=_FakeEmbedder())
    other.index = __import__("faiss", fromlist=["IndexFlatL2"]).IndexFlatL2(8)
    import numpy as _np

    other.index.add(_np.ones((1, 8), dtype="float32"))
    other.documents = [("d", {})]
    other.save(tmp_path)

    from core.faiss_indexer import FAISS_DIMENSION as REAL_DIM

    with pytest.raises(ValueError, match="[Dd]imensi"):
        FAISSIndexer.load(tmp_path, dimension=REAL_DIM)


def test_load_rejects_ntotal_documents_desync(tmp_path):
    ix = FAISSIndexer(embedder=_FakeEmbedder())
    ix.add("d1", {})
    ix.save(tmp_path)

    # Desincronizar: documents.pkl con más docs que vectores
    import pickle

    with open(tmp_path / "documents.pkl", "wb") as f:
        pickle.dump([("d1", {}), ("phantom", {})], f)
    # Recalcular checksum para que pase la validación de integridad
    import hashlib

    digest = hashlib.sha256((tmp_path / "documents.pkl").read_bytes()).hexdigest()
    (tmp_path / "documents.pkl.sha256").write_text(digest, encoding="ascii")

    with pytest.raises(ValueError, match="desincronizado|ntotal"):
        FAISSIndexer.load(tmp_path)


# ═══════════ rebuild condicional ═══════════


@pytest.mark.asyncio
async def test_self_healing_skips_rebuild_when_clean(tmp_path, monkeypatch):
    from core.memory.manager import MemoryManager

    mm = MemoryManager.__new__(MemoryManager)
    mm.base_dir = tmp_path
    mm.active_workspace = "ws"
    mm.documents = [("ok_doc", "contenido suficientemente bueno y largo")]
    mm.index = faiss.IndexIDMap2(faiss.IndexFlatL2(FAISS_DIMENSION))
    mm._ids = {}
    mm._id_to_key = {}
    mm._next_id = 0
    mm._access_log = {}
    mm.embedder = type("E", (), {})()
    mm.embedder.wait_until_ready = lambda timeout=60: True
    mm.embedder.encode = lambda t, kind="passage": np.ones(FAISS_DIMENSION, dtype="float32")

    rebuilds = {"n": 0}

    async def counting_rebuild():
        rebuilds["n"] += 1

    async def perfect_critique(key, value, hint=None):
        return {"quality_score": 95, "is_valid": True, "suggested_fix": ""}

    async def zero(*a, **k):
        return 0

    # HIGIENE: monkeypatch (asignación directa en el singleton filtraba a
    # todos los tests posteriores de memoria — causa raíz del cluster flaky).
    monkeypatch.setattr(mm, "_llm_critique", perfect_critique)
    monkeypatch.setattr(mm, "_detect_duplicates", zero)
    monkeypatch.setattr(mm, "_resolve_contradictions", zero)
    monkeypatch.setattr(mm, "_prune_stale", zero)
    monkeypatch.setattr(mm, "_rebuild_index", counting_rebuild)

    await mm.self_healing_check()
    assert rebuilds["n"] == 0, "rebuild incondicional pese a workspace limpio"


async def _async_return(v):
    return v


class _TinyBatch(_FakeEmbedder):
    """Embedder dim-4 para el test de batch (add/search coherentes)."""

    def __init__(self):
        pass

    def encode(self, text, kind="passage"):
        return np.ones(4, dtype="float32")

    @staticmethod
    def encode_static():
        return np.ones(4, dtype="float32")


# ═══════════ stamp index.meta.json + política ═══════════


def _mini_index(tmp_path, dim=8):
    """Índice pequeño con 2 docs usando dim compacta."""

    class _Tiny(_FakeEmbedder):
        def encode(self, text, kind="passage"):
            return np.ones(dim, dtype="float32")

    ix = FAISSIndexer(dimension=dim, embedder=_Tiny())
    ix.add("k1", "texto uno")
    ix.add("k2", "texto dos")
    ix.save(tmp_path)
    return ix


def test_stamp_written_on_save_and_happy_load(tmp_path, monkeypatch):
    from core.config import settings

    _mini_index(tmp_path)
    meta_path = tmp_path / "index.meta.json"
    assert meta_path.exists(), "save no escribió index.meta.json"

    import json

    meta = json.loads(meta_path.read_text())
    assert meta["dim"] == 8
    assert meta["model"] == settings.embed_model
    assert len(meta["fingerprint"]) == 8
    assert "created" in meta

    ix = FAISSIndexer.load(tmp_path, dimension=8)  # mismo fp ⇒ carga feliz
    assert ix.document_count == 2


def test_mismatch_auto_rebuild_deletes_files_and_raises_file_not_found(tmp_path, monkeypatch):
    from core.config import settings

    _mini_index(tmp_path)
    monkeypatch.setattr(settings, "embed_model", "BAAI/bge-m3", raising=False)

    with pytest.raises(FileNotFoundError):
        FAISSIndexer.load(tmp_path, dimension=8)

    assert not (tmp_path / "faiss.index").exists()
    assert not (tmp_path / "documents.pkl").exists()
    assert not (tmp_path / "index.meta.json").exists()


def test_mismatch_strict_raises_valueerror_and_keeps_files(tmp_path, monkeypatch):
    from core.config import settings

    _mini_index(tmp_path)
    monkeypatch.setattr(settings, "embed_model", "BAAI/bge-m3", raising=False)

    with pytest.raises(ValueError):
        FAISSIndexer.load(tmp_path, dimension=8, policy="strict")

    assert (tmp_path / "faiss.index").exists()


def test_legacy_index_without_meta_is_accepted_with_warning(tmp_path):

    _mini_index(tmp_path)
    # simular índice legacy: quitar meta + su checksum
    (tmp_path / "index.meta.json").unlink()
    # reconstruir el índice sin stamp: re-save manual sería con meta; en su lugar
    # borramos meta y validamos que load acepte (warning) — los checksum de
    # faiss/documents siguen válidos.
    ix = FAISSIndexer.load(tmp_path, dimension=8)
    assert ix.document_count == 2


def test_add_embedding_precomputed_keeps_alignment():
    """add_embedding con vector pre-calculado mantiene índice↔docs."""

    class _Tiny4(_FakeEmbedder):
        def encode(self, text, kind="passage"):
            return np.ones(4, dtype="float32")

    ix = FAISSIndexer(dimension=4, embedder=_Tiny4())
    vec = np.ones(4, dtype="float32") * 0.5
    ix.add_embedding("k1", "doc uno", vec)
    assert ix.document_count == 1
    assert ix.index.ntotal == 1
    hits = ix.search("doc", k=1)
    assert hits and hits[0]["key"] == "k1"

    with pytest.raises(ValueError, match="dim"):
        ix.add_embedding("k2", "otro", np.ones(7, dtype="float32"))
    assert ix.document_count == 1, "vector rechazado no debe desalinear"


def test_codebase_indexing_uses_batch_encode(tmp_path, monkeypatch):
    """index_project encodea en un pase batch vía el embedder del indexer."""
    from core.codebase_indexer import CodebaseIndexer

    (tmp_path / "mod.py").write_text("def f():\n    return 1\n")
    counts = {"batch": 0, "single": 0}

    class _SpyEmbedder(_TinyBatch):
        @staticmethod
        def encode_batch(texts, kind="passage"):
            counts["batch"] += 1
            return [np.ones(4, dtype="float32") * 0.5 for _ in texts]

        def encode(self, text, kind="passage"):
            counts["single"] += 1
            return np.ones(4, dtype="float32") * 0.5

    cbi = CodebaseIndexer(workspace="ws_batch")
    monkeypatch.setattr(cbi, "_resolve_base", lambda: tmp_path)
    monkeypatch.setattr(cbi, "_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(cbi, "_load_cache", lambda: {})
    cbi.indexer = FAISSIndexer(dimension=4, embedder=_SpyEmbedder())

    n = cbi.index_project(patterns=[".py"], force=True)
    assert n >= 1
    assert counts["batch"] == 1, f"debe ser 1 pase batch: {counts}"
