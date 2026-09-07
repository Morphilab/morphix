"""Reproducciones de bugs críticos de memoria/secretos.

Cada test protege un contrato específico contra su regresión:
- poda por access-log vacío tras reinicio (debe usar mtime).
- overwrite desincroniza índice FAISS ↔ documents.
- EmbeddingProvider sin reintentos ni fail-fast.
- .env incluido en exports.
- PDF sin escape XML crashea con '<'/'&'.
- get_messages ordena solo por timestamp (sin tiebreaker id).
- hechos consolidados merged_* invisibles para search.

Convención repo: mocks inline, sin fixtures compartidas.
"""

import os
import threading
import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import faiss
import numpy as np
import pytest

from core.faiss_indexer import FAISS_DIMENSION
from core.memory.manager import MemoryManager
from core.repositories.conversation_repository import (
    ConversationRepository,
    _collect_project_files,
)

_T = datetime(2026, 8, 21, 12, 0, 0)


def _fresh_manager(tmp_path) -> MemoryManager:
    """Instancia aislada de MemoryManager apuntando a tmp_path (sin singleton)."""
    mm = MemoryManager.__new__(MemoryManager)
    mm.base_dir = tmp_path
    mm.active_workspace = "ws1"
    mm.documents = []
    mm.index = faiss.IndexIDMap2(faiss.IndexFlatL2(FAISS_DIMENSION))
    mm._ids = {}
    mm._id_to_key = {}
    mm._next_id = 0
    mm._access_log = {}
    mm.embedder = MagicMock()
    mm.embedder.wait_until_ready.return_value = True
    mm.embedder.encode = lambda t, kind="passage": np.ones(FAISS_DIMENSION, dtype="float32")
    return mm


def _mock_db_session() -> MagicMock:
    session = MagicMock()
    session.get = AsyncMock()
    session.add = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.rollback = AsyncMock()
    session.commit = AsyncMock()
    return session


# ==================== poda por mtime ====================


@pytest.mark.asyncio
async def test_B1_disk_loaded_doc_survives_self_healing(tmp_path):
    """Documentos cargados de disco sin access-log NO deben podarse."""
    mm = _fresh_manager(tmp_path)
    ws = tmp_path / "ws1"
    ws.mkdir(exist_ok=True)
    f = ws / "nota_vieja.md"
    f.write_text("contenido relevante de hace un mes", encoding="utf-8")
    recent_t = time.time() - 2 * 86400
    os.utime(f, (recent_t, recent_t))
    mm.documents = [("nota_vieja", "contenido relevante de hace un mes")]
    removed = await mm._prune_stale(max_age_days=30)
    assert removed == 0
    assert any(k == "nota_vieja" for k, _ in mm.documents)
    assert f.exists()


@pytest.mark.asyncio
async def test_prune_uses_file_mtime_when_access_log_empty(tmp_path):
    """La poda debe usar mtime del archivo cuando no hay registro de acceso."""
    mm = _fresh_manager(tmp_path)
    ws = tmp_path / "ws1"
    ws.mkdir(exist_ok=True)
    old_t = time.time() - 40 * 86400
    young_t = time.time() - 5 * 86400
    (ws / "viejo.md").write_text("documento viejo", encoding="utf-8")
    (ws / "nuevo.md").write_text("documento nuevo", encoding="utf-8")
    os.utime(ws / "viejo.md", (old_t, old_t))
    os.utime(ws / "nuevo.md", (young_t, young_t))
    mm.documents = [("viejo", "documento viejo"), ("nuevo", "documento nuevo")]
    removed = await mm._prune_stale(max_age_days=30)
    assert removed == 1
    assert [k for k, _ in mm.documents] == ["nuevo"]
    assert (ws / "nuevo.md").exists()
    assert not (ws / "viejo.md").exists()


# ==================== consistencia índice↔documents ====================


@pytest.mark.asyncio
async def test_B2_overwrite_keeps_search_consistent(tmp_path):
    """Tras overwrite, los vectores huérfanos no deben contaminar la búsqueda.

    Escenario: doc_a(alpha) + doc_b(beta); overwrite de doc_a(gamma) añade vector
    sin purgar el viejo → desalineación posicional; doc_d(delta) hereda el vector
    huérfano y la búsqueda por 'gamma' devuelve el documento EQUIVOCADO.
    """
    mm = _fresh_manager(tmp_path)
    vecs = {
        "alpha": np.ones(FAISS_DIMENSION, dtype="float32"),
        "beta": np.full(FAISS_DIMENSION, -1.0, dtype="float32"),
        "gamma": np.zeros(FAISS_DIMENSION, dtype="float32"),
        "delta": np.full(FAISS_DIMENSION, 0.5, dtype="float32"),
    }

    def fake_encode(text, kind="passage"):
        return vecs.get(str(text).split()[0], np.ones(FAISS_DIMENSION, dtype="float32"))

    mm.embedder.encode = fake_encode
    assert await mm.write("doc_a", "alpha primero", validated=True)
    assert await mm.write("doc_b", "beta segundo", validated=True)
    assert await mm.write("doc_a", "gamma reescrito", validated=True)  # overwrite
    assert await mm.write("doc_d", "delta cuarto", validated=True)

    results = mm.search("gamma reescrito", k=4)
    keys = [r["key"] for r in results]
    assert "doc_a" in keys, f"doc_a (contenido gamma) no aparece para query gamma: {keys}"
    top = results[0]["key"]
    assert top == "doc_a", f"contaminación posicional: query gamma devolvió {top!r}"
    vals = {r["key"]: r["value"] for r in results}
    assert vals.get("doc_a") == "gamma reescrito", f"valor stale: {vals}"
    assert len(keys) == len(set(keys)), "duplicados en resultados"


@pytest.mark.asyncio
async def test_duplicate_merge_deletes_correct_file(tmp_path):
    """El hecho consolidado debe persistir con clave estable y ser buscable."""
    mm = _fresh_manager(tmp_path)
    base = np.ones(FAISS_DIMENSION, dtype="float32")
    delta = 0.3 / np.sqrt(FAISS_DIMENSION)

    def encode_close(text, kind="passage"):
        return base + (delta if str(text).endswith("v2") else 0.0)

    mm.embedder.encode = encode_close

    assert await mm.write("hecho_a", "el usuario prefiere python para scripts", validated=True)
    assert await mm.write("hecho_b", "el usuario prefiere python para scripts v2", validated=True)

    with patch.object(
        MemoryManager,
        "_arbitrate_contradiction",
        new=AsyncMock(return_value="el usuario prefiere python (consolidado)"),
    ):
        resolved = await mm._resolve_contradictions()

    assert resolved == 1
    ws = tmp_path / "ws1"
    assert not (ws / "hecho_a.md").exists()
    assert not (ws / "hecho_b.md").exists()
    fact_files = [f for f in ws.glob("*.md") if f.stem.startswith("fact_")]
    assert fact_files, "no hay hecho consolidado fact_* en disco"
    results = mm.search("python consolidado", k=5)
    keys = [r["key"] for r in results]
    assert any(
        k.startswith("fact_") for k in keys
    ), f"hecho consolidado invisible para search: {keys}"


# ==================== resiliencia EmbeddingProvider ====================


def _fresh_provider(monkeypatch):
    """EmbeddingProvider en estado conocido, sin carga real de modelo."""
    from core.embedding_provider import EmbeddingProvider

    monkeypatch.setattr(EmbeddingProvider, "_model", None, raising=False)
    monkeypatch.setattr(EmbeddingProvider, "_loading", False, raising=False)
    monkeypatch.setattr(EmbeddingProvider, "_load_attempts", 0, raising=False)
    monkeypatch.setattr(EmbeddingProvider, "load_error", None, raising=False)
    monkeypatch.setattr(EmbeddingProvider, "_ready", threading.Event())
    return EmbeddingProvider


def test_B3_provider_fail_fast_after_exhaustion(monkeypatch):
    """Campaña de carga agotada → wait_until_ready NO debe esperar el timeout completo."""
    EP = _fresh_provider(monkeypatch)
    monkeypatch.setattr(EP, "_load_attempts", 99)
    monkeypatch.setattr(EP, "load_error", "boom")
    monkeypatch.setattr(EP, "_load_model", lambda: None)

    t0 = time.monotonic()
    ok = EP.wait_until_ready(timeout=6.0)
    elapsed = time.monotonic() - t0
    assert ok is False
    assert elapsed < 5, f"esperó {elapsed:.1f}s pese a fallo agotado"


def test_B3_retry_succeeds_after_transient_failure(monkeypatch):
    """Fallo transitorio en el constructor → campaña reintenta hasta éxito."""
    import sys

    import core.embedding_provider as mod

    EP = _fresh_provider(monkeypatch)
    monkeypatch.setattr(mod, "_MAX_LOAD_ATTEMPTS", 3, raising=False)
    monkeypatch.setattr(mod, "_LOAD_BACKOFF_SECONDS", 0.01, raising=False)

    calls = {"n": 0}

    class FakeST:
        def encode(self, text):
            return np.ones(4, dtype="float32")

    def flaky_constructor(name):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("red caída")
        return FakeST()

    monkeypatch.setitem(
        sys.modules, "sentence_transformers", MagicMock(SentenceTransformer=flaky_constructor)
    )

    ok = EP.wait_until_ready(timeout=10.0)
    assert ok is True
    assert calls["n"] >= 3, f"solo {calls['n']} intento(s), no hubo reintento"


# ==================== secretos fuera de exports ====================


def test_collect_project_files_excludes_env(tmp_path):
    """.env* nunca se exporta."""
    (tmp_path / ".env").write_text("API_KEY=supersecreto", encoding="utf-8")
    (tmp_path / ".env.example").write_text("API_KEY=placeholder", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('hola')", encoding="utf-8")
    text = _collect_project_files(tmp_path)
    assert "app.py" in text
    assert "supersecreto" not in text
    assert "API_KEY" not in text


@pytest.mark.asyncio
async def test_pdf_export_escapes_xml(tmp_path):
    """PDF con '<'/'&' en título o contenido no debe crashear."""
    conv = MagicMock(title="A < B & C", created_at=_T)
    rows = [
        MagicMock(role="user", content="usa <tag> & 'quotes'", timestamp=_T),
        MagicMock(role="assistant", content="ok <b>negrita sin cerrar y & ampersand", timestamp=_T),
    ]
    session = _mock_db_session()
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = rows
    session.get = AsyncMock(return_value=conv)
    session.execute = AsyncMock(return_value=result_mock)

    with (
        patch("core.repositories.conversation_repository.get_async_session") as mock_ctx,
        patch("core.path_resolver.paths") as mock_paths,
    ):
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_paths.exports_dir.return_value = tmp_path
        out = await ConversationRepository.export(conv_id=1, format="pdf")

    assert isinstance(out, str), f"export pdf crasheó o retornó {out!r}"
    assert out.endswith(".pdf")


# ==================== orden estable por id ====================


@pytest.mark.asyncio
async def test_B5_messages_ordered_by_insertion_id():
    """get_messages ordena por (timestamp, id) — empates resueltos por inserción."""
    stmts = []

    async def capture_execute(stmt):
        stmts.append(stmt)
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [
            MagicMock(role="user", content="primero", timestamp=_T, id=10),
            MagicMock(role="assistant", content="segundo", timestamp=_T, id=11),
            MagicMock(role="user", content="tercero", timestamp=_T, id=12),
        ]
        return result_mock

    session = _mock_db_session()
    session.execute = capture_execute

    with patch("core.repositories.conversation_repository.get_async_session") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        msgs = await ConversationRepository.get_messages(1)

    assert [m["content"] for m in msgs] == ["primero", "segundo", "tercero"]
    compiled = str(stmts[0].compile(compile_kwargs={"literal_binds": True})).lower()
    order_part = compiled.split("order by")[-1]
    assert "message.id" in order_part, f"ORDER BY sin tiebreaker id: {order_part!r}"
