"""Codebase Indexer — indexación semántica con FAISS + cache en disco."""

import hashlib
import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path

from core.context_manager import ContextManager
from core.faiss_indexer import FAISSIndexer
from core.path_resolver import paths

logger = logging.getLogger(__name__)

CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".rs",
    ".go",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".rb",
    ".php",
    ".swift",
    ".kt",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".md",
    ".rst",
    ".txt",
    ".sql",
    ".sh",
    ".bash",
    ".cfg",
    ".ini",
    ".env.example",
}

MAX_FILE_SIZE = 500_000  # 500KB
MAX_FILES = 200
CACHE_FILE = "codebase_index.json"

# Aborto cooperativo del hilo de indexado: un to_thread no se puede cancelar,
# así que el shutdown de la app marca el evento y el loop lo consulta entre
# lotes (el checkpoint del lote en curso ya está persistido).
_SHUTDOWN = threading.Event()

# Single-flight por (workspace, project_root): con time-box en la vía
# automática el indexado sigue en background — un retry o un preload
# concurrente NO deben duplicar el trabajo ni pisar el checkpoint.
_IN_FLIGHT: set[tuple[str, str]] = set()
_IN_FLIGHT_LOCK = threading.Lock()


def request_indexing_shutdown() -> None:
    """Marca los indexados en curso para abortar en el próximo límite de lote."""
    _SHUTDOWN.set()


def reset_indexing_shutdown() -> None:
    """Solo para tests: rearma el flag tras un aborto simulado."""
    _SHUTDOWN.clear()


class CodebaseIndexer:
    """Indexa un codebase con FAISS para búsqueda semántica de código relevante."""

    def __init__(self, project_root: str | None = None, workspace: str | None = None):
        if workspace is None:
            from core.config import settings

            workspace = settings.active_workspace
        self.workspace = workspace
        self.project_root = project_root
        self._index_built = False
        self._file_hashes: dict[str, str] = {}

        cache_dir = self._cache_dir()
        try:
            self.indexer = FAISSIndexer.load(cache_dir)
            self._index_built = self.indexer.index.ntotal > 0
            logger.info(
                f"Loaded cached FAISS index from {cache_dir} ({self.indexer.document_count} docs)"
            )
        except FileNotFoundError:
            self._purgar_hash_cache_huerfano("sin índice FAISS (purge del stamp o primera corrida)")
            self.indexer = FAISSIndexer()
        except ValueError as e:
            logger.warning("Caché FAISS inválida (%s) — reindexando desde cero", e)
            self._purgar_hash_cache_huerfano("índice FAISS inválido")
            self.indexer = FAISSIndexer()

    def _purgar_hash_cache_huerfano(self, motivo: str) -> None:
        """Sin índice FAISS válido, el hash-cache NO significa nada.

        Sus hashes referencian vectores que ya no existen (el purge del
        stamp por cambio de backend borra los artefactos FAISS pero no puede
        conocer este archivo, que es del CodebaseIndexer). Si sobrevive, el
        reindex "incremental" se salta archivos y el índice nuevo queda
        incompleto — huérfanos con hash pero sin vectores.
        """
        hash_path = self._cache_dir() / CACHE_FILE
        if hash_path.exists():
            try:
                hash_path.unlink()
                logger.info(
                    "Hash-cache huérfano purgado (%s) — reindex completo: %s",
                    motivo,
                    hash_path,
                )
            except OSError:
                logger.warning("No se pudo purgar el hash-cache huérfano: %s", hash_path)

    def _resolve_base(self) -> Path:
        base = paths.memory_dir(self.workspace)
        if self.project_root:
            base = base / self.project_root
        return base

    def _cache_dir(self) -> Path:
        cache_dir = paths.memory_dir(self.workspace) / ".codebase_cache"
        if self.project_root:
            project_name = Path(self.project_root).name
            cache_dir = cache_dir / project_name
        return cache_dir

    def _load_cache(self) -> dict[str, str]:
        cache_path = self._cache_dir() / CACHE_FILE
        if cache_path.exists():
            try:
                return json.loads(cache_path.read_text())
            except Exception:
                logger.debug("Error leyendo caché en _load_cache", exc_info=True)
        return {}

    def _save_cache(self) -> None:
        cache_path = self._cache_dir() / CACHE_FILE
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            cache_path.write_text(json.dumps(self._file_hashes, indent=2))
        except Exception:
            logger.debug("Error guardando caché de codebase", exc_info=True)

    def _hash_file(self, file_path: Path) -> str:
        """Hash rápido basado en mtime + tamaño."""
        stat = file_path.stat()
        return hashlib.md5(f"{stat.st_mtime}:{stat.st_size}".encode()).hexdigest()[:12]

    def index_project(
        self,
        patterns: list[str] | None = None,
        max_files: int = MAX_FILES,
        force: bool = False,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> int:
        """Indexa archivos incrementalmente (solo archivos modificados desde último index).

        Args:
            patterns: Extensiones a indexar (None = CODE_EXTENSIONS).
            max_files: Máximo de archivos a indexar.
            force: Si True, reindexa todo ignorando cache.
            progress_callback: Callable(dict) para reportar progreso.
                Fases: "indexing" (per-5-archivos), "done", "aborted".

        Returns:
            Número de chunks indexados en esta ejecución.

        Persistencia por LOTE (checkpoint): hashes + índice se salvan tras
        cada lote de ~embed_batch_size chunks — un retry/abort retoma donde
        estaba en vez de perder todo lo invertido — un intento abortado
        conserva el checkpoint del último lote persistido.
        """
        base = self._resolve_base()
        if not base.exists():
            logger.debug("Directorio de proyecto no encontrado: %s", base)
            return 0

        key = (self.workspace, str(self.project_root))
        with _IN_FLIGHT_LOCK:
            if key in _IN_FLIGHT:
                logger.info(
                    "Indexado de %s ya corre en background — skip (el dueño persiste)",
                    key,
                )
                return 0
            _IN_FLIGHT.add(key)
        try:
            return self._index_project_impl(base, patterns, max_files, force, progress_callback)
        finally:
            with _IN_FLIGHT_LOCK:
                _IN_FLIGHT.discard(key)

    def _index_project_impl(
        self,
        base: Path,
        patterns: list[str] | None,
        max_files: int,
        force: bool,
        progress_callback: Callable[[dict], None] | None,
    ) -> int:
        from core.config import settings

        # Los hashes se cargan SIEMPRE: sin ellos ni la detección de
        # borrados opera ni el diff incremental puede decidir qué
        # re-encodear. El early-return por cache existente congelaba el
        # índice (archivos nuevos invisibles hasta borrarlo a mano).
        self._file_hashes = self._load_cache()
        if force:
            self._file_hashes = {}

        # detectar DELETIONS — archivos indexados que ya no existen.
        # Por lotes: remove_prefixes realinea UNA vez (antes era un
        # rebuild completo por archivo borrado).
        deleted_rels = [rel for rel in list(self._file_hashes) if not (base / rel).exists()]
        if deleted_rels:
            for rel in deleted_rels:
                self._file_hashes.pop(rel, None)
            removed = self.indexer.remove_prefixes([rel + ":" for rel in deleted_rels])
            logger.info("Limpieza: %d archivos borrados purgados del índice", removed)

        total_chunks = 0
        files_processed = 0
        # archivos COMPLETOS (skipped por hash o con chunks persistidos):
        # el % de progreso mide esto — el scan es instantáneo, el ENCODE es
        # el costo real, y una barra que mide archivos escaneados "termina"
        # en segundos y queda congelada mientras encodean los lotes.
        files_done = 0

        extensions = patterns if patterns else CODE_EXTENSIONS

        # Rough total estimate for progress
        total_estimate = min(max_files, sum(1 for _ in base.rglob("*") if _.is_file()))

        batch_size = int(getattr(settings, "embed_batch_size", 32) or 32)

        pending_chunks: list[tuple[str, object]] = []
        pending_hashes: dict[str, str] = {}

        def _progress(data: dict) -> None:
            if progress_callback is None:
                return
            try:
                progress_callback(data)
            except Exception:
                logger.warning("Failed to report indexer progress", exc_info=True)

        def _pct() -> int:
            return min(99, int(files_done / max(1, total_estimate) * 100))

        def _flush() -> None:
            """Encodea el lote, lo añade al índice y PERSISTE el checkpoint.

            El hash de un archivo se marca SOLO si TODOS sus chunks entraron
            al índice — marcar con embeddings fallidos (modelo frío, dim
            incompatible) crea huérfanos (hash sin vectores) que el
            incremental jamás re-encodea.
            """
            nonlocal total_chunks, files_done
            if not pending_chunks:
                return
            embeddings = self.indexer.encode_many([str(v) for _k, v in pending_chunks])
            added_batch = 0
            failed_files: set[str] = set()
            for (key, value), emb in zip(pending_chunks, embeddings, strict=False):
                rel = value.get("file") if isinstance(value, dict) else None
                if emb is None:
                    if rel:
                        failed_files.add(rel)
                    continue
                try:
                    self.indexer.add_embedding(key=key, value=value, embedding=emb)
                    added_batch += 1
                except ValueError:
                    logger.warning("Chunk descartado por dim incompatible: %s", key)
                    if rel:
                        failed_files.add(rel)
            total_chunks += added_batch
            for rel, h in pending_hashes.items():
                if rel in failed_files:
                    logger.warning("Hash NO marcado (chunks sin vector): %s — se re-encodeará", rel)
                else:
                    self._file_hashes[rel] = h
                    files_done += 1
            self._index_built = self.indexer.index.ntotal > 0
            self._save_cache()
            if self._index_built:
                try:
                    self.indexer.save(self._cache_dir())
                except Exception as e:
                    logger.warning(f"Failed to save FAISS index: {e}")
            last_file = next(iter(reversed(pending_hashes)), "")
            pending_chunks.clear()
            pending_hashes.clear()
            _progress(
                {
                    "phase": "indexing",
                    "current_file": last_file,
                    "files_scanned": files_processed,
                    "files_done": files_done,
                    "total_chunks": total_chunks,
                    "pct": _pct(),
                }
            )

        for ext in extensions:
            pattern = f"**/*{ext}" if ext.startswith(".") else f"**/*.{ext}"
            for f in base.glob(pattern):
                if files_processed >= max_files:
                    break
                if not f.is_file():
                    continue
                if any(
                    p in f.parts
                    for p in (".git", "__pycache__", "node_modules", ".venv", ".undo", ".redo")
                ):
                    continue
                if f.stat().st_size > MAX_FILE_SIZE:
                    continue

                rel_path = str(f.relative_to(base))
                current_hash = self._hash_file(f)

                if not force and self._file_hashes.get(rel_path) == current_hash:
                    files_processed += 1
                    files_done += 1
                    continue

                try:
                    content = f.read_text(encoding="utf-8", errors="replace")
                except Exception as e:
                    logger.debug("Error leyendo %s: %s", rel_path, e)
                    files_processed += 1
                    continue

                chunks = ContextManager.chunk_large_file(content, rel_path)
                for chunk in chunks:
                    pending_chunks.append(
                        (
                            f"{chunk['file']}:L{chunk['start_line']}",
                            chunk,
                        )
                    )

                self.indexer.remove_prefix(
                    rel_path + ":"
                )  # barato: los embeddings ya almacenados evitan re-encodear
                pending_hashes[rel_path] = current_hash
                files_processed += 1

                # Progress reporting
                if files_processed % 5 == 0:
                    _progress(
                        {
                            "phase": "indexing",
                            "current_file": rel_path,
                            "files_scanned": files_processed,
                            "files_done": files_done,
                            "total_chunks": total_chunks,
                            "pct": _pct(),
                        }
                    )

                if len(pending_chunks) >= batch_size:
                    if _SHUTDOWN.is_set():
                        break
                    _flush()

            if files_processed >= max_files or _SHUTDOWN.is_set():
                break

        if _SHUTDOWN.is_set():
            # El lote EN VUELLO se descarta: sus hashes NUNCA se marcaron y el
            # próximo run los re-encodea (pérdida: un lote de trabajo barato).
            # Flushearlo aquí mantenía el proceso vivo UN LOTE MÁS tras el
            # cierre de la app — minutos con un backend local lento.
            descartados = len(pending_chunks)
            logger.warning(
                "Indexado abortado por shutdown — checkpoint persistido "
                "(%d archivos completos); %d chunks del lote en vuelo "
                "descartados (se re-encodearán en el próximo run)",
                files_done,
                descartados,
            )
            _progress(
                {
                    "phase": "aborted",
                    "files_scanned": files_processed,
                    "files_done": files_done,
                    "total_chunks": total_chunks,
                    "pct": 100,
                }
            )
            return total_chunks

        _flush()

        self._index_built = self.indexer.index.ntotal > 0
        logger.info(f"Codebase indexado: {files_processed} archivos, {total_chunks} chunks nuevos")
        _progress(
            {
                "phase": "done",
                "files_scanned": files_processed,
                "files_done": files_done,
                "total_chunks": total_chunks,
                "pct": 100,
            }
        )
        return total_chunks

    def search(self, query: str, k: int = 10) -> list[dict]:
        if not self._index_built:
            return []
        return self.indexer.search(query, k=k)

    def find_relevant_code(self, task: str, max_results: int = 5) -> str:
        results = self.search(task, k=max_results)
        if not results:
            return ""
        parts = []
        for r in results:
            key = r["key"]
            chunk = r["value"]
            if isinstance(chunk, dict):
                file_name = chunk.get("file", key)
                content = chunk.get("content", str(chunk))[:1000]
                start = chunk.get("start_line", "?")
                parts.append(f"// {file_name}:{start}\n{content}")
            else:
                parts.append(f"// {key}\n{str(chunk)[:1000]}")
        return "\n\n".join(parts)
