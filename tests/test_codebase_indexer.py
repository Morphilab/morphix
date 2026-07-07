# tests/test_codebase_indexer.py
from unittest.mock import patch

import pytest


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
