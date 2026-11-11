# tests/conftest.py
import os
import socket as _socket
import sys
from pathlib import Path

import pytest

# Tema determinista en la suite: pydantic-settings lee el .env del usuario
# (DARK_MODE=true/false) y desktop/theme.py resuelve el tema ACTIVO al importar
# (COLORS/StyleFactory/INPUT_STYLE son snapshots del tema). Fijar la variable
# ANTES del primer import de core.config (los módulos de test se importan
# después de conftest) mantiene la suite independiente del .env; un export
# explícito del usuario sigue mandando (setdefault no pisa el entorno).
os.environ.setdefault("DARK_MODE", "true")

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Trampa de entorno: run.py carga el .env pero pytest NO. Sin DATABASE_URL en
# el proceso, los tests _pg se saltan EN SILENCIO (skipif lee os.environ) y la
# cobertura baja sin síntoma. Se toma SOLO esa clave — cargar el .env completo
# rompería la hermeticidad (las claves de API del usuario jamás entran a la
# suite; un export explícito sigue mandando).
if "DATABASE_URL" not in os.environ:
    _env_file = project_root / ".env"
    if _env_file.exists():
        for _line in _env_file.read_text(encoding="utf-8").splitlines():
            if _line.startswith("DATABASE_URL="):
                os.environ["DATABASE_URL"] = _line.split("=", 1)[1].strip()
                break

# Enable async tests
pytest_plugins = ["pytest_asyncio"]


def _hard_close_engine(engine) -> None:
    """Cierra a nivel SOCKET las conexiones en standby del pool del engine.

    Soltar la
    referencia del engine sin teardown deja a GC la tarea de cerrar
    conexiones asyncpg cuyo event loop ya murió — asyncpg falla el cierre
    en silencio, el socket queda ABIERTO y PostgreSQL espera eternamente en
    ``ClientRead`` (handshakes zombie: `select
    pg_catalog.version()`/`SET search_path` congelados en pg_stat_activity).
    Los finalizadores C de asyncpg/transport corriendo con loop muerto
    pueden además crash nativo del proceso.

    El cierre por socket es agnóstico del loop: shutdown+close del fd no
    toca asyncio. Al terminar el test no hay nada checked-out en uso — los
    records del queue son standby puro.
    """
    try:
        pool = engine.sync_engine.pool
        queue = getattr(pool, "_pool", None)
        records: list = []
        if queue is not None:
            while True:
                try:
                    records.append(queue.get_nowait())
                except Exception:  # cola vacía o cerrada
                    break
    except Exception:
        return
    for rec in records:
        try:
            apg = rec.dbapi_connection._connection  # AsyncAdapt_… → asyncpg.Connection
            transport = getattr(apg, "_transport", None)
            sock = transport.get_extra_info("socket") if transport else None
            if sock is None:
                continue
            try:
                sock.shutdown(_socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        except Exception:
            continue


@pytest.fixture(autouse=True)
def isolated_db_engine():
    """Drop the global async DB engine after every test.

    pytest-asyncio (auto mode) runs each test in a fresh function-scoped event
    loop. The module-global asyncpg engine in ``core.database`` binds to the
    loop that first created it; reusing it from a later test's loop leaks
    connections ("Connection._cancel was never awaited") and eventually surfaces
    as an ``OSError`` in a late DB-touching test. Resetting the references here
    ensures the engine is recreated fresh inside each test's own loop and never
    crosses loops. No-op for tests that never touch the DB.

    Además del reset de referencias, el pool se desarma a nivel
    socket (``_hard_close_engine``) — el teardown por GC es no-determinista
    y deja handshakes zombie/ClientRead que cuelgan la suite.
    """
    yield
    from core import database

    engine = database._async_engine
    database._async_engine = None
    database._async_session_factory = None
    database._engine_loop = None
    if engine is not None:
        _hard_close_engine(engine)


# Fixtures compartidos útiles:
# mock_llm_call, mock_safe_tool_call, temp_workspace_dir, mock_tools_registry
# fueron eliminados (huérfanos — nunca se usaban).
# Si un test necesita mocks, defínelos inline o en su propio módulo.


@pytest.fixture(autouse=True)
def _reset_global_singletons(monkeypatch):
    """Pivote anti-contaminación: resetear singletons globales tras cada test.

    La suite completa ejecuta ~90 módulos en un proceso compartido; varios
    tests mutan estado global (singleton de MemoryManager vía ``__new__``,
    classvars de EmbeddingProvider/OfflineManager, breakers por proveedor).
    El orden alfabético determina quién hereda basura — fallos que no se
    reproducen por pares. Este fixture garantiza línea base limpia.

    NO toca tools_registry / TOOL_DEFINITIONS (los flujos direct-tool
    validan contra el registro de sesión; los archivos que los mutan ya
    tienen su propio snapshot/restore).
    """
    # 1) MemoryManager: reinicialización completa del singleton
    try:
        from core.memory.manager import MemoryManager

        if MemoryManager._instance is not None:
            MemoryManager._instance._init_memory()
            MemoryManager._instance.active_workspace = None
            # Los monkeypatch de INSTANCIA sobre el singleton dejan bound
            # methods/AsyncMock en __dict__ que sombrean patches de clase de
            # tests posteriores (footgun documentado): se retiran las sombras
            # que colisionan con atributos de la clase.
            _inst = MemoryManager._instance
            for _name in list(_inst.__dict__):
                if _name in vars(MemoryManager):
                    del _inst.__dict__[_name]
    except Exception:
        pass

    # 2) EmbeddingProvider: classvars a estado neutro
    try:
        import threading

        from core.embedding_provider import EmbeddingProvider

        EmbeddingProvider._model = None
        EmbeddingProvider._loading = False
        EmbeddingProvider._load_attempts = 0
        EmbeddingProvider.load_error = None
        EmbeddingProvider._ready = threading.Event()
    except Exception:
        pass

    # 3) OfflineManager: estado de clase sin detectar
    try:
        from llm.offline import OfflineManager

        OfflineManager._state_offline = None
    except Exception:
        pass

    # 3b) Backend de embeddings PINNED a local: el .env del usuario puede
    # activar un backend remoto (API pagada) y pydantic-settings SÍ lee el
    # .env dentro de pytest — sin este pin, toda la suite de embeddings
    # haría llamadas reales al endpoint del usuario.
    try:
        from core.config import settings as _settings

        monkeypatch.setattr(_settings, "embed_backend", "local", raising=False)
        monkeypatch.setattr(_settings, "embed_openai_api_key", "", raising=False)
    except Exception:
        pass

    # 4) Circuit breakers: recrearse limpios para el siguiente test
    try:
        from core.circuit_breaker import CircuitBreakerRegistry

        CircuitBreakerRegistry.reset_all()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _ledger_hermetico(tmp_path, monkeypatch):
    """El ledger durable del LLM escribe SIEMPRE en tmp por test.

    Los tests de fallos del controller (starvation, breaker, streams) hacen
    `bump` vía `RetryLedger.default()/for_workspace()` — sin este pivote
    escriben el ledger durable REAL del workspace y el estado cruza tests y
    sesiones. Los archivos con su propio parche explícito siguen mandando
    (su monkeypatch pisa el de aquí, mismo mecanismo).
    """
    try:
        from core.retry_ledger import RetryLedger

        monkeypatch.setattr(
            RetryLedger,
            "default",
            classmethod(lambda cls: RetryLedger(tmp_path / "ledger.json")),
        )
        monkeypatch.setattr(
            RetryLedger,
            "for_workspace",
            classmethod(lambda cls, ws: RetryLedger(tmp_path / f"ws_{ws}.json")),
        )
    except Exception:
        pass


# ── Flake epoll: hilos asyncio_N del default executor ────────────────────
# Causa raíz (repro minimal): pytest-asyncio cierra los loops con
# `loop.close()` pelado — NUNCA llama `shutdown_default_executor()` — y los
# threads `asyncio_N` del default executor quedan vivos mientras el loop no
# sea recolectado (GC no determinista → flake). `asyncio.run()` NO es la
# fuente: el Runner de 3.12 sí apaga el executor.
# Fix: cerrar el executor (sin esperar) como parte del close de TODO loop
# selector — cubre pytest-asyncio, los loops mintados por run_async y
# cualquier cierre manual.
from asyncio import selector_events as _selector_events
from asyncio import unix_events as _unix_events


def _mk_cierre_con_executor(cls):
    _real = cls.close

    def _cierre(self):
        if not self.is_closed():
            _ex = getattr(self, "_default_executor", None)
            if _ex is not None and not getattr(_ex, "_shutdown", False):
                _ex.shutdown(wait=False)  # el item en curso termina y el hilo sale
        _real(self)

    return _cierre


# el loop concreto (_UnixSelectorEventLoop) y su base definen close(): se
# parchean ambas — la doble pasada es inocua (la 2ª ve _shutdown=True).
for _cls_loop in (_selector_events.BaseSelectorEventLoop, _unix_events._UnixSelectorEventLoop):
    if "close" in vars(_cls_loop):
        _cls_loop.close = _mk_cierre_con_executor(_cls_loop)

# ── Sonda de hilos ──────────────────────────────────────────────────────
# Verificación del fix: registra QUÉ test deja hilos nuevos tras el cierre.
# Con el fix, `asyncio_*` solo puede aparecer durante microsegundos (el item
# en curso del executor); la permanencia era el bug.
_probe_threads: set = set()


def pytest_runtest_setup(item):
    import threading

    _probe_threads.clear()
    _probe_threads.update(t.ident for t in threading.enumerate())


def pytest_runtest_teardown(item, nextitem):
    import threading
    import time as _time

    try:
        nuevos = {t for t in threading.enumerate() if t.ident and t.ident not in _probe_threads}
        if nuevos:
            log_dir = Path(__file__).parent.parent / "logs"
            log_dir.mkdir(exist_ok=True)
            with open(log_dir / "flake-probe.log", "a", encoding="utf-8") as f:
                f.write(
                    f"{_time.strftime('%Y-%m-%d %H:%M:%S')} {item.nodeid} → "
                    + ", ".join(f"{t.name}(daemon={t.daemon})" for t in nuevos)
                    + "\n"
                )
    except Exception:
        pass


def pytest_sessionfinish(session, exitstatus):
    """Censo final: los hilos `asyncio_*` que AÚN viven al terminar la suite.

    La sonda por-test dispara en el instante del teardown y no distingue un
    hilo terminando su último item de uno fugado; el censo al cierre sí — un
    huevo aquí tras el fix del close es la señal del bug.
    """
    import threading
    import time as _time

    try:
        _time.sleep(0.05)  # drena el item en curso de los executors
        vivos = [t.name for t in threading.enumerate() if t.name.startswith("asyncio_")]
        log_dir = Path(__file__).parent.parent / "logs"
        log_dir.mkdir(exist_ok=True)
        with open(log_dir / "flake-probe.log", "a", encoding="utf-8") as f:
            f.write(
                f"{_time.strftime('%Y-%m-%d %H:%M:%S')} CENSO-FINAL "
                f"asyncio_* vivos: {len(vivos)} {sorted(vivos)}\n"
            )
    except Exception:
        pass
