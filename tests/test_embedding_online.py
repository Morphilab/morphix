# tests/test_embedding_online.py — backend openai configurable (endpoint + clave propia)
"""Endpoint OpenAI-compatible para embeddings: base URL configurable (p. ej.
NVIDIA integrate), clave DEDICADA con precedencia sobre la del chat,
input_type opt-in para modelos instruidos, fingerprint con host, perfil
nemotron, dimension() honesto y fallback remoto->local dim-coherente."""

from unittest.mock import AsyncMock

import numpy as np
import pytest

import core.embedding_provider as ep
from core.config import settings
from core.embedding_provider import EmbeddingProvider, OpenAIEmbedBackend, profile_for


def _patch_online(monkeypatch, *, base="https://integrate.api.nvidia.com/v1", key="nvapi-propia"):
    monkeypatch.setattr(settings, "embed_openai_base_url", base, raising=False)
    monkeypatch.setattr(settings, "embed_openai_api_key", key, raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "sk-chat", raising=False)


# ── Endpoint + clave ──────────────────────────────────────────────────────


def test_base_url_configurable_apunta_al_endpoint(monkeypatch):
    """EMBED_OPENAI_BASE_URL mueve el POST completo (URL + Bearer)."""
    import httpx

    _patch_online(monkeypatch)
    captured = {}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"index": 0, "embedding": [0.5, 0.5]}]}

    def fake_post(url, json=None, headers=None, **kw):
        captured["url"] = url
        captured["headers"] = headers
        return FakeResp()

    monkeypatch.setattr(httpx, "post", fake_post)
    b = OpenAIEmbedBackend("nvidia/nemotron-3-embed-1b")
    out = b.encode_texts(["hola"], kind="passage")
    assert out.shape == (1, 2)
    assert captured["url"] == "https://integrate.api.nvidia.com/v1/embeddings"
    assert captured["headers"]["Authorization"] == "Bearer nvapi-propia"


def test_clave_dedicada_tiene_precedencia_y_disponible(monkeypatch):
    _patch_online(monkeypatch)
    b = OpenAIEmbedBackend("nvidia/nemotron-3-embed-1b")
    assert b.api_key == "nvapi-propia"
    assert b.available() is True

    # Sin clave dedicada => retrocompatibilidad con la del chat
    monkeypatch.setattr(settings, "embed_openai_api_key", "", raising=False)
    assert b.api_key == "sk-chat"
    assert b.available() is True

    # Sin ninguna => no disponible
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)
    assert b.available() is False


def test_input_type_opt_in_solo_cuando_se_configura(monkeypatch):
    import httpx

    _patch_online(monkeypatch)
    captured = {}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"index": 0, "embedding": [0.5, 0.5]}]}

    def fake_post(url, json=None, headers=None, **kw):
        captured["json"] = json
        return FakeResp()

    monkeypatch.setattr(httpx, "post", fake_post)
    b = OpenAIEmbedBackend("nvidia/nemotron-3-embed-1b")

    monkeypatch.setattr(settings, "embed_openai_input_type", False, raising=False)
    b.encode_texts(["doc"], kind="passage")
    assert "input_type" not in captured["json"]

    monkeypatch.setattr(settings, "embed_openai_input_type", True, raising=False)
    b.encode_texts(["doc"], kind="passage")
    assert captured["json"]["input_type"] == "passage"
    b.encode_texts(["q"], kind="query")
    assert captured["json"]["input_type"] == "query"


def test_fingerprint_incluye_host_del_endpoint(monkeypatch):
    _patch_online(monkeypatch)
    fp = OpenAIEmbedBackend("nvidia/nemotron-3-embed-1b").fingerprint
    assert "integrate.api.nvidia.com" in fp
    assert "nemotron-3-embed-1b" in fp

    monkeypatch.setattr(
        settings, "embed_openai_base_url", "https://api.openai.com/v1", raising=False
    )
    fp_openai = OpenAIEmbedBackend("nvidia/nemotron-3-embed-1b").fingerprint
    # Mismo modelo en endpoints distintos = stamps distintos: la caché de un
    # proveedor jamás se confunde con la del otro.
    assert fp_openai != fp


# ── Perfil nemotron + dimension() honesto ─────────────────────────────────


def test_perfil_nemotron_dim_2048():
    familia, prof = profile_for("nvidia/nemotron-3-embed-1b")
    assert familia == "nemotron"
    assert prof["dim"] == 2048
    assert OpenAIEmbedBackend("nvidia/nemotron-3-embed-1b").dimension == 2048


# ── Perfil qwen3-embedding (DeepInfra) ─────────────────────────────────────


def test_qwen3_prefix_de_query_en_payload_y_pasaje_limpio(monkeypatch):
    """La instrucción de Qwen3 viaja en el TEXTO del payload (DeepInfra no la
    aplica solo): kind=query lleva el prefijo Instruct/Query, kind=passage va
    limpio."""
    import httpx

    _patch_online(monkeypatch, base="https://api.deepinfra.com/v1/openai", key="dfi-propia")
    captured = {}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"index": 0, "embedding": [0.5, 0.5]}]}

    def fake_post(url, json=None, headers=None, **kw):
        captured["url"] = url
        captured["json"] = json
        return FakeResp()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(settings, "embed_openai_input_type", False, raising=False)
    b = OpenAIEmbedBackend("Qwen/Qwen3-Embedding-8B")

    b.encode_texts(["¿Qué es FAISS?"], kind="query")
    (payload_query,) = captured["json"]["input"]
    assert payload_query.startswith("Instruct: ")
    assert payload_query.endswith("\nQuery: ¿Qué es FAISS?")
    assert captured["url"] == "https://api.deepinfra.com/v1/openai/embeddings"

    b.encode_texts(["FAISS es un índice."], kind="passage")
    assert captured["json"]["input"] == ["FAISS es un índice."]


def test_qwen3_dim_4096_y_fingerprint_distinto_de_nemotron(monkeypatch):
    """dim derivada del perfil (4096 publicada para el 8B) y stamps que no
    colisionan con nemotron aunque compartan backend: el host del fingerprint
    separa DeepInfra de NVIDIA. fingerprint es property que lee settings en
    vivo — capturar cada uno ANTES de mover el base_url."""
    _patch_online(monkeypatch, base="https://api.deepinfra.com/v1/openai", key="dfi-propia")
    b_qwen = OpenAIEmbedBackend("Qwen/Qwen3-Embedding-8B")
    assert b_qwen.dimension == 4096
    fp_qwen = b_qwen.fingerprint
    assert "api.deepinfra.com" in fp_qwen

    monkeypatch.setattr(settings, "embed_openai_base_url", "https://integrate.api.nvidia.com/v1")
    b_nemotron = OpenAIEmbedBackend("nvidia/nemotron-3-embed-1b")
    fp_nemotron = b_nemotron.fingerprint
    assert "integrate.api.nvidia.com" in fp_nemotron
    assert fp_qwen != fp_nemotron


def test_dimension_remoto_desconocido_no_hereda_la_local(monkeypatch):
    """Un remoto sin dim conocida debe reportar 0 — heredar la del local
    construiría un índice que rechaza todos los chunks del backend efectivo."""

    class RemotoSinDim:
        @staticmethod
        def encode_texts(texts, kind="passage"):
            return np.ones((len(texts), 8), dtype="float32")

    monkeypatch.setattr(EmbeddingProvider, "_resolve_backend", lambda: ("ollama", RemotoSinDim))
    assert EmbeddingProvider.dimension() == 0


def test_dimension_remoto_conocida_manda(monkeypatch):
    class RemotoConDim:
        dimension = 768

        @staticmethod
        def encode_texts(texts, kind="passage"):
            return np.ones((len(texts), 768), dtype="float32")

    monkeypatch.setattr(EmbeddingProvider, "_resolve_backend", lambda: ("ollama", RemotoConDim))
    assert EmbeddingProvider.dimension() == 768


# ── Fallback dim-coherente ────────────────────────────────────────────────


class _RemotoQueCae:
    """Backend remoto que SIEMPRE falla, con dim declarada configurable."""

    def __init__(self, dim: int):
        self._dim = dim

    @property
    def dimension(self) -> int:
        return self._dim

    def encode_texts(self, texts, kind="passage"):
        raise ConnectionError("endpoint caído")


@pytest.mark.asyncio
async def test_fallback_descartado_si_dims_incoherentes(monkeypatch):
    """Remoto 768 cae con local 1024: el fallback NO escribe vectores de otra
    dimensión — los misses quedan en None y el índice no se corrompe."""
    import asyncio

    monkeypatch.setattr(EmbeddingProvider, "_model", None, raising=False)
    local_calls = []

    def fake_local_batch(texts, kind="passage"):
        local_calls.append(list(texts))
        return [np.ones(1024, dtype="float32") for _ in texts]

    monkeypatch.setattr(
        EmbeddingProvider, "_encode_batch_local", staticmethod(fake_local_batch), raising=True
    )
    monkeypatch.setattr(
        EmbeddingProvider, "_resolve_backend", lambda: ("ollama", _RemotoQueCae(768))
    )

    # textos ÚNICOS: el LRU de clase es global y un test previo puede haber
    # cacheado textos cortos bajo el fingerprint de fallback ("no-fp")
    import time

    u1 = f"dim-incoherente-{time.monotonic_ns()}"
    u2 = f"otro-dim-incoherente-{time.monotonic_ns()}"
    out = await asyncio.to_thread(EmbeddingProvider.encode_batch, [u1, u2])
    assert out == [None, None]
    assert local_calls == []  # jamás tocó el local: dim incoherente


@pytest.mark.asyncio
async def test_fallback_procede_si_dims_coinciden(monkeypatch):
    """Remoto 1024 cae con local 1024: cae (misma dim, modelo distinto — el
    warning de calidad va al log, la coherencia dimensional se respeta)."""
    import asyncio

    monkeypatch.setattr(EmbeddingProvider, "_model", None, raising=False)
    local_calls = []

    def fake_local_batch(texts, kind="passage"):
        local_calls.append(list(texts))
        return [np.ones(1024, dtype="float32") for _ in texts]

    monkeypatch.setattr(
        EmbeddingProvider, "_encode_batch_local", staticmethod(fake_local_batch), raising=True
    )
    monkeypatch.setattr(
        EmbeddingProvider, "_resolve_backend", lambda: ("ollama", _RemotoQueCae(1024))
    )

    import time as _t

    u = f"dim-coherente-{_t.monotonic_ns()}"
    out = await asyncio.to_thread(EmbeddingProvider.encode_batch, [u])
    assert out[0] is not None and out[0].shape == (1024,)
    assert len(local_calls) == 1


# ── Puente de eventos del workflow ────────────────────────────────────────


@pytest.mark.asyncio
async def test_emit_indexing_progress_dispara_callback():
    from orchestration.context import WorkflowEvents, emit_indexing_progress

    cb = AsyncMock()
    events = WorkflowEvents(on_indexing_progress=cb)
    await emit_indexing_progress(events, {"phase": "indexing", "pct": 10})
    cb.assert_awaited_once_with({"phase": "indexing", "pct": 10})


@pytest.mark.asyncio
async def test_emit_indexing_progress_none_es_noop():
    from orchestration.context import WorkflowEvents, emit_indexing_progress

    events = WorkflowEvents()
    await emit_indexing_progress(events, {"phase": "done"})  # no rompe


def test_lru_cache_con_backend_online_invalida_por_fingerprint(monkeypatch):
    """La clave del LRU lleva el fingerprint del backend efectivo — cambiar de
    endpoint/modelo NO sirve vectores del remoto anterior."""
    import time

    _patch_online(monkeypatch)
    text = f"texto-unico-{time.monotonic_ns()}"
    EmbeddingProvider._cache.clear()

    import httpx

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"index": 0, "embedding": [0.6, 0.8]}]}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp())
    monkeypatch.setattr(
        EmbeddingProvider,
        "_resolve_backend",
        lambda: ("openai", OpenAIEmbedBackend("nvidia/nemotron-3-embed-1b")),
    )
    out1 = EmbeddingProvider.encode(text, kind="passage")
    assert out1 is not None and out1.shape == (2,)
    # mismo texto, MISMO backend: hit de caché (no re-POST)
    stats_before = EmbeddingProvider._cache_misses
    EmbeddingProvider.encode(text, kind="passage")
    assert EmbeddingProvider._cache_misses == stats_before
    ep.EmbeddingProvider._cache.clear()


def test_backoff_ante_429(monkeypatch):
    """El rate-limit del endpoint no debe perder el embedding: reintento
    con espera breve antes de tirar la toalla."""
    import time

    import httpx

    _patch_online(monkeypatch)
    calls = {"n": 0}

    class _Resp429:
        status_code = 429

    class _RespOK:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"index": 0, "embedding": [0.5, 0.5]}]}

    def fake_post(url, json=None, headers=None, **kw):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise httpx.HTTPStatusError("429", request=None, response=_Resp429())
        return _RespOK()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    b = OpenAIEmbedBackend("nvidia/nemotron-3-embed-1b")
    out = b.encode_texts(["hola"], kind="passage")
    assert out.shape == (1, 2)
    assert calls["n"] == 3


def test_429_persistente_falla_loud(monkeypatch):
    import time

    import httpx

    _patch_online(monkeypatch)
    calls = {"n": 0}

    class _Resp429:
        status_code = 429

    def fake_post(url, json=None, headers=None, **kw):
        calls["n"] += 1
        raise httpx.HTTPStatusError("429", request=None, response=_Resp429())

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    b = OpenAIEmbedBackend("nvidia/nemotron-3-embed-1b")
    with pytest.raises(httpx.HTTPStatusError):
        b.encode_texts(["hola"], kind="passage")
    assert calls["n"] == 3
