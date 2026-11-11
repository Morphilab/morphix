# tests/test_indexer_checkpoint.py — persistencia por lote, single-flight y aborto
"""index_project con checkpoint por lote (hash del archivo marcado SOLO
cuando sus chunks viven en el índice persistido), single-flight por
(workspace, project) y aborto cooperativo entre lotes con el checkpoint a
salvo — un intento interrumpido retoma donde estaba.

Los ganchos de aborto/bloqueo viven en el SCAN (progress_callback del 5º
archivo, chunk_large_file) — NUNCA en el modelo de embeddings: una campaña
de carga legada de un test anterior puede completarse a mitad del test y
pisar el modelo fake (thread daemon), y el hook dejaría de disparar.
Los contadores son de CHUNKS persistidos, no de batches del modelo."""

import json
import threading
from collections import OrderedDict
from types import SimpleNamespace

import numpy as np
import pytest

import core.codebase_indexer as ci
from core.codebase_indexer import request_indexing_shutdown, reset_indexing_shutdown
from core.embedding_provider import EmbeddingProvider
from core.faiss_indexer import FAISS_DIMENSION


class _FakeModel:
    """Modelo local fake determinista (dim e5, vectores ones)."""

    def __init__(self, dim: int = FAISS_DIMENSION):
        self.dim = dim

    def get_sentence_embedding_dimension(self):
        return self.dim

    def encode(self, texts, batch_size=32):
        return [np.ones(self.dim, dtype="float32") for _ in texts]


@pytest.fixture(autouse=True)
def _provider_listo(monkeypatch):
    """Provider en estado LISTO con modelo fake; LRU limpio (simula procesos
    distintos — la caché en-proceso NO debe tapar el checkpoint) y
    shutdown/registro en vuelo rearmandose para aislar tests."""
    model = _FakeModel()
    ready = threading.Event()
    ready.set()
    monkeypatch.setattr(EmbeddingProvider, "_model", model)
    monkeypatch.setattr(EmbeddingProvider, "_ready", ready)
    monkeypatch.setattr(EmbeddingProvider, "_loading", False)
    monkeypatch.setattr(EmbeddingProvider, "_cache", OrderedDict())
    reset_indexing_shutdown()
    yield model
    reset_indexing_shutdown()
    ci._IN_FLIGHT.clear()


@pytest.fixture
def proyecto(tmp_path, monkeypatch):
    """Layout de PRODUCCIÓN: memory_dir y proyecto en árboles separados.

    base = <mem>/<proj>, cache = <mem>/.codebase_cache/<proj> — si el cache
    viviera DENTRO del proyecto, el propio scan lo indexaría (.json es
    extensión válida) y el test mentiría. 8 archivos para que el progreso
    del scan (cada 5) dispare DENTRO del ciclo."""
    mem = tmp_path / "mem"
    proj = mem / "proj"
    proj.mkdir(parents=True)
    for n in range(8):
        (proj / f"f{n}.py").write_text(f"def f{n}():\n    return {n}\n")
    monkeypatch.setattr(ci.paths, "memory_dir", lambda workspace: mem)
    return proj


@pytest.fixture
def batch_1(monkeypatch):
    """Frontera de lote por archivo: el aborto a mitad de scan corta ANTES
    del final (con batch grande el flush final del aborto completa todo —
    comportamiento correcto, pero el test de retoma necesita el corte)."""
    from core.config import settings

    monkeypatch.setattr(settings, "embed_batch_size", 1, raising=False)


def _indexar(workspace: str = "main", project_root: str = "proj", **kw):
    indexer = ci.CodebaseIndexer(project_root=project_root, workspace=workspace)
    return indexer, indexer.index_project(**kw)


def _hashes(proyecto):
    cache = proyecto.parent / ".codebase_cache" / "proj" / "codebase_index.json"
    return json.loads(cache.read_text())


def _abortar_en_el_quinto_archivo(eventos: list[dict]):
    """Callback de progreso del scan: al 5º archivo pide el aborto —
    determinista (hilo del scan, antes de cualquier encode)."""

    def _cb(data: dict) -> None:
        eventos.append(data)
        if data.get("phase") == "indexing":
            request_indexing_shutdown()

    return _cb


def test_checkpoint_por_lote_abortado_retoma_donde_estaba(proyecto, _provider_listo, batch_1):
    """Aborto al 5º archivo: los lotes COMPLETOS quedan persistidos y el
    lote en vuelo se DESCARTA (sus hashes nunca se marcaron — el próximo run
    los re-encodea); la segunda pasada (proceso nuevo, LRU limpio) indexa
    SOLO lo que falta. Antes un intento abortado no dejaba NADA en disco."""
    eventos: list[dict] = []
    _, chunks1 = _indexar(progress_callback=_abortar_en_el_quinto_archivo(eventos))
    assert eventos[-1]["phase"] == "aborted"
    assert eventos[-2]["phase"] == "indexing"

    parcial = _hashes(proyecto)
    restantes = {f"f{n}.py" for n in range(8)} - set(parcial)
    # marcó lo escaneado antes del aborto y NO el resto — el corte exacto
    # depende del batching; lo que NO puede pasar es marcar TODO o NADA
    assert 0 < len(parcial) < 8
    assert len(restantes) > 0
    assert (proyecto.parent / ".codebase_cache" / "proj" / "faiss.index").exists()
    assert chunks1 == len(parcial)  # chunks persistidos == archivos marcados

    # segunda pasada: sin aborto — indexa SOLO los que faltaban
    reset_indexing_shutdown()
    _, chunks2 = _indexar()
    assert chunks2 == len(restantes)  # nada re-encodeado de lo ya marcado
    assert set(_hashes(proyecto)) == {f"f{n}.py" for n in range(8)}


def test_eventos_de_progreso_indexing_y_done(proyecto, _provider_listo):
    eventos: list[dict] = []
    _indexar(progress_callback=eventos.append)
    assert any(e["phase"] == "indexing" for e in eventos), eventos
    assert eventos[-1]["phase"] == "done"
    assert eventos[-1]["pct"] == 100


def test_eventos_de_progreso_aborted_en_shutdown(proyecto, _provider_listo):
    eventos: list[dict] = []
    _indexar(progress_callback=_abortar_en_el_quinto_archivo(eventos))
    assert eventos[-1]["phase"] == "aborted"


def _bloquear_primer_chunk(dentro: threading.Event, liberar: threading.Event, monkeypatch):
    """Congela el scan en el PRIMER chunk (hilo del scan, no del modelo)."""
    original = ci.ContextManager.chunk_large_file

    def bloquear(content, path):
        if not dentro.is_set():
            dentro.set()
            liberar.wait(timeout=5)
        return original(content, path)

    monkeypatch.setattr(ci, "ContextManager", SimpleNamespace(chunk_large_file=bloquear))


def test_single_flight_segunda_llamada_hace_skip(proyecto, _provider_listo, monkeypatch):
    """Un indexado en curso para (workspace, project): una segunda llamada
    concurrente (retry tras time-box, preload contra vía automática) no
    duplica el trabajo ni pisa el checkpoint."""
    dentro = threading.Event()
    liberar = threading.Event()
    _bloquear_primer_chunk(dentro, liberar, monkeypatch)

    resultado: dict = {}
    t = threading.Thread(
        target=lambda: resultado.update(
            chunks=ci.CodebaseIndexer(project_root="proj", workspace="main").index_project()
        ),
        daemon=True,
    )
    t.start()
    assert dentro.wait(timeout=5)

    # segunda llamada MISMA clave mientras la primera corre
    _, chunks_segunda = _indexar()
    assert chunks_segunda == 0  # skip: ya hay un indexado en vuelo

    liberar.set()
    t.join(timeout=10)
    assert resultado["chunks"] > 0
    assert len(_hashes(proyecto)) == 8


def test_single_flight_por_clave_otra_clave_no_salta(proyecto, _provider_listo, monkeypatch):
    """El single-flight es por (workspace, project_root): un indexado en curso
    NO bloquea indexar otra clave — la detección de skip es precisa, no un
    mutex global (base y cache son árboles distintos por clave)."""
    otro = proyecto.parent / "proj_otro"
    otro.mkdir()
    (otro / "z.py").write_text("def z():\n    return 0\n")

    dentro = threading.Event()
    liberar = threading.Event()
    _bloquear_primer_chunk(dentro, liberar, monkeypatch)
    t = threading.Thread(
        target=lambda: ci.CodebaseIndexer(project_root="proj", workspace="main").index_project(),
        daemon=True,
    )
    t.start()
    assert dentro.wait(timeout=5)

    # clave distinta: corrió normal y no se colgó
    _, chunks_otro = _indexar(project_root="proj_otro")
    assert chunks_otro > 0

    liberar.set()
    t.join(timeout=10)


def test_borrados_se_purgan_por_lote(proyecto, _provider_listo):
    _indexar()
    (proyecto / "f0.py").unlink()
    (proyecto / "f1.py").unlink()
    indexer = ci.CodebaseIndexer(project_root="proj", workspace="main")
    indexer.index_project()
    # solo quedan los vigentes y sus vectores
    assert {k.split(":")[0] for k, _ in indexer.indexer.documents} == {
        f"f{n}.py" for n in range(2, 8)
    }
    assert indexer.indexer.index.ntotal == 6


def test_purge_del_stamp_purga_tambien_el_hash_cache(proyecto, _provider_listo):
    """RC purge huérfano: el auto_rebuild del stamp borra los artefactos
    FAISS pero NO conoce codebase_index.json (es del CodebaseIndexer). Si el
    hash-cache sobrevive, el reindex "incremental" se salta archivos y el
    índice nuevo queda incompleto — exactamente lo que dejó un índice
    production sin los vectores de README/AGENTS."""
    _indexar()
    cache = proyecto.parent / ".codebase_cache" / "proj"
    assert (cache / "codebase_index.json").exists()

    # simulación EXACTA del purge del stamp: solo artefactos FAISS
    for art in (
        "faiss.index",
        "documents.pkl",
        "index.meta.json",
        "faiss.index.sha256",
        "documents.pkl.sha256",
    ):
        p = cache / art
        if p.exists():
            p.unlink()

    idx = ci.CodebaseIndexer(project_root="proj", workspace="main")
    assert not (cache / "codebase_index.json").exists(), "el hash-cache huérfano debe purgarse"
    assert idx.indexer.index.ntotal == 0

    chunks = idx.index_project()
    assert chunks == 8  # reindex COMPLETO: el incremental no se saltó nada
    assert len(_hashes(proyecto)) == 8


def test_hash_no_marcado_si_el_lote_falla(proyecto, _provider_listo, batch_1, monkeypatch):
    """Embeddings None (modelo local frío) => el hash del archivo NO se
    marca: marcarlo crearía un huérfano (hash sin vectores) que el
    incremental jamás re-encodea."""
    calls = {"n": 0}
    real = EmbeddingProvider._encode_batch_local

    def flaky_batch(texts, kind="passage"):
        calls["n"] += 1
        if calls["n"] == 1:
            return [None for _ in texts]
        return real(texts, kind=kind)

    monkeypatch.setattr(EmbeddingProvider, "_encode_batch_local", staticmethod(flaky_batch))

    _indexar()
    hashes = _hashes(proyecto)
    assert len(hashes) == 7  # el archivo del lote fallido quedó FUERA

    todos = {f"f{n}.py" for n in range(8)}
    faltante = todos - set(hashes)
    assert len(faltante) == 1

    idx2 = ci.CodebaseIndexer(project_root="proj", workspace="main")
    con_vectores = {k.split(":")[0] for k, _ in idx2.indexer.documents}
    assert faltante <= (todos - con_vectores), "el archivo sin hash tampoco tiene vectores"


def test_pct_mide_encode_no_scan(proyecto, _provider_listo, batch_1):
    """El % mide archivos COMPLETOS (skipped + persistidos), no escaneados:
    una barra que mide el scan llega al 99% en segundos y queda congelada
    mientras el ENCODE —el costo real— dura minutos."""
    eventos: list[dict] = []
    _indexar(progress_callback=eventos.append)
    idx_events = [e for e in eventos if e["phase"] == "indexing"]
    pcts = [e["pct"] for e in idx_events]
    assert pcts, "sin eventos de indexado"
    assert pcts[0] < 30, f"el primer evento debe ser el primer lote encodeado: {pcts}"
    assert pcts == sorted(pcts), f"pct no monótono: {pcts}"
    assert pcts[-1] < 100, "el 100% es exclusivo del done"
