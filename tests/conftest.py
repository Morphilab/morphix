# tests/conftest.py
import sys
from pathlib import Path

import pytest

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Enable async tests
pytest_plugins = ["pytest_asyncio"]


@pytest.fixture(autouse=True)
def _isolate_db_engine():
    """Drop the global async DB engine after every test.

    pytest-asyncio (auto mode) runs each test in a fresh function-scoped event
    loop. The module-global asyncpg engine in ``core.database`` binds to the
    loop that first created it; reusing it from a later test's loop leaks
    connections ("Connection._cancel was never awaited") and eventually surfaces
    as an ``OSError`` in a late DB-touching test. Resetting the references here
    ensures the engine is recreated fresh inside each test's own loop and never
    crosses loops. No-op for tests that never touch the DB.
    """
    yield
    from core import database

    database._async_engine = None
    database._async_session_factory = None
    database._engine_loop = None


# Fixtures compartidos útiles:
# - mock_llm_call, mock_safe_tool_call, temp_workspace_dir, mock_tools_registry
#   fueron eliminados (huérfanos — nunca se usaban).
#   Si un test necesita mocks, defínelos inline o en su propio módulo.
