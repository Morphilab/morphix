"""Registro de perfiles por familia + provider configurable (doc embeddings)."""

import threading

import numpy as np
import pytest

from core.embedding_provider import MODEL_PROFILES, EmbeddingProvider, profile_for


def test_e5_profile_has_prefixes_and_dim():
    fam, prof = profile_for("intfloat/multilingual-e5-large")
    assert fam == "e5"
    assert prof["query_prefix"] == "query: "
    assert prof["passage_prefix"] == "passage: "
    assert prof["dim"] == 1024
    assert prof["normalize"] is True


def test_bge_profile_no_prefixes():
    fam, prof = profile_for("BAAI/bge-m3")
    assert fam == "bge"
    assert not prof.get("query_prefix")
    assert not prof.get("passage_prefix")
    assert prof["dim"] == 1024


def test_nomic_dim_768_and_unknown_falls_back_plain():
    _, prof = profile_for("nomic-ai/nomic-embed-text-v1.5")
    assert prof["dim"] == 768
    fam, prof = profile_for("org/modelo-desconocido")
    assert fam == "plain"
    assert not prof.get("query_prefix")


def test_registry_has_expected_families():
    assert {"e5", "bge", "nomic", "mxbai", "plain"} <= set(MODEL_PROFILES)


def test_model_name_from_settings(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "embed_model", "BAAI/bge-m3", raising=False)
    assert EmbeddingProvider._model_name() == "BAAI/bge-m3"


def test_fingerprint_stable_and_sensitive(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "embed_model", "intfloat/multilingual-e5-large", raising=False)
    monkeypatch.setattr(settings, "embed_normalize", True, raising=False)
    fp_a = EmbeddingProvider.fingerprint()
    fp_b = EmbeddingProvider.fingerprint()
    assert fp_a == fp_b
    assert len(fp_a) == 8

    monkeypatch.setattr(settings, "embed_model", "BAAI/bge-m3", raising=False)
    fp_c = EmbeddingProvider.fingerprint()
    assert fp_c != fp_a

    monkeypatch.setattr(settings, "embed_normalize", False, raising=False)
    assert EmbeddingProvider.fingerprint() != fp_c


class _SpyST:
    """Fake SentenceTransformer que registra el input crudo."""

    def __init__(self, dim=3):
        self.dim = dim
        self.last_input = None

    def encode(self, text, **kw):
        self.last_input = text
        return np.ones(self.dim, dtype=np.float32)

    def get_sentence_embedding_dimension(self):
        return self.dim


@pytest.fixture
def spy_provider(monkeypatch):
    fake = _SpyST()
    monkeypatch.setattr(EmbeddingProvider, "_model", fake)
    ready = threading.Event()
    ready.set()
    monkeypatch.setattr(EmbeddingProvider, "_ready", ready)
    return fake


def test_encode_no_prefix_for_non_e5(monkeypatch, spy_provider):
    from core.config import settings

    monkeypatch.setattr(settings, "embed_model", "BAAI/bge-m3", raising=False)
    EmbeddingProvider.encode("hola mundo")
    assert spy_provider.last_input == "hola mundo"


def test_encode_prefix_for_e5(monkeypatch, spy_provider):
    from core.config import settings

    monkeypatch.setattr(settings, "embed_model", "intfloat/multilingual-e5-large", raising=False)
    EmbeddingProvider.encode("hola mundo", kind="query")
    assert spy_provider.last_input.startswith("query: ")


def test_dimension_from_profile(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "embed_model", "intfloat/multilingual-e5-large", raising=False)
    assert EmbeddingProvider.dimension() == 1024


# ── device / batch / preload ──


def test_load_model_passes_configured_device(monkeypatch):
    from core import embedding_provider as ep
    from core.config import settings

    captured = {}

    class FakeST:
        def __init__(self, name, **kw):
            captured["name"] = name
            captured["kwargs"] = kw

    monkeypatch.setattr(settings, "embed_device", "cpu", raising=False)
    monkeypatch.setattr(ep, "_MAX_LOAD_ATTEMPTS", 1)
    monkeypatch.setattr(ep.EmbeddingProvider, "_load_attempts", 0)
    monkeypatch.setattr("sentence_transformers.SentenceTransformer", FakeST)
    ep.EmbeddingProvider._load_model()
    assert captured["kwargs"].get("device") == "cpu"


def test_load_model_auto_omits_device(monkeypatch):
    from core import embedding_provider as ep
    from core.config import settings

    captured = {}

    class FakeST:
        def __init__(self, name, **kw):
            captured["kwargs"] = kw

    monkeypatch.setattr(settings, "embed_device", "auto", raising=False)
    monkeypatch.setattr(ep, "_MAX_LOAD_ATTEMPTS", 1)
    monkeypatch.setattr(ep.EmbeddingProvider, "_load_attempts", 0)
    monkeypatch.setattr("sentence_transformers.SentenceTransformer", FakeST)
    ep.EmbeddingProvider._load_model()
    assert "device" not in captured["kwargs"]


def test_encode_batch_prefixes_and_normalizes(monkeypatch):
    from core.config import settings

    calls = {}

    class BatchST(_SpyST):
        def encode(self, texts, batch_size=32, **kw):
            calls["batch_size"] = batch_size
            calls["inputs"] = list(texts)
            return np.ones((len(texts), 3), dtype=np.float32)

    fake = BatchST()
    monkeypatch.setattr(EmbeddingProvider, "_model", fake)
    ready = threading.Event()
    ready.set()
    monkeypatch.setattr(EmbeddingProvider, "_ready", ready)
    monkeypatch.setattr(settings, "embed_model", "intfloat/multilingual-e5-large", raising=False)
    monkeypatch.setattr(settings, "embed_batch_size", 7, raising=False)

    out = EmbeddingProvider.encode_batch(["a", "b"], kind="query")
    assert calls["batch_size"] == 7
    assert all(i.startswith("query: ") for i in calls["inputs"])
    norms = [float(np.linalg.norm(v)) for v in out]
    assert all(abs(n - 1.0) < 1e-5 for n in norms), out


def test_encode_batch_empty_and_not_ready(monkeypatch):
    assert EmbeddingProvider.encode_batch([]) == []
    monkeypatch.setattr(EmbeddingProvider, "_model", None)
    ready = threading.Event()
    monkeypatch.setattr(EmbeddingProvider, "_ready", ready)
    assert EmbeddingProvider.encode_batch(["x"]) == [None]


def test_preload_setting_triggers_get_instance(monkeypatch):
    """EMBED_PRELOAD=true dispara get_instance en el hook de arranque."""
    from core.config import settings
    from core.embedding_provider import preload_if_configured as preload_embeddings_if_configured

    called = {"n": 0}

    def spy():
        called["n"] += 1
        return None

    monkeypatch.setattr(settings, "embed_preload", True, raising=False)
    monkeypatch.setattr(EmbeddingProvider, "get_instance", staticmethod(spy))
    preload_embeddings_if_configured()
    assert called["n"] == 1


def test_preload_disabled_does_not_touch_provider(monkeypatch):
    from core.config import settings
    from core.embedding_provider import preload_if_configured as preload_embeddings_if_configured

    called = {"n": 0}

    def spy():
        called["n"] += 1
        return None

    monkeypatch.setattr(settings, "embed_preload", False, raising=False)
    monkeypatch.setattr(EmbeddingProvider, "get_instance", staticmethod(spy))
    preload_embeddings_if_configured()
    assert called["n"] == 0
