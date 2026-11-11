"""Tool memory_inspector — list/read/delete con guardas."""

import faiss
import numpy as np
import pytest

from core.faiss_indexer import FAISS_DIMENSION
from core.memory.manager import MemoryManager


@pytest.fixture
def isolated_memory(monkeypatch):
    """Singleton aislado apuntando a tmp, rebind del attr de módulo."""
    mm = MemoryManager.__new__(MemoryManager)
    mm.base_dir = None
    mm.active_workspace = "ws"
    mm.documents = []
    mm.index = faiss.IndexIDMap2(faiss.IndexFlatL2(FAISS_DIMENSION))
    mm._ids = {}
    mm._id_to_key = {}
    mm._next_id = 0
    mm._access_log = {}

    monkeypatch.setattr("core.memory.manager.memory", mm)
    return mm


@pytest.fixture
def handler(isolated_memory):
    from tools.memory_inspector import register
    from tools.registry import ToolsRegistry

    reg = ToolsRegistry()
    register(reg)
    return reg.get_tool("memory_inspector")


@pytest.mark.asyncio
async def test_list_returns_keys(handler, isolated_memory):
    isolated_memory.documents = [("doc_a", "contenido uno"), ("doc_b", "dos")]
    result = await handler(action="list")
    assert result["success"] is True
    assert result["count"] == 2
    assert {i["key"] for i in result["keys"]} == {"doc_a", "doc_b"}


@pytest.mark.asyncio
async def test_read_denies_security_private(handler):
    result = await handler(action="read", key="security_private")
    assert result["success"] is False


@pytest.mark.asyncio
async def test_read_returns_value(handler, isolated_memory):
    isolated_memory.documents = [("nota", {"dato": 1})]
    result = await handler(action="read", key="nota")
    assert result["success"] is True
    assert result["value"] == {"dato": 1}


@pytest.mark.asyncio
async def test_delete_requires_confirm(handler, isolated_memory):
    isolated_memory.base_dir = __import__("pathlib").Path("/tmp")
    result = await handler(action="delete", key="algo")
    assert result["success"] is False
    assert "confirm_delete" in result["error"]


@pytest.mark.asyncio
async def test_delete_denies_protected(handler, isolated_memory):
    isolated_memory.base_dir = __import__("pathlib").Path("/tmp")
    result = await handler(action="delete", key="user_profile", confirm_delete=True)
    assert result["success"] is False


@pytest.mark.asyncio
async def test_delete_removes_doc_index_and_file(handler, isolated_memory, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    isolated_memory.base_dir = tmp_path

    # Doc persistido vía write-like manual (doc + índice coherentes)
    vec = np.ones(FAISS_DIMENSION, dtype="float32")
    isolated_memory.index.add_with_ids(vec.reshape(1, -1), np.array([0], dtype="int64"))
    isolated_memory._ids["borrable"] = 0
    isolated_memory._id_to_key[0] = "borrable"
    isolated_memory.documents = [("borrable", "contenido")]
    (ws / "borrable.md").write_text("contenido", encoding="utf-8")

    result = await handler(action="delete", key="borrable", confirm_delete=True)

    assert result["success"] is True
    assert isolated_memory.documents == []
    assert isolated_memory.index.ntotal == 0
    assert not (ws / "borrable.md").exists()


@pytest.mark.asyncio
async def test_invalid_action(handler):
    result = await handler(action="hack")
    assert result["success"] is False
