# tests/test_faiss_indexer.py
"""FAISSIndexer: persistencia con integridad (checksum del pickle)."""

import hashlib
import pickle

import pytest


@pytest.fixture
def indexer():
    from core.faiss_indexer import FAISSIndexer

    idx = FAISSIndexer(dimension=8)
    idx.embedder = type(
        "FakeEmbedder",
        (),
        {
            "wait_until_ready": staticmethod(lambda timeout: True),
            "encode": staticmethod(lambda text: __import__("numpy").zeros(8, dtype="float32")),
        },
    )
    return idx


def test_save_writes_checksum(indexer, tmp_path):
    indexer.add("k1", "valor 1")
    indexer.save(tmp_path)

    pkl = tmp_path / "documents.pkl"
    sha = tmp_path / "documents.pkl.sha256"
    assert pkl.exists()
    assert sha.exists()

    expected = hashlib.sha256(pkl.read_bytes()).hexdigest()
    assert sha.read_text().strip() == expected


def test_load_roundtrip_verifies_checksum(indexer, tmp_path):
    indexer.add("k1", "valor 1")
    indexer.save(tmp_path)

    from core.faiss_indexer import FAISSIndexer

    loaded = FAISSIndexer.load(tmp_path, dimension=8)
    assert loaded.document_count == 1
    assert loaded.documents[0][0] == "k1"


def test_load_rejects_tampered_pickle(indexer, tmp_path):
    indexer.add("k1", "valor 1")
    indexer.save(tmp_path)

    docs = tmp_path / "documents.pkl"
    data = bytearray(docs.read_bytes())
    data[0] ^= 0xFF  # corromper el primer byte
    docs.write_bytes(bytes(data))

    from core.faiss_indexer import FAISSIndexer

    with pytest.raises(ValueError):
        FAISSIndexer.load(tmp_path, dimension=8)


def test_load_rejects_missing_checksum(tmp_path):
    from core.faiss_indexer import FAISSIndexer

    index = FAISSIndexer(dimension=8)
    faiss_dir = tmp_path
    import faiss

    faiss.write_index(index.index, str(faiss_dir / "faiss.index"))
    docs = [("k", "v")]
    with open(faiss_dir / "documents.pkl", "wb") as f:
        pickle.dump(docs, f)

    with pytest.raises(ValueError):
        FAISSIndexer.load(tmp_path, dimension=8)
