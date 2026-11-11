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
    # La clave DEDICADA de embeddings habilita el backend por sí sola —
    # sin esto el test deja de ser hermético cuando el usuario la configura.
    monkeypatch.setattr(settings, "embed_openai_api_key", "", raising=False)
    b = OpenAIEmbedBackend("text-embedding-3-small")
    assert b.available() is False


def test_openai_backend_parses_response(monkeypatch):
    from core.config import settings
    from core.embedding_provider import OpenAIEmbedBackend

    monkeypatch.setattr(settings, "openai_api_key", "sk-test", raising=False)
    monkeypatch.setattr(settings, "embed_openai_api_key", "", raising=False)
    monkeypatch.setattr(
        settings, "embed_openai_base_url", "https://api.openai.com/v1", raising=False
    )
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


def test_auto_offline_prefiere_ollama_local_y_cae_a_local(monkeypatch):
    """auto + offline: ollama local (nomic) si responde; local si no —
    nunca un endpoint remoto con el modo activo."""
    import httpx

    from core.config import settings

    monkeypatch.setattr(settings, "embed_backend", "auto", raising=False)
    from llm.offline import OfflineManager

    monkeypatch.setattr(OfflineManager, "is_offline", lambda self: True, raising=True)
    monkeypatch.setattr(ep, "_OLLAMA_PROBE_CACHE", {})
    monkeypatch.setattr(httpx, "get", lambda *a, **k: type("R", (), {"status_code": 200})())
    kind, backend = EmbeddingProvider._resolve_backend()
    assert kind == "ollama" and backend is not None

    def boom(*a, **k):
        raise OSError("sin servidor")

    monkeypatch.setattr(httpx, "get", boom)
    ep._OLLAMA_PROBE_CACHE.clear()  # expira el probe exitoso de la primera mitad
    assert EmbeddingProvider._resolve_backend() == ("local", None)


def test_auto_online_without_keys_falls_to_ollama_then_local(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "embed_backend", "auto", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)
    monkeypatch.setattr(settings, "embed_openai_api_key", "", raising=False)

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


# ── OFFLINE_MODE gobierna también los embeddings ──────────────────────────


class TestOfflineConmutaAOllamaLocal:
    """Con el modo offline activo, un backend remoto resuelto se sustituye
    por ollama local (nomic-embed-text): ningún byte sale a un endpoint
    remoto con la UI en modo offline. El fingerprint cambia con la
    resolución, así que el stamp auto-rebuild purga y reconstruye el
    índice con la dim nueva al togglear."""

    @staticmethod
    def _offline(monkeypatch, valor: bool):
        import llm.offline as off
        from core.config import settings

        monkeypatch.setattr(settings, "offline_mode", valor, raising=False)
        monkeypatch.setattr(off.OfflineManager, "_state_offline", False)

    @staticmethod
    def _ollama_disponible(monkeypatch):
        import httpx

        monkeypatch.setattr(ep, "_OLLAMA_PROBE_CACHE", {})
        monkeypatch.setattr(httpx, "get", lambda *a, **k: type("R", (), {"status_code": 200})())

    @staticmethod
    def _ollama_caido(monkeypatch):
        import httpx

        monkeypatch.setattr(ep, "_OLLAMA_PROBE_CACHE", {})

        def boom(*a, **k):
            raise OSError("sin servidor")

        monkeypatch.setattr(httpx, "get", boom)

    def test_openai_en_offline_resuelve_ollama_local(self, monkeypatch):
        from core.config import settings

        monkeypatch.setattr(settings, "embed_backend", "openai", raising=False)
        self._ollama_disponible(monkeypatch)
        self._offline(monkeypatch, True)
        kind, backend = EmbeddingProvider._resolve_backend()
        assert kind == "ollama"
        assert backend.model == settings.embed_ollama_model

    def test_openai_online_no_se_toca(self, monkeypatch):
        from core.config import settings

        monkeypatch.setattr(settings, "embed_backend", "openai", raising=False)
        self._ollama_disponible(monkeypatch)
        self._offline(monkeypatch, False)
        kind, _backend = EmbeddingProvider._resolve_backend()
        assert kind == "openai"

    def test_local_explicito_no_se_toca_en_offline(self, monkeypatch):
        from core.config import settings

        monkeypatch.setattr(settings, "embed_backend", "local", raising=False)
        self._offline(monkeypatch, True)
        assert EmbeddingProvider._resolve_backend() == ("local", None)

    def test_auto_offline_prefiere_ollama(self, monkeypatch):
        from core.config import settings

        monkeypatch.setattr(settings, "embed_backend", "auto", raising=False)
        self._ollama_disponible(monkeypatch)
        self._offline(monkeypatch, True)
        kind, _backend = EmbeddingProvider._resolve_backend()
        assert kind == "ollama"

    def test_auto_offline_sin_ollama_cae_a_local_sin_sondear_remoto(self, monkeypatch):

        from core.config import settings

        monkeypatch.setattr(settings, "embed_backend", "auto", raising=False)
        self._ollama_caido(monkeypatch)
        self._offline(monkeypatch, True)
        sondeado = {"openai": False}

        class _Guardia:
            def __getattr__(self, item):
                sondeado["openai"] = True
                raise AssertionError("available() del backend openai sondeado en offline")

        monkeypatch.setattr(
            EmbeddingProvider,
            "_remote_for",
            classmethod(lambda cls, kind: _Guardia() if kind == "openai" else None),
        )
        kind, backend = EmbeddingProvider._resolve_backend()
        assert (kind, backend) == ("local", None)
        assert sondeado["openai"] is False

    def test_openai_offline_sin_ollama_mantiene_comportamiento_previo(self, monkeypatch):
        """openai elegido + offline + ollama caído: el remoto se devuelve como
        antes — su fallo y el fallback dim-coherente del caller hacen el
        resto (no adivinamos conectividad en la resolución)."""
        from core.config import settings

        monkeypatch.setattr(settings, "embed_backend", "openai", raising=False)
        self._ollama_caido(monkeypatch)
        self._offline(monkeypatch, True)
        kind, _backend = EmbeddingProvider._resolve_backend()
        assert kind == "openai"

    def test_fingerprint_cambia_al_conmutar_offline(self, monkeypatch):
        """El stamp auto-rebuild se dispara: offline cambia el fingerprint
        del backend efectivo (openai:… → ollama:nomic…)."""
        from core.config import settings

        monkeypatch.setattr(settings, "embed_backend", "openai", raising=False)
        self._ollama_disponible(monkeypatch)
        self._offline(monkeypatch, False)
        fp_online = EmbeddingProvider.fingerprint()
        self._offline(monkeypatch, True)
        fp_offline = EmbeddingProvider.fingerprint()
        assert fp_online != fp_offline
        assert fp_offline.startswith("ollama:")
