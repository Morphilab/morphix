"""semantic_search sobre la columna Message.embedding persistida.

La columna ES la caché: backfill lazy solo de mensajes sin embedding
(encode_batch único + flush), sin caché Redis de vectores.
Paridad de vectores: encode_batch aplica el MISMO prefijo de familia y
normalización L2 que encode(kind="passage") — ver core/embedding_provider.py.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest


def _msg(mid, content, embedding=None):
    m = MagicMock()
    m.id = mid
    m.content = content
    m.conversation_id = 7
    m.embedding = embedding
    # Por defecto el fp coincide con el parcheado en los tests ('fp-test')
    m.embedding_fp = "fp-test"
    return m


def _vec4():
    # ones*0.5 ya está normalizado (norma = 1) — escala del umbral semántico
    return np.ones(4, dtype="float32") * 0.5


@pytest.fixture(autouse=True)
def _isolated_history_class_state():
    """Evita fugas del client Redis cacheado a nivel de clase entre tests."""
    from desktop.services.history_service import HistoryService

    if hasattr(HistoryService, "_redis_client"):
        HistoryService._redis_client = None
    yield
    if hasattr(HistoryService, "_redis_client"):
        HistoryService._redis_client = None


@pytest.mark.asyncio
async def test_search_encodes_missing_only_then_persists():
    """2 mensajes sin embedding + 1 con → encode_batch UNA vez con los 2 textos;
    tras la búsqueda las filas tienen .embedding != None."""
    msgs = [
        _msg(3, "texto tres"),
        _msg(2, "texto dos"),
        _msg(1, "texto uno", embedding=_vec4().tobytes()),
    ]
    convs = [MagicMock(id=7)]
    msgs_result = MagicMock()
    msgs_result.scalars.return_value.all.return_value = msgs
    convs_result = MagicMock()
    convs_result.scalars.return_value.all.return_value = convs
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[msgs_result, convs_result])
    session.flush = AsyncMock()

    batch_calls: list[list[str]] = []

    def fake_batch(texts):
        batch_calls.append(list(texts))
        return [_vec4() for _ in texts]

    with (
        patch("desktop.services.history_service._get_embed_model", return_value=MagicMock()),
        patch(
            "core.embedding_provider.EmbeddingProvider.encode",
            side_effect=lambda text, kind="passage": _vec4(),
        ),
        patch(
            "core.embedding_provider.EmbeddingProvider.encode_batch",
            side_effect=fake_batch,
        ),
        patch(
            "core.embedding_provider.EmbeddingProvider.fingerprint",
            return_value="fp-test",
        ),
        patch("core.embedding_provider.EmbeddingProvider.wait_until_ready", return_value=True),
    ):
        from desktop.services.history_service import HistoryService

        out = await HistoryService.semantic_search("consulta", session)

    assert len(batch_calls) == 1, f"encode_batch debe llamarse UNA vez: {batch_calls}"
    assert batch_calls[0] == [
        "texto tres",
        "texto dos",
    ], f"solo los mensajes SIN embedding van al batch: {batch_calls}"
    assert out == convs
    for m in msgs:
        assert m.embedding is not None, f"msg {m.id} quedó sin embedding persistido"
        assert isinstance(m.embedding, bytes)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_second_search_skips_encoding():
    """Segunda búsqueda sobre las mismas filas: todo ya tiene embedding →
    encode_batch NO vuelve a llamarse ni hay flush."""
    msgs = [_msg(2, "texto dos"), _msg(1, "texto uno")]
    convs = [MagicMock(id=7)]

    def results_pair():
        mr = MagicMock()
        mr.scalars.return_value.all.return_value = msgs
        cr = MagicMock()
        cr.scalars.return_value.all.return_value = convs
        return [mr, cr]

    session = MagicMock()
    session.execute = AsyncMock(side_effect=results_pair() + results_pair())
    session.flush = AsyncMock()

    batch_mock = MagicMock(side_effect=lambda texts: [_vec4() for _ in texts])

    with (
        patch("desktop.services.history_service._get_embed_model", return_value=MagicMock()),
        patch(
            "core.embedding_provider.EmbeddingProvider.encode",
            side_effect=lambda text, kind="passage": _vec4(),
        ),
        patch("core.embedding_provider.EmbeddingProvider.encode_batch", batch_mock),
        patch("core.embedding_provider.EmbeddingProvider.wait_until_ready", return_value=True),
    ):
        from desktop.services.history_service import HistoryService

        await HistoryService.semantic_search("primera", session)
        await HistoryService.semantic_search("segunda", session)

    batch_mock.assert_called_once(), "la segunda búsqueda NO debe re-encodear"
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_redis_vector_cache_removed():
    """Durante la búsqueda NO se escriben claves emb:v3 — la caché de
    vectores es la columna Message.embedding, no Redis."""
    msgs = [_msg(1, "texto uno", embedding=_vec4().tobytes())]
    convs = [MagicMock(id=7)]
    msgs_result = MagicMock()
    msgs_result.scalars.return_value.all.return_value = msgs
    convs_result = MagicMock()
    convs_result.scalars.return_value.all.return_value = convs
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[msgs_result, convs_result])
    session.flush = AsyncMock()

    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=None)
    fake_client.set = AsyncMock()
    url_factory = MagicMock(return_value=fake_client)

    with (
        patch("desktop.services.history_service._get_embed_model", return_value=MagicMock()),
        patch(
            "core.embedding_provider.EmbeddingProvider.encode",
            side_effect=lambda text, kind="passage": _vec4(),
        ),
        patch(
            "core.embedding_provider.EmbeddingProvider.fingerprint",
            return_value="fp-test",
        ),
        patch("core.embedding_provider.EmbeddingProvider.wait_until_ready", return_value=True),
        patch("redis.asyncio.from_url", url_factory),
    ):
        from desktop.services.history_service import HistoryService

        await HistoryService.semantic_search("consulta", session)

    assert not url_factory.called, "la búsqueda no debe tocar caché Redis"
    assert not hasattr(HistoryService, "_EMB_CACHE_VERSION"), "constante de caché Redis huérfana"


@pytest.mark.asyncio
async def test_semantic_search_uses_class_encode_with_query_kind():
    """La query se encodea vía EmbeddingProvider.encode(kind='query')."""
    from desktop.services.history_service import HistoryService

    calls: list[tuple] = []

    def fake_encode(text, kind="passage"):
        calls.append((text, kind))
        arr = np.ones(4, dtype="float32") / 2.0
        return arr / np.linalg.norm(arr)

    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = []  # sin mensajes → sale temprano
    session = MagicMock()
    session.execute = AsyncMock(return_value=result_mock)

    with (
        patch("desktop.services.history_service._get_embed_model", return_value=MagicMock()),
        patch("core.embedding_provider.EmbeddingProvider.encode", side_effect=fake_encode),
    ):
        await HistoryService.semantic_search("mi consulta", session)

    assert calls, "no se llamó a encode"
    assert calls[0][1] == "query", f"la query no usa convención e5 query: {calls}"
    assert calls[0][0] == "mi consulta"


@pytest.mark.asyncio
async def test_semantic_search_falls_back_when_encode_returns_none():
    """EmbeddingProvider.encode None (modelo no listo) → fallback keyword."""
    from desktop.services.history_service import HistoryService

    convs = [MagicMock(id=1, created_at=0)]
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = convs
    session = MagicMock()
    session.execute = AsyncMock(return_value=result_mock)

    with (
        patch("desktop.services.history_service._get_embed_model", return_value=MagicMock()),
        patch("core.embedding_provider.EmbeddingProvider.encode", return_value=None),
        patch.object(
            HistoryService,
            "_keyword_fallback",
            new=AsyncMock(return_value=convs),
        ) as kw_mock,
    ):
        out = await HistoryService.semantic_search("consulta", session)

    kw_mock.assert_awaited_once()
    assert out == convs


def test_semantic_threshold_matches_normalized_cosine_scale():
    """Umbral 0.9 sobre L2² normalizado ⇔ coseno > ~0.6 (2 − 2cos)."""
    import math

    from desktop.services.history_service import HistoryService

    threshold = HistoryService._SEMANTIC_DIST_THRESHOLD
    cos_at_threshold = 1 - (threshold**2) / 2
    assert math.isclose(cos_at_threshold, 0.595, abs_tol=0.01)
    # Un vector idéntico → dist 0 (pasa); ortogonal → dist √2 ≈ 1.414 (falla)
    assert 0.0 < threshold
    assert math.sqrt(2) > threshold


@pytest.mark.asyncio
async def test_semantic_search_falls_back_to_keyword_when_no_model():
    """Provider zombi → semantic_search NO rompe; usa búsqueda LIKE."""
    from desktop.services.history_service import HistoryService

    convs = [MagicMock(id=1, created_at=0)]
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = convs
    session = MagicMock()
    session.execute = AsyncMock(return_value=result_mock)

    with (
        patch("desktop.services.history_service._get_embed_model", return_value=None),
        patch("core.database.get_async_schema", return_value="main"),
    ):
        out = await HistoryService.semantic_search("pagos con tarjeta", session)

    assert out == convs, f"NV-M6: sin modelo de embeddings rompió la búsqueda: {out}"


@pytest.mark.asyncio
async def test_load_conversations_survives_semantic_search_crash():
    """Si semantic_search lanza, load_conversations no debe propagar."""
    from desktop.services.history_service import HistoryService

    convs = [MagicMock(created_at=0)]
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = convs
    session = MagicMock()
    session.execute = AsyncMock(return_value=result_mock)

    # Hermético: la sesión real de load_conversations se sustituye — este test
    # ejercita SOLO la lógica de fallback, no la infraestructura DB.
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch.object(
            HistoryService,
            "semantic_search",
            side_effect=RuntimeError("FAISS explotó"),
        ),
        patch(
            "desktop.services.history_service.get_async_session",
            return_value=mock_cm,
        ),
    ):
        out = await HistoryService.load_conversations(query="algo raro")

    assert isinstance(out, list)


# ── fingerprint-stamp en embeddings persistidos ──────────────────────────


def _fake_ix_cls(monkeypatch):
    """Sustituye FAISSIndexer por un doble que registra adds y no devuelve hits."""

    class FakeIx:
        added: list = []

        def __init__(self, dimension, embedder=None):
            self.dimension = dimension

        def add_embedding(self, key, text, vec):
            FakeIx.added.append((key, vec))

        def search(self, q, k=10):
            return []

    import core.faiss_indexer as fmod

    monkeypatch.setattr(fmod, "FAISSIndexer", FakeIx, raising=True)
    return FakeIx


@pytest.mark.asyncio
async def test_backfill_stamps_fingerprint(monkeypatch):
    """Backfill lazy escribe embedding_fp = fingerprint del backend actual."""
    from desktop.services.history_service import HistoryService

    msgs = [_msg(1, "alpha"), _msg(2, "beta")]
    msgs_result = MagicMock()
    msgs_result.scalars.return_value.all.return_value = msgs
    session = MagicMock()
    session.execute = AsyncMock(return_value=msgs_result)
    session.flush = AsyncMock()

    batch_calls: list[list[str]] = []

    def fake_batch(texts):
        batch_calls.append(list(texts))
        return [_vec4() for _ in texts]

    _fake_ix_cls(monkeypatch)
    with (
        patch("desktop.services.history_service._get_embed_model", return_value=MagicMock()),
        patch(
            "core.embedding_provider.EmbeddingProvider.encode",
            side_effect=lambda text, kind="passage": _vec4(),
        ),
        patch(
            "core.embedding_provider.EmbeddingProvider.encode_batch",
            side_effect=fake_batch,
        ),
        patch(
            "core.embedding_provider.EmbeddingProvider.fingerprint",
            return_value="fp-test",
        ),
    ):
        await HistoryService.semantic_search("consulta", session)

    assert len(batch_calls) == 1
    for m in msgs:
        assert m.embedding is not None
        assert m.embedding_fp == "fp-test"


@pytest.mark.asyncio
async def test_stale_fingerprint_reembedded(monkeypatch):
    """Embedding con fp distinto al actual se re-encodea — misma-dim de otra
    familia mezclaría escalas silenciosamente."""
    from desktop.services.history_service import HistoryService

    stale = _msg(3, "old", embedding=_vec4().tobytes())
    stale.embedding_fp = "otro-familia"
    fresh = _msg(4, "nuevo", embedding=_vec4().tobytes())

    msgs_result = MagicMock()
    msgs_result.scalars.return_value.all.return_value = [stale, fresh]
    session = MagicMock()
    session.execute = AsyncMock(return_value=msgs_result)
    session.flush = AsyncMock()

    batch_calls: list[list[str]] = []

    def fake_batch(texts):
        batch_calls.append(list(texts))
        return [_vec4() for _ in texts]

    _fake_ix_cls(monkeypatch)
    with (
        patch("desktop.services.history_service._get_embed_model", return_value=MagicMock()),
        patch(
            "core.embedding_provider.EmbeddingProvider.encode",
            side_effect=lambda text, kind="passage": _vec4(),
        ),
        patch(
            "core.embedding_provider.EmbeddingProvider.encode_batch",
            side_effect=fake_batch,
        ),
        patch(
            "core.embedding_provider.EmbeddingProvider.fingerprint",
            return_value="fp-test",
        ),
    ):
        await HistoryService.semantic_search("q", session)

    # SOLO el stale entra al batch; el fresco se reutiliza tal cual
    assert batch_calls == [["old"]]
    assert stale.embedding_fp == "fp-test"
