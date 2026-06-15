"""FAISS Indexer — indexación semántica reutilizable con FAISS + SentenceTransformer."""

import logging
import pickle
import threading
from pathlib import Path

import faiss

from core.embedding_provider import EmbeddingProvider

logger = logging.getLogger(__name__)

FAISS_DIMENSION = 1024


class FAISSIndexer:
    """Indexación FAISS reutilizable: add, search, save, load, rebuild."""

    def __init__(self, dimension: int = FAISS_DIMENSION, embedder=None):
        self._lock = threading.RLock()
        self.index = faiss.IndexFlatL2(dimension)
        self.documents: list[tuple[str, object]] = []
        self.embedder = embedder or EmbeddingProvider
        if not embedder:
            self.embedder.get_instance()

    def _encode(self, text: str):
        if self.embedder.wait_until_ready(timeout=60):
            return self.embedder.encode(text)
        logger.warning("Modelo embeddings no disponible")
        return None

    def add(self, key: str, value: object) -> None:
        """Añade un documento al índice."""
        embedding = self._encode(str(value))
        if embedding is None:
            return
        with self._lock:
            self.index.add(embedding.reshape(1, -1))
            self.documents.append((key, value))

    def search(self, query: str, k: int = 5) -> list[dict]:
        """Búsqueda semántica. Retorna [{key, value, distance, similarity}]."""
        query_emb = self._encode(query)
        if query_emb is None:
            return []
        query_emb = query_emb.reshape(1, -1)
        with self._lock:
            if self.index.ntotal == 0:
                return []
            distances, indices = self.index.search(query_emb, min(k, self.index.ntotal))
        results = []
        with self._lock:
            for dist, idx in zip(distances[0], indices[0], strict=False):
                if idx >= 0 and idx < len(self.documents):
                    key, value = self.documents[idx]
                    similarity = 1.0 / (1.0 + float(dist))
                    results.append(
                        {
                            "key": key,
                            "value": value,
                            "distance": float(dist),
                            "similarity": float(similarity),
                        }
                    )
        return results

    def remove(self, key: str) -> None:
        """Elimina un documento del índice (requiere rebuild)."""
        with self._lock:
            self.documents = [(k, v) for k, v in self.documents if k != key]

    def rebuild_index(self) -> None:
        """Reconstruye el índice desde cero basado en documents."""
        with self._lock:
            doc_snapshot = list(self.documents)
        precomputed = []
        for _, value in doc_snapshot:
            emb = self._encode(str(value))
            if emb is not None:
                precomputed.append(emb)
        with self._lock:
            self.index = faiss.IndexFlatL2(self.index.d)
            for emb in precomputed:
                self.index.add(emb.reshape(1, -1))

    def clear(self) -> None:
        """Limpia documentos e índice."""
        with self._lock:
            self.documents = []
            self.index = faiss.IndexFlatL2(self.index.d)

    def save(self, directory: Path) -> None:
        """Persiste el índice FAISS y documentos a disco."""
        directory.mkdir(parents=True, exist_ok=True)
        with self._lock:
            faiss.write_index(self.index, str(directory / "faiss.index"))
            with open(directory / "documents.pkl", "wb") as f:
                pickle.dump(self.documents, f)
        logger.info(f"FAISS index saved to {directory} ({self.index.ntotal} vectors)")

    @classmethod
    def load(cls, directory: Path, dimension: int = FAISS_DIMENSION) -> "FAISSIndexer":
        """Carga un índice FAISS desde disco."""
        index_path = directory / "faiss.index"
        docs_path = directory / "documents.pkl"
        if not index_path.exists() or not docs_path.exists():
            raise FileNotFoundError(f"No cached FAISS index at {directory}")
        instance = cls(dimension=dimension)
        instance.index = faiss.read_index(str(index_path))
        with open(docs_path, "rb") as f:
            instance.documents = pickle.load(f)
        logger.info(f"FAISS index loaded from {directory} ({instance.index.ntotal} vectors)")
        return instance

    @property
    def document_count(self) -> int:
        return len(self.documents)
