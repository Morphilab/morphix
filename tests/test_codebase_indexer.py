# tests/test_codebase_indexer.py
from unittest.mock import patch

import numpy as np
import pytest

from core.faiss_indexer import FAISS_DIMENSION


class FakeFastEmbedder:
    """Embedder determinista — evita cargar e5 real (~11s/test)."""

    def wait_until_ready(self, timeout: float = 60) -> bool:
        return True

    def get_instance(self):
        return self

    def encode(self, text, kind="passage"):
        return np.ones(FAISS_DIMENSION, dtype="float32")


@pytest.fixture(autouse=True)
def _fast_embedder(monkeypatch):
    """Provider en estado LISTO con modelo fake."""
    import threading

    from core.embedding_provider import EmbeddingProvider

    ready = threading.Event()
    ready.set()
    monkeypatch.setattr(EmbeddingProvider, "_model", FakeFastEmbedder())
    monkeypatch.setattr(EmbeddingProvider, "_ready", ready)
    monkeypatch.setattr(EmbeddingProvider, "_loading", False)


@pytest.fixture
def project_dir(tmp_path):
    """Directorio de proyecto temporal con archivos Python."""
    proj = tmp_path / "test_proj"
    proj.mkdir(parents=True)
    (proj / "main.py").write_text("def hello():\n    return 'world'\n")
    (proj / "utils.py").write_text("def add(a, b):\n    return a + b\n")
    (proj / "nested").mkdir()
    (proj / "nested" / "deep.py").write_text("class Foo:\n    pass\n")
    return proj


class TestCodebaseIndexer:
    def test_index_project_finds_files(self, project_dir):
        """Indexa archivos Python del proyecto."""
        with patch("core.codebase_indexer.paths.memory_dir", return_value=project_dir):
            from core.codebase_indexer import CodebaseIndexer

            indexer = CodebaseIndexer(workspace="main")
            count = indexer.index_project()
            assert count > 0

    def test_search_returns_results(self, project_dir):
        """Búsqueda semántica tras indexar."""
        with patch("core.codebase_indexer.paths.memory_dir", return_value=project_dir):
            from core.codebase_indexer import CodebaseIndexer

            indexer = CodebaseIndexer(workspace="main")
            indexer.index_project()
            results = indexer.search("hello world")
            assert isinstance(results, list)

    def test_find_relevant_code_returns_string(self, project_dir):
        """find_relevant_code retorna string formateado."""
        with patch("core.codebase_indexer.paths.memory_dir", return_value=project_dir):
            from core.codebase_indexer import CodebaseIndexer

            indexer = CodebaseIndexer(workspace="main")
            indexer.index_project()
            code = indexer.find_relevant_code("def hello", max_results=2)
            assert isinstance(code, str)

    def test_empty_project(self, tmp_path):
        """Proyecto sin archivos no rompe nada."""
        empty = tmp_path / "empty_proj"
        empty.mkdir()
        with patch("core.codebase_indexer.paths.memory_dir", return_value=empty):
            from core.codebase_indexer import CodebaseIndexer

            indexer = CodebaseIndexer(workspace="main")
            count = indexer.index_project()
            assert count == 0
            results = indexer.search("anything")
            assert results == []


class TestIndexadoIncremental:
    def test_archivo_nuevo_se_indexa_sin_force(self, _fast_embedder, project_dir):
        """Sin el early-return del cache, el diff por hash re-encodea solo
        lo nuevo: un archivo creado DESPUÉS del primer indexado se incorpora
        en la siguiente pasada (antes quedaba invisible hasta borrar cache)."""
        import json

        from core.codebase_indexer import CodebaseIndexer

        (project_dir / "uno.py").write_text("def uno():\n    return 1\n")
        idx = CodebaseIndexer(project_root=str(project_dir), workspace="main")
        idx._indexer = _fast_embedder
        primeros = idx.index_project()
        assert primeros > 0

        (project_dir / "dos.py").write_text("def dos():\n    return 2\n")
        nuevos = idx.index_project()
        assert nuevos > 0
        assert nuevos < primeros  # solo el archivo nuevo, no todo el árbol

        # el cache de hashes registra el archivo nuevo
        assert "dos.py" in json.loads((idx._cache_dir() / "codebase_index.json").read_text())
