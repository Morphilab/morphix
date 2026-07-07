# tests/test_lsp_manager.py
from pathlib import Path

import pytest


def _make_project_dir(tmp_path, files: dict[str, str]) -> Path:
    """Crea un directorio de proyecto temporal con archivos Python."""
    project = tmp_path / "test_project"
    project.mkdir(parents=True, exist_ok=True)
    for fname, content in files.items():
        fpath = project / fname
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content)
    return project


@pytest.fixture
def simple_project(tmp_path):
    return _make_project_dir(
        tmp_path,
        {
            "main.py": (
                "def saludar():\n"
                '    """Saluda al usuario."""\n'
                '    return "Hola"\n'
                "\n"
                "def despedir():\n"
                '    return "Adiós"\n'
                "\n"
                "resultado = saludar()\n"
            ),
            "utils.py": "def helper(x):\n    return x * 2\n",
        },
    )


class TestLspManagerDefinition:
    @pytest.mark.asyncio
    async def test_definition_found(self, simple_project):
        from tools.lsp_manager import lsp_manager_tool

        result = await lsp_manager_tool(
            action="definition",
            file="main.py",
            line=4,
            character=10,
            project_root=str(simple_project),
            workspace="main",
        )
        assert "despedir" in result.lower() or "saludar" in result.lower() or "📍" in result

    @pytest.mark.asyncio
    async def test_definition_not_found(self, simple_project):
        from tools.lsp_manager import lsp_manager_tool

        result = await lsp_manager_tool(
            action="definition",
            file="main.py",
            line=0,
            character=0,
            project_root=str(simple_project),
            workspace="main",
        )
        assert isinstance(result, str)


class TestLspManagerHover:
    @pytest.mark.asyncio
    async def test_hover_returns_info(self, simple_project):
        from tools.lsp_manager import lsp_manager_tool

        result = await lsp_manager_tool(
            action="hover",
            file="main.py",
            line=0,
            character=5,
            project_root=str(simple_project),
            workspace="main",
        )
        assert isinstance(result, str)


class TestLspManagerDiagnostics:
    @pytest.mark.asyncio
    async def test_diagnostics_no_errors(self, simple_project):
        from tools.lsp_manager import lsp_manager_tool

        result = await lsp_manager_tool(
            action="diagnostics",
            project_root=str(simple_project),
            workspace="main",
        )
        assert isinstance(result, str)
        # Puede reportar "problemas", "símbolos", o "detectaron"
        assert any(
            word in result.lower() for word in ["problema", "símbolo", "detectaron", "archivo"]
        )

    @pytest.mark.asyncio
    async def test_diagnostics_specific_file(self, simple_project):
        from tools.lsp_manager import lsp_manager_tool

        result = await lsp_manager_tool(
            action="diagnostics",
            file="main.py",
            project_root=str(simple_project),
            workspace="main",
        )
        assert isinstance(result, str)


class TestLspManagerReferences:
    @pytest.mark.asyncio
    async def test_references_returns_data(self, simple_project):
        from tools.lsp_manager import lsp_manager_tool

        result = await lsp_manager_tool(
            action="references",
            file="main.py",
            line=0,
            character=5,
            project_root=str(simple_project),
            workspace="main",
        )
        assert isinstance(result, str)
        # Puede encontrar o no referencias, pero no debe dar error
        assert "Error" not in result or "no soportada" in result.lower()

    @pytest.mark.asyncio
    async def test_invalid_action(self, simple_project):
        from tools.lsp_manager import lsp_manager_tool

        result = await lsp_manager_tool(
            action="invalid_action",
            project_root=str(simple_project),
            workspace="main",
        )
        assert "no soportada" in result.lower() or "Acción" in result

    @pytest.mark.asyncio
    async def test_project_not_found(self):
        from tools.lsp_manager import lsp_manager_tool

        result = await lsp_manager_tool(
            action="definition",
            project_root="proyecto_inexistente",
            workspace="main",
        )
        assert "no existe" in result.lower()


class TestLspManagerRuffCheck:
    """Tests for ruff_check action — previously 0% coverage."""

    @pytest.mark.asyncio
    async def test_ruff_check_clean_project(self, simple_project):
        from tools.lsp_manager import lsp_manager_tool

        result = await lsp_manager_tool(
            action="ruff_check",
            project_root=str(simple_project),
            workspace="main",
        )
        assert isinstance(result, str)
        assert "ningún problema" in result.lower()

    @pytest.mark.asyncio
    async def test_ruff_check_with_issues(self, tmp_path):
        project = _make_project_dir(
            tmp_path,
            {
                "bad.py": ("import os\nimport sys\nx=1\n"),
            },
        )
        from tools.lsp_manager import lsp_manager_tool

        result = await lsp_manager_tool(
            action="ruff_check",
            project_root=str(project),
            workspace="main",
        )
        assert isinstance(result, str)
        assert "ruff" in result.lower() or "encontró" in result.lower()

    @pytest.mark.asyncio
    async def test_ruff_check_specific_file(self, tmp_path):
        project = _make_project_dir(
            tmp_path,
            {
                "clean.py": "def foo():\n    return 42\n",
                "dirty.py": "import os\nimport sys\nx=1\n",
            },
        )
        from tools.lsp_manager import lsp_manager_tool

        result = await lsp_manager_tool(
            action="ruff_check",
            file="dirty.py",
            project_root=str(project),
            workspace="main",
        )
        assert isinstance(result, str)
        assert "ruff" in result.lower() or "dirty" in result.lower()

    @pytest.mark.asyncio
    async def test_ruff_check_null_and_non_dict_entries_do_not_crash(self, tmp_path):
        """Regression: ruff output [null, 42, 'str'] must not crash."""
        project = _make_project_dir(
            tmp_path,
            {"main.py": "def foo():\n    return 42\n"},
        )
        from tools import lsp_manager as lm

        original_loads = lm._json.loads

        def mock_loads(s):
            return [
                None,
                42,
                "string",
                {
                    "filename": "main.py",
                    "location": {"row": 1, "column": 1},
                    "code": "F401",
                    "message": "unused",
                    "fix": {},
                },
            ]

        lm._json.loads = mock_loads
        try:
            result = await lm.lsp_manager_tool(
                action="ruff_check",
                project_root=str(project),
                workspace="main",
            )
            assert isinstance(result, str)
            assert "Error" not in result
        finally:
            lm._json.loads = original_loads

    @pytest.mark.asyncio
    async def test_ruff_check_fix_flag(self, tmp_path):
        project = _make_project_dir(
            tmp_path,
            {"bad.py": "import os\nimport sys\nx=1\n"},
        )
        from tools.lsp_manager import lsp_manager_tool

        result = await lsp_manager_tool(
            action="ruff_check",
            file="bad.py",
            fix=True,
            project_root=str(project),
            workspace="main",
        )
        assert isinstance(result, str)
