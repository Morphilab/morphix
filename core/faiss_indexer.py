"""FAISS Indexer — indexación semántica reutilizable con FAISS + SentenceTransformer."""

import contextlib
import datetime
import hashlib
import json
import logging
import pickle
import threading
from pathlib import Path
from typing import Any

import faiss

from core.embedding_provider import EmbeddingProvider

logger = logging.getLogger(__name__)

FAISS_DIMENSION = 1024

_CHECKSUM_SUFFIX = ".sha256"
_META_NAME = "index.meta.json"


def _write_checksum(path: Path) -> None:
    """Escribe el sha256 del archivo en <path>.sha256."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (Path(str(path) + _CHECKSUM_SUFFIX)).write_text(digest, encoding="ascii")


def _verify_checksum(path: Path) -> None:
    """Verifica el sha256 de path contra <path>.sha256; ValueError si no coincide."""
    checksum_path = Path(str(path) + _CHECKSUM_SUFFIX)
    if not checksum_path.exists():
        raise ValueError(f"Checksum faltante para {path.name} — archivo no íntegro")
    expected = checksum_path.read_text(encoding="ascii").strip()
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"Checksum inválido para {path.name} — archivo corrupto/manipulado")


class FAISSIndexer:
    """Indexación FAISS reutilizable: add, search, save, load, rebuild."""

    def __init__(self, dimension: int = FAISS_DIMENSION, embedder=None):
        self._lock = threading.RLock()
        self.index = faiss.IndexFlatL2(dimension)
        self.documents: list[tuple[str, object]] = []
        self.embedder = embedder or EmbeddingProvider
        if not embedder:
            self.embedder.get_instance()

    def _encode(self, text: str, kind: str = "passage"):
        if self.embedder.wait_until_ready(timeout=60):
            encode = getattr(self.embedder, "encode", None)
            if encode is None:
                return None
            try:
                return encode(text, kind=kind)
            except TypeError:
                # embedders legacy sin parámetro kind
                return encode(text)
        logger.warning("Modelo embeddings no disponible")
        return None

    def encode_many(self, texts: list[str]) -> list:
        """Encodeo en lote usando el embedder propio del indexer.
        Usa embedder.encode_batch si existe; si no, cae a _encode uno-a-uno."""
        batch_fn = getattr(self.embedder, "encode_batch", None)
        if callable(batch_fn):
            try:
                out = batch_fn(list(texts))
                if out is not None and len(out) == len(texts):
                    return list(out)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "encode_batch del embedder falló — fallback uno-a-uno", exc_info=True
                )
        return [self._encode(x) for x in texts]

    def add(self, key: str, value: object) -> None:
        """Añade un documento al índice."""
        embedding = self._encode(str(value))
        if embedding is None:
            return
        self.add_embedding(key, value, embedding)

    def add_embedding(self, key: str, value: object, embedding) -> None:
        """Añade con embedding PRE-CALCULADO (p.ej. desde una caché) —
        evita re-encodear lo que ya se pagó. Mantiene alineación índice↔docs."""
        import numpy as np

        arr = np.asarray(embedding, dtype="float32").reshape(1, -1)
        with self._lock:
            if arr.shape[1] != self.index.d:
                raise ValueError(
                    f"Embedding dim {arr.shape[1]} ≠ dimensión del índice ({self.index.d})"
                )
            self.index.add(arr)
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
        """Elimina un documento del índice y realinea vectores."""
        with self._lock:
            if any(k == key for k, _ in self.documents):
                self.documents = [(k, v) for k, v in self.documents if k != key]
                removed_now = True
            else:
                removed_now = False
        if removed_now:
            self.rebuild_index()

    def remove_prefix(self, prefix: str) -> int:
        """Elimina TODOS los documentos cuyo key empiece por prefix.

        'utils.py:' remueve utils.py:L1, utils.py:L2, ... Realinea el índice.
        Retorna el número de documentos eliminados.
        """
        with self._lock:
            remaining = [(k, v) for k, v in self.documents if not k.startswith(prefix)]
            removed = len(self.documents) - len(remaining)
            if removed:
                self.documents = remaining
        if removed:
            self.rebuild_index()
        return removed

    def rebuild_index(self) -> None:
        """Reconstrucción ALINEADA: lo que falle embedding se excluye de AMBAS
        estructuras para preservar ntotal == len(documents)."""
        with self._lock:
            doc_snapshot = list(self.documents)
        aligned: list[tuple[tuple[str, object], Any]] = []
        for pair in doc_snapshot:
            emb = self._encode(str(pair[1]))
            if emb is not None:
                aligned.append((pair, emb))
        with self._lock:
            self.index = faiss.IndexFlatL2(self.index.d)
            for _pair, emb in aligned:
                self.index.add(emb.reshape(1, -1))
            self.documents = [pair for pair, _ in aligned]

    def clear(self) -> None:
        """Limpia documentos e índice."""
        with self._lock:
            self.documents = []
            self.index = faiss.IndexFlatL2(self.index.d)

    def save(self, directory: Path) -> None:
        """Persistencia ATÓMICA: temp + os.replace para índice y pickle,
        checksum en ambos. Un crash a mitad no corrompe la persistencia previa."""
        import os
        import tempfile

        directory.mkdir(parents=True, exist_ok=True)
        with self._lock:
            index_path = directory / "faiss.index"
            docs_path = directory / "documents.pkl"

            # 1. Pickle primero (fuente de verdad de keys/values)
            fd_docs, tmp_docs = tempfile.mkstemp(dir=str(directory), suffix=".tmp")
            with os.fdopen(fd_docs, "wb") as f:
                pickle.dump(self.documents, f)
            os.replace(tmp_docs, docs_path)
            _write_checksum(docs_path)

            # 2. Índice al final (los vectores referencian posiciones de documents)
            fd_idx, tmp_idx = tempfile.mkstemp(dir=str(directory), suffix=".tmp")
            os.close(fd_idx)
            faiss.write_index(self.index, tmp_idx)
            os.replace(tmp_idx, index_path)
            _write_checksum(index_path)

            # stamp de compatibilidad — cambiar de modelo/norm invalida
            # el índice y la política decide reconstruir o fallar ruidoso.
            from core.config import settings

            meta = {
                "backend": settings.embed_backend,
                "model": settings.embed_model,
                "fingerprint": EmbeddingProvider.fingerprint(),
                "dim": int(self.index.d),
                "normalized": bool(settings.embed_normalize),
                "created": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
            }
            meta_path = directory / _META_NAME
            fd_meta, tmp_meta = tempfile.mkstemp(dir=str(directory), suffix=".tmp")
            with os.fdopen(fd_meta, "w") as f:
                json.dump(meta, f)
            os.replace(tmp_meta, meta_path)
        logger.info(f"FAISS index saved to {directory} ({self.index.ntotal} vectors)")

    @classmethod
    def load(
        cls, directory: Path, dimension: int = FAISS_DIMENSION, policy: str | None = None
    ) -> "FAISSIndexer":
        """Carga con validación completa: checksums, dimensión, stamp de
        huella (con política auto_rebuild|strict) y sincronía ntotal ↔ len(documents)."""
        from core.config import settings

        index_path = directory / "faiss.index"
        docs_path = directory / "documents.pkl"
        if not index_path.exists() or not docs_path.exists():
            raise FileNotFoundError(f"No cached FAISS index at {directory}")
        _verify_checksum(docs_path)
        _verify_checksum(index_path)
        instance = cls(dimension=dimension)
        instance.index = faiss.read_index(str(index_path))
        with open(docs_path, "rb") as f:
            instance.documents = pickle.load(f)

        # validar stamp contra el backend efectivo actual.
        meta_path = directory / _META_NAME
        effective_policy = policy or getattr(settings, "embed_index_policy", "auto_rebuild")
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
            except Exception:
                meta = None
                logger.warning("index.meta.json ilegible — tratado como legacy", exc_info=True)
            if isinstance(meta, dict):
                fp_now = EmbeddingProvider.fingerprint()
                if meta.get("fingerprint") != fp_now or int(meta.get("dim", -1)) != int(
                    instance.index.d
                ):
                    msg = (
                        f"Stamp mismatch: índice fue creado con "
                        f"{meta.get('model')} (fp={meta.get('fingerprint')}, dim={meta.get('dim')}) "
                        f"y el backend efectivo es fp={fp_now}"
                    )
                    if effective_policy == "strict":
                        raise ValueError(f"{msg} — política strict: falla ruidosa")
                    # auto_rebuild: limpiar TODOS los artefactos y señalar
                    # reconstrucción vía FileNotFoundError (los consumidores ya
                    # tratan ese caso creando un indexer vacío).
                    logger.warning(f"{msg} — auto_rebuild: borrando caché para reindexar")
                    for artifact in (
                        "faiss.index",
                        "documents.pkl",
                        _META_NAME,
                        "faiss.index.sha256",
                        "documents.pkl.sha256",
                    ):
                        with contextlib.suppress(FileNotFoundError):
                            (directory / artifact).unlink()
                    raise FileNotFoundError(f"{msg} — caché purgada, reindexe")

                if meta.get("dim") != int(instance.index.d):
                    raise ValueError(
                        f"Dimensión del índice ({instance.index.d}) ≠ esperada ({dimension})"
                    )
        else:
            logger.warning(
                "Índice sin index.meta.json (legacy pre-F3.1) — aceptado; "
                "se regenerará con stamp en el próximo save"
            )

        if instance.index.d != dimension:
            raise ValueError(f"Dimensión del índice ({instance.index.d}) ≠ esperada ({dimension})")
        if instance.index.ntotal != len(instance.documents):
            raise ValueError(
                f"Índice desincronizado: ntotal={instance.index.ntotal} "
                f"vs documents={len(instance.documents)} — regenere el índice"
            )
        logger.info(f"FAISS index loaded from {directory} ({instance.index.ntotal} vectors)")
        return instance

    @property
    def document_count(self) -> int:
        return len(self.documents)
