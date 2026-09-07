# tests/conftest.py
import socket as _socket
import sys
from pathlib import Path

import pytest

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

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
def _reset_global_singletons():
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

    # 4) Circuit breakers: recrearse limpios para el siguiente test
    try:
        from core.circuit_breaker import CircuitBreakerRegistry

        CircuitBreakerRegistry.reset_all()
    except Exception:
        pass
