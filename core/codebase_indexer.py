"""Codebase Indexer — indexación semántica con FAISS + cache en disco."""

import hashlib
import json
import logging
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
            self.indexer = FAISSIndexer()

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

        Returns:
            Número de chunks indexados en esta ejecución.
        """
        base = self._resolve_base()
        if not base.exists():
            logger.debug("Directorio de proyecto no encontrado: %s", base)
            return 0

        # Skip if cache already loaded and not forced
        if not force and self._index_built and self.indexer.document_count > 0:
            logger.info("Using cached FAISS index, skipping re-index")
            return 0

        self._file_hashes = self._load_cache()
        total_chunks = 0
        files_processed = 0

        extensions = patterns if patterns else CODE_EXTENSIONS

        # Rough total estimate for progress
        total_estimate = min(max_files, sum(1 for _ in base.rglob("*") if _.is_file()))

        # Batch indexing: accumulate all chunks first, rebuild once at the end
        pending_chunks: list[tuple[str, object]] = []

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
                    continue

                try:
                    content = f.read_text(encoding="utf-8", errors="replace")
                except Exception:
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
                    total_chunks += 1

                self.indexer.remove(rel_path)
                self._file_hashes[rel_path] = current_hash
                files_processed += 1

                # Progress reporting
                if progress_callback and files_processed % 5 == 0:
                    try:
                        progress_callback(
                            {
                                "phase": "indexing",
                                "current_file": rel_path,
                                "files_scanned": files_processed,
                                "total_chunks": total_chunks,
                                "pct": min(99, int(files_processed / max(1, total_estimate) * 100)),
                            }
                        )
                    except Exception:
                        logger.warning("Failed to report indexer progress", exc_info=True)

        # Single rebuild with all new chunks at once
        if pending_chunks:
            self.indexer.rebuild_index()
            for key, value in pending_chunks:
                self.indexer.add(key=key, value=value)

        self._index_built = self.indexer.index.ntotal > 0
        self._save_cache()

        # Persist FAISS index to disk
        if self._index_built:
            try:
                self.indexer.save(self._cache_dir())
            except Exception as e:
                logger.warning(f"Failed to save FAISS index: {e}")

        logger.info(f"Codebase indexado: {files_processed} archivos, {total_chunks} chunks nuevos")
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
