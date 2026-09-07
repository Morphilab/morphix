# tests/test_embedding_backends.py — backends alternativos de embeddings
"""Backends ollama/openai para embeddings con selección auto y fallback a
local. Protocolo mínimo: encode batch-first, dimension,
fingerprint, available()."""

import numpy as np
import pytest

import core.embedding_provider as ep
from core.embedding_provider import EmbeddingProvider

# ── OllamaEmbedBackend ────────────────────────────────────────────────────


def test_ollama_backend_parses_api_embed(monkeypatch):
    from core.embedding_provider import OllamaEmbedBackend

    b = OllamaEmbedBackend("nomic-embed-text")
    assert b.dimension == 768
    assert b.fingerprint == "ollama:nomic-embed-text:norm=True"

    captured = {}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"embeddings": [[0.3, 0.4], [0.1, 0.2]]}

    def fake_post(url, json=None, **kw):
        captured["url"] = url
        captured["json"] = json
        return FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)
    out = b.encode_texts(["hola", "mundo"], kind="passage")
    assert isinstance(out, np.ndarray) and out.shape == (2, 2)
    assert "/api/embed" in captured["url"]
    assert captured["json"]["model"] == "nomic-embed-text"
    assert captured["json"]["input"] == ["hola", "mundo"]
    # normalización por perfil nomic: filas de norma 1
    assert np.allclose(np.linalg.norm(out, axis=1), 1.0)


def test_ollama_backend_available_probe(monkeypatch):
    import httpx

    from core.embedding_provider import OllamaEmbedBackend

    monkeypatch.setattr(ep, "_OLLAMA_PROBE_CACHE", {})
    monkeypatch.setattr(httpx, "get", lambda *a, **k: type("R", (), {"status_code": 200})())
    assert OllamaEmbedBackend("m").available() is True

    ep._OLLAMA_PROBE_CACHE.clear()  # expira el probe exitoso

    def boom(*a, **k):
        raise OSError("sin servidor")

    monkeypatch.setattr(httpx, "get", boom)
    assert OllamaEmbedBackend("m").available() is False


# ── OpenAIEmbedBackend ────────────────────────────────────────────────────


def test_openai_backend_requires_key(monkeypatch):
    from core.config import settings
    from core.embedding_provider import OpenAIEmbedBackend

    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)
    b = OpenAIEmbedBackend("text-embedding-3-small")
    assert b.available() is False


def test_openai_backend_parses_response(monkeypatch):
    from core.config import settings
    from core.embedding_provider import OpenAIEmbedBackend

    monkeypatch.setattr(settings, "openai_api_key", "sk-test", raising=False)
    b = OpenAIEmbedBackend("text-embedding-3-small")

    captured = {}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "data": [
                    {"index": 0, "embedding": [0.6, 0.8]},
                    {"index": 1, "embedding": [1.0, 0.0]},
                ]
            }

    def fake_post(url, json=None, headers=None, **kw):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)
    out = b.encode_texts(["a", "b"], kind="passage")
    assert out.shape == (2, 2)
    assert "api.openai.com" in captured["url"]
    assert captured["headers"]["Authorization"].startswith("Bearer ")
    assert captured["json"]["input"] == ["a", "b"]


# ── Selección auto + fallback ─────────────────────────────────────────────


def test_auto_offline_resolves_local(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "embed_backend", "auto", raising=False)
    from llm.offline import OfflineManager

    monkeypatch.setattr(OfflineManager, "is_offline", lambda self: True, raising=True)
    assert EmbeddingProvider._resolve_backend() == ("local", None)


def test_auto_online_without_keys_falls_to_ollama_then_local(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "embed_backend", "auto", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)

    from llm.offline import OfflineManager

    monkeypatch.setattr(OfflineManager, "is_offline", lambda self: False, raising=True)

    # Ollama disponible → ollama
    monkeypatch.setattr(ep.OllamaEmbedBackend, "available", lambda self: True)
    kind, _ = EmbeddingProvider._resolve_backend()
    assert kind == "ollama"

    # Ollama caído → local ST
    monkeypatch.setattr(ep.OllamaEmbedBackend, "available", lambda self: False)
    kind, _ = EmbeddingProvider._resolve_backend()
    assert kind == "local"


@pytest.mark.asyncio
async def test_encode_batch_routes_to_ollama_and_falls_back(monkeypatch):
    """embed_backend='ollama': usa el backend remoto; ante error HTTP cae a
    la ruta local sin romper al llamador."""
    from core.config import settings

    calls = {"remote": 0, "local": 0}

    class RemoteOK:
        @staticmethod
        def encode_texts(texts, kind="passage"):
            calls["remote"] += 1
            return np.ones((len(texts), 4), dtype="float32") / 2

    monkeypatch.setattr(settings, "embed_backend", "ollama", raising=False)
    monkeypatch.setattr(EmbeddingProvider, "_resolve_backend", lambda: ("ollama", RemoteOK))

    out = await __import__("asyncio").to_thread(EmbeddingProvider.encode_batch, ["a", "b"])
    assert out[0].shape == (4,)
    assert calls["remote"] == 1 and calls["local"] == 0

    class RemoteBroken:
        @staticmethod
        def encode_texts(texts, kind="passage"):
            raise ConnectionError("server down")

    monkeypatch.setattr(EmbeddingProvider, "_resolve_backend", lambda: ("ollama", RemoteBroken))

    def fake_local_batch(texts, kind="passage"):
        calls["local"] += 1
        return [np.ones(4, dtype="float32") / 2 for _ in texts]

    monkeypatch.setattr(
        EmbeddingProvider,
        "_encode_batch_local",
        staticmethod(fake_local_batch),
        raising=True,
    )
    out2 = await __import__("asyncio").to_thread(EmbeddingProvider.encode_batch, ["c"])
    assert out2[0].shape == (4,)
    assert calls["local"] == 1


def test_fingerprint_follows_selected_backend(monkeypatch):
    from core.config import settings

    fp_local = EmbeddingProvider.fingerprint()
    # local explícito → mismo stamp que default
    monkeypatch.setattr(settings, "embed_backend", "local", raising=False)
    assert EmbeddingProvider.fingerprint() == fp_local

    class FakeRemote:
        fingerprint = "ollama:nomic-embed-text:norm=True"
        dimension = 768

        @staticmethod
        def available():
            return True

    monkeypatch.setattr(settings, "embed_backend", "ollama", raising=False)
    monkeypatch.setattr(EmbeddingProvider, "_resolve_backend", lambda: ("ollama", FakeRemote))
    assert EmbeddingProvider.fingerprint() != fp_local
    assert EmbeddingProvider.fingerprint() == "ollama:nomic-embed-text:norm=True"
