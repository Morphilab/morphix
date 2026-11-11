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

    def __init__(self, dimension: int | None = None, embedder=None):
        self._lock = threading.RLock()
        if dimension is None:
            dimension = self._resolve_dimension(embedder)
        self.index = faiss.IndexFlatL2(dimension)
        self.documents: list[tuple[str, object]] = []
        # Vectores alineados con documents (por posición). Mantiene el rebuild
        # y el remove_prefix BARATOS: reconstruir el índice deja de re-encodear
        # O(n) documentos: un update incremental con LRU frío re-encodeaba
        # el índice completo. Se puebla en load()
        # vía index.reconstruct — nada nuevo en disco.
        self._embeddings: list = []
        self.embedder = embedder or EmbeddingProvider
        if not embedder and self.embedder.needs_local_model():
            # Con backend remoto el modelo local es solo fallback: cargarlo
            # aquí quemaría GB de RAM que el backend no necesita.
            self.embedder.get_instance()

    @staticmethod
    def _resolve_dimension(embedder=None) -> int:
        """Dimensión derivada del backend de embeddings efectivo.

        El índice persistido históricamente nacía a 1024 hardcode — con un
        backend de otra dimensión (nomic 768, nemotron 2048, 3-small 1536)
        TODOS los chunks se descartaban por dim incompatible. Ahora se
        pregunta al embedder; 0/desconocida ⇒ fallback al default clásico
        con warning loud (los chunks del backend serían rechazados — visible,
        no silencioso).
        """
        from core.embedding_provider import _dim_of

        candidates = [embedder, EmbeddingProvider]
        for cand in candidates:
            if cand is None:
                continue
            d = _dim_of(cand)
            if d > 0:
                return d
        logger.warning(
            "Dimensión de embeddings DESCONOCIDA — índice con default %d "
            "(los chunks de un backend de otra dim serán rechazados)",
            FAISS_DIMENSION,
        )
        return FAISS_DIMENSION

    def _encode(self, text: str, kind: str = "passage"):
        needs_ready = getattr(self.embedder, "needs_local_model", None)
        # Con backend remoto el gate de carga local MATA search/add (60s de
        # espera por un modelo que el backend no usa); con local sigue igual.
        if needs_ready is None or needs_ready():
            if not self.embedder.wait_until_ready(timeout=60):
                logger.warning("Modelo embeddings no disponible")
                return None
        encode = getattr(self.embedder, "encode", None)
        if encode is None:
            return None
        try:
            return encode(text, kind=kind)
        except TypeError:
            # embedders legacy sin parámetro kind
            return encode(text)

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
        evita re-encodear lo que ya se pagó. Mantiene alineación índice↔docs
        (y ↔ _embeddings, para que rebuild/remove no re-encodeen)."""
        import numpy as np

        arr = np.asarray(embedding, dtype="float32").reshape(1, -1)
        with self._lock:
            if arr.shape[1] != self.index.d:
                raise ValueError(
                    f"Embedding dim {arr.shape[1]} ≠ dimensión del índice ({self.index.d})"
                )
            self.index.add(arr)
            self.documents.append((key, value))
            self._embeddings.append(arr.reshape(-1).copy())

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
            if not any(k == key for k, _ in self.documents):
                return
            if len(self._embeddings) == len(self.documents):
                kept = [
                    (pair, emb)
                    for pair, emb in zip(self.documents, self._embeddings, strict=False)
                    if pair[0] != key
                ]
                self.documents = [pair for pair, _ in kept]
                self._embeddings = [emb for _pair, emb in kept]
            else:
                self.documents = [(k, v) for k, v in self.documents if k != key]
        self.rebuild_index()

    def remove_prefix(self, prefix: str) -> int:
        """Elimina TODOS los documentos cuyo key empiece por prefix.

        'utils.py:' remueve utils.py:L1, utils.py:L2, ... Realinea el índice
        (barato: desde los embeddings almacenados). Retorna eliminados.
        """
        return self.remove_prefixes([prefix])

    def remove_prefixes(self, prefixes: list[str]) -> int:
        """remove_prefix por lotes: UNA realineación para N prefijos.

        La detección de borrados del scan llamaba uno-a-uno y cada llamada
        reconstruía el índice; con embeddings almacenados el filtro es la
        parte cara y conviene hacerlo una vez.
        """
        if not prefixes:
            return 0
        with self._lock:
            if len(self._embeddings) == len(self.documents):
                kept = [
                    (pair, emb)
                    for pair, emb in zip(self.documents, self._embeddings, strict=False)
                    if not any(pair[0].startswith(p) for p in prefixes)
                ]
                removed = len(self.documents) - len(kept)
                if removed:
                    self.documents = [pair for pair, _ in kept]
                    self._embeddings = [emb for _pair, emb in kept]
            else:
                remaining = [
                    (k, v) for k, v in self.documents if not any(k.startswith(p) for p in prefixes)
                ]
                removed = len(self.documents) - len(remaining)
                if removed:
                    self.documents = remaining
        if removed:
            self.rebuild_index()
        return removed

    def rebuild_index(self) -> None:
        """Reconstrucción ALINEADA y BARATA: usa los embeddings ALMACENADOS
        (add_embedding / reconstruct en load) — solo re-encodea lo que no
        tenga vector (embedders legacy/fakes). El fallo de embedding excluye
        el documento de AMBAS estructuras para preservar
        ntotal == len(documents)."""
        import numpy as np

        with self._lock:
            doc_snapshot = list(self.documents)
            emb_snapshot = list(self._embeddings)
        aligned: list[tuple[tuple[str, object], Any]] = []
        have_stored = len(emb_snapshot) == len(doc_snapshot)
        for i, pair in enumerate(doc_snapshot):
            emb = emb_snapshot[i] if have_stored else None
            if emb is None:
                emb = self._encode(str(pair[1]))
                if emb is None:
                    continue
            arr = np.asarray(emb, dtype="float32").reshape(-1)
            if arr.shape[0] != self.index.d:
                logger.warning(
                    "Documento excluido del rebuild: dim %d ≠ índice %d (%s)",
                    arr.shape[0],
                    self.index.d,
                    pair[0],
                )
                continue
            aligned.append((pair, arr))
        with self._lock:
            self.index = faiss.IndexFlatL2(self.index.d)
            self.documents = []
            self._embeddings = []
            for pair, emb in aligned:
                self.index.add(emb.reshape(1, -1))
                self.documents.append(pair)
                self._embeddings.append(emb.copy())

    def clear(self) -> None:
        """Limpia documentos e índice."""
        with self._lock:
            self.documents = []
            self._embeddings = []
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

            # El campo model debe describir el backend EFECTIVO: con un
            # remoto seleccionado, settings.embed_model (el local) miente
            # sobre quién produjo los vectores. El fingerprint sigue siendo
            # el campo validado — esto es legibilidad del stamp.
            try:
                _kind, _remote = EmbeddingProvider._resolve_backend()
                if _remote is not None:
                    effective_model = str(getattr(_remote, "model", _kind))
                else:
                    effective_model = str(settings.embed_model)
            except (
                Exception
            ):  # noqa: BLE001 — settings rotos: el campo legible degrada, el stamp no
                effective_model = str(settings.embed_backend)
            meta = {
                "backend": settings.embed_backend,
                "model": effective_model,
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
        cls,
        directory: Path,
        dimension: int | None = None,
        policy: str | None = None,
        embedder=None,
    ) -> "FAISSIndexer":
        """Carga con validación completa: checksums, dimensión, stamp de
        huella (con política auto_rebuild|strict) y sincronía ntotal ↔ len(documents).

        `dimension=None` deriva del backend efectivo (ver _resolve_dimension):
        un cambio de modelo de embeddings con otra dim debe reindexar, no
        rechazar chunk a chunk. `embedder` opcional para tests/embedders
        custom (default: EmbeddingProvider)."""
        from core.config import settings

        index_path = directory / "faiss.index"
        docs_path = directory / "documents.pkl"
        if not index_path.exists() or not docs_path.exists():
            raise FileNotFoundError(f"No cached FAISS index at {directory}")
        _verify_checksum(docs_path)
        _verify_checksum(index_path)
        if dimension is None:
            dimension = cls._resolve_dimension(embedder)
        instance = cls(dimension=dimension, embedder=embedder)
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
                "Índice sin index.meta.json (legacy) — aceptado; "
                "se regenerará con stamp en el próximo save"
            )

        if instance.index.d != dimension:
            raise ValueError(f"Dimensión del índice ({instance.index.d}) ≠ esperada ({dimension})")
        if instance.index.ntotal != len(instance.documents):
            raise ValueError(
                f"Índice desincronizado: ntotal={instance.index.ntotal} "
                f"vs documents={len(instance.documents)} — regenere el índice"
            )
        # Poblar _embeddings desde el índice persistido (IndexFlatL2 guarda
        # los vectores crudos): habilita rebuild/remove baratos tras load.
        # Si reconstruct no está disponible (índice exótico), se queda vacío
        # y el rebuild cae al re-encode histórico.
        try:
            import numpy as np

            instance._embeddings = [
                np.asarray(instance.index.reconstruct(i), dtype="float32").reshape(-1)
                for i in range(instance.index.ntotal)
            ]
        except Exception:  # noqa: BLE001
            logger.debug("reconstruct no disponible — rebuild re-encodeará", exc_info=True)
            instance._embeddings = []
        logger.info(f"FAISS index loaded from {directory} ({instance.index.ntotal} vectors)")
        return instance

    @property
    def document_count(self) -> int:
        return len(self.documents)
