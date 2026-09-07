"""Prefijos obligatorios del modelo e5 + normalización L2 ajustable por flag.

e5 exige 'query: '/'passage: ' según uso y vectores normalizados en norma L2
para que las distancias/similaridades tengan la escala esperada por los
umbrales.
"""

import numpy as np
import pytest


class _FakeE5:
    """SentenceTransformer fake que registra el input crudo."""

    def __init__(self):
        self.last_input: str | None = None

    def encode(self, text: str) -> np.ndarray:
        self.last_input = text
        return np.array([3.0, 4.0], dtype=np.float32)


@pytest.fixture
def fake_provider(monkeypatch):
    from core.embedding_provider import EmbeddingProvider

    fake = _FakeE5()
    monkeypatch.setattr(EmbeddingProvider, "_model", fake)
    monkeypatch.setattr(EmbeddingProvider, "_ready", __import__("threading").Event())
    EmbeddingProvider._ready.set()
    yield fake, EmbeddingProvider
    EmbeddingProvider._model = None
    EmbeddingProvider._loading = False


def test_encode_applies_passage_prefix_by_default(fake_provider):
    fake, EP = fake_provider
    EP.encode("hecho del usuario")
    assert fake.last_input == "passage: hecho del usuario"


def test_encode_applies_query_prefix(fake_provider):
    fake, EP = fake_provider
    EP.encode("busca hecho", kind="query")
    assert fake.last_input == "query: busca hecho"


def test_encode_l2_normalizes_output(fake_provider):
    fake, EP = fake_provider
    out = EP.encode("algo")
    norm = float(np.linalg.norm(out))
    assert abs(norm - 1.0) < 1e-5, f"norma {norm} ≠ 1"


def test_normalize_flag_off_keeps_raw(fake_provider, monkeypatch):
    from core.config import settings

    fake, EP = fake_provider
    monkeypatch.setattr(settings, "embed_normalize", False)
    out = EP.encode("algo")
    assert abs(float(np.linalg.norm(out)) - 5.0) < 1e-5  # 3-4-5 sin normalizar
