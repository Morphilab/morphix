# tests/test_pkb.py
"""Tests de la Project Knowledge Base (PKB) — reutilización-first.

- paths.workspace_knowledge_dir
- tool project_docs (read-only): list/read/search/inject + guard de path traversal
- MemoryManager._knowledge_entries: docs kb_ protegidos
- onboarding: knowledge/onboarding/agent-guide.md → bootstrap <SKILLS>
"""

from pathlib import Path

import pytest


@pytest.fixture
def kb_dir(tmp_path, monkeypatch):
    """Redirige workspaces/<ws>/knowledge a tmp y crea contenido."""

    def _make(ws: str) -> Path:
        kdir = tmp_path / ws / "knowledge"
        (kdir / "conventions").mkdir(parents=True)
        (kdir / "onboarding").mkdir()
        (kdir / "decisions").mkdir()
        (kdir / "conventions" / "python-style.md").write_text(
            "# Estilo Python\nUsar 4 espacios y typing estricto.", encoding="utf-8"
        )
        (kdir / "onboarding" / "agent-guide.md").write_text(
            "GUIA DEL PROYECTO: respeta el esquema de BD.", encoding="utf-8"
        )
        (kdir / "decisions" / "2026-08-31-choice-orm.md").write_text(
            "# ORM\nDecidimos SQLModel por contrato tipado.", encoding="utf-8"
        )
        return kdir

    from core.path_resolver import PathResolver

    monkeypatch.setattr(
        PathResolver,
        "workspace_knowledge_dir",
        staticmethod(lambda ws: tmp_path / ws / "knowledge"),
    )
    return _make


class TestPaths:
    def test_knowledge_dir_helper(self):
        from core.path_resolver import paths

        p = paths.workspace_knowledge_dir("miws")
        assert p.name == "knowledge"
        assert p.parent.name == "miws"


class TestTool:
    @pytest.fixture
    def handler(self, kb_dir, monkeypatch):
        kb_dir("main")
        import tools.project_docs as tpd
        from tools.registry import ToolsRegistry

        monkeypatch.setattr(tpd, "_current_workspace", lambda: "main")
        reg = ToolsRegistry()
        tpd.register(reg)
        return reg.get_tool("project_docs")

    @pytest.mark.asyncio
    async def test_list_categorias(self, handler):
        result = await handler(action="list")
        assert result["success"] is True
        names = {c["category"] for c in result["categories"]}
        assert names == {"conventions", "onboarding", "decisions"}
        conv = next(c for c in result["categories"] if c["category"] == "conventions")
        assert any(d["name"] == "python-style" for d in conv["docs"])

    @pytest.mark.asyncio
    async def test_read(self, handler):
        result = await handler(action="read", category="conventions", name="python-style")
        assert result["success"] is True
        assert "typing estricto" in result["content"]

    @pytest.mark.asyncio
    async def test_read_traversal_rechazado(self, handler):
        result = await handler(
            action="read", category="conventions", name="../onboarding/agent-guide"
        )
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_read_categoria_inexistente(self, handler):
        result = await handler(action="read", category="api", name="x")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_search_por_palabras(self, handler):
        result = await handler(action="search", query="sqlmodel contrato")
        assert result["success"] is True
        assert any(r["name"] == "2026-08-31-choice-orm" for r in result["results"])

    @pytest.mark.asyncio
    async def test_inject_devuelve_contenido_top_k(self, handler):
        result = await handler(action="inject", query="estilo python", k=1)
        assert result["success"] is True
        assert len(result["docs"]) == 1
        assert "typing estricto" in result["docs"][0]["content"]

    @pytest.mark.asyncio
    async def test_accion_invalida(self, handler):
        result = await handler(action="write")
        assert result["success"] is False


class TestKnowledgeEntries:
    def test_claves_kb_sanitizadas(self, kb_dir):
        from core.memory.manager import _knowledge_entries

        kb_dir("ws1")
        entries = dict(_knowledge_entries("ws1"))
        assert "kb_conventions_python_style" in entries
        assert "kb_decisions_2026_08_31_choice_orm" in entries
        assert "typing estricto" in entries["kb_conventions_python_style"]

    def test_sin_directorio_vacio(self, tmp_path, monkeypatch):
        from core.memory.manager import _knowledge_entries
        from core.path_resolver import PathResolver

        monkeypatch.setattr(
            PathResolver,
            "workspace_knowledge_dir",
            staticmethod(lambda ws: tmp_path / ws / "knowledge"),
        )
        assert _knowledge_entries("vacio") == []

    def test_prefijo_kb_protegido(self):
        from core.memory.manager import MemoryManager

        assert "kb_" in MemoryManager._PROTECTED_PREFIXES


class TestOnboardingBootstrap:
    def test_agent_guide_se_inyecta(self, kb_dir):
        from core import skills as skills_mod

        kb_dir("main")
        # build_bootstrap lee paths.workspace_knowledge_dir("main") — el
        # fixture ya redirigió el resolver y creó el árbol con agent-guide.md
        bootstrap = skills_mod.build_bootstrap("main")
        assert "GUIA DEL PROYECTO" in bootstrap
        assert "esquema de BD" in bootstrap
