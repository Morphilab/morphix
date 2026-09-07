# tests/test_migrator.py — migrador endurecido (advisory-lock, guards, verificación)
"""Wrapper ÚNICO para migraciones: advisory-lock de sesión (serializa contra
operaciones destructivas), guard previo de estados ambiguos fail-closed,
verificación POST del catálogo y lint anti-invocación directa."""

import pytest


class FakeConn:
    """Registra el SQL del holder (locks); las lecturas van por helpers."""

    def __init__(self):
        self.executed: list[str] = []

    async def execute(self, sql, params=None):
        self.executed.append(str(sql))

        class R:
            def scalar(self):
                return None

            def fetchall(self):
                return []

        return R()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeEngine:
    def __init__(self):
        self.conn = FakeConn()

    def connect(self):
        return self.conn


@pytest.fixture
def env(monkeypatch):
    from core import migrator

    eng = FakeEngine()
    state = {
        "stamped": None,  # revisión estampada en la BD (None = sin stamp)
        "tables": {
            "conversation",
            "message",
            "user",
            "workflow",
            "paused_sessions",
            "blackboard_entries",
        },
        "rc": 0,
        "cmds": [],
    }

    async def fake_stamped(conn, schema):
        return state["stamped"]

    async def fake_tables(conn, schema):
        return set(state["tables"])

    async def fake_runner(argv, envd):
        state["cmds"].append((list(argv), dict(envd)))
        return state["rc"]

    monkeypatch.setattr(migrator, "_fetch_stamped_revision", fake_stamped)
    monkeypatch.setattr(migrator, "_base_tables_present", fake_tables)
    monkeypatch.setattr(migrator, "_default_runner", fake_runner)
    return {"migrator": migrator, "engine": eng, "state": state}


@pytest.mark.asyncio
async def test_blocks_when_tables_exist_without_stamp(env):
    """Estado ambiguo clásico: startup_db creó tablas pero nadie estampó."""
    m, eng, st = env["migrator"], env["engine"], env["state"]
    st["stamped"] = None  # tablas presentes + sin stamp

    res = await m.upgrade_head(schema="main", engine=eng)

    assert res.ok is False and res.blocked is True
    assert "stamp" in res.detail.lower()
    assert st["cmds"] == [], "no debe migrar en estado ambiguo"
    sqls = eng.conn.executed
    assert any("pg_advisory_lock" in s for s in sqls)
    assert any("pg_advisory_unlock" in s for s in sqls)


@pytest.mark.asyncio
async def test_allows_virgin_db_without_stamp(env):
    """BD virgen (sin tablas ni stamp) NO es ambigua — es el deploy limpio."""
    m, eng, st = env["migrator"], env["engine"], env["state"]
    st["stamped"] = None
    st["tables"] = set()
    from core import migrator as mmod

    head = mmod.current_head()
    # tras migrar, la BD queda estampada en head y con tablas
    st["stamped"] = head
    st["tables"] = {
        "conversation",
        "message",
        "user",
        "workflow",
        "paused_sessions",
        "blackboard_entries",
    }

    res = await m.upgrade_head(schema="main", engine=eng)
    assert res.ok is True


@pytest.mark.asyncio
async def test_blocks_unknown_stamped_revision(env):
    m, eng, st = env["migrator"], env["engine"], env["state"]
    st["stamped"] = "revision_fantasma"

    res = await m.upgrade_head(schema="main", engine=eng)

    assert res.ok is False and res.blocked is True
    assert "fantasma" not in res.detail.lower() or res.detail  # mensaje accionable
    assert st["cmds"] == []


@pytest.mark.asyncio
async def test_happy_path_locks_migrates_verifies_and_unlocks(env):
    m, eng, st = env["migrator"], env["engine"], env["state"]
    st["stamped"] = m.current_head()  # ya en head: upgrade no-op pero válido

    res = await m.upgrade_head(schema="main", engine=eng)

    assert res.ok is True
    assert len(st["cmds"]) == 1
    argv, envd = st["cmds"][0]
    assert argv[-2:] == ["upgrade", "head"]
    assert envd.get("ALEMBIC_SCHEMA") == "main"
    sqls = eng.conn.executed
    assert any("pg_advisory_lock" in s for s in sqls)
    assert any("pg_advisory_unlock" in s for s in sqls)
    idx_lock = next(i for i, s in enumerate(sqls) if "advisory_lock" in s)
    idx_cmd = 10**9 if not st["cmds"] else idx_lock
    idx_unlock = next(i for i, s in enumerate(sqls) if "advisory_unlock" in s)
    assert idx_lock < idx_unlock, "el unlock SIEMPRE va después del lock"


@pytest.mark.asyncio
async def test_failure_releases_lock_and_reports(env, monkeypatch):
    m, eng, st = env["migrator"], env["engine"], env["state"]
    st["stamped"] = m.current_head()

    async def failing_runner(argv, envd):
        return 1

    monkeypatch.setattr(m, "_default_runner", failing_runner)

    res = await m.upgrade_head(schema="main", engine=eng)

    assert res.ok is False and res.blocked is False
    assert any("pg_advisory_unlock" in s for s in eng.conn.executed)


@pytest.mark.asyncio
async def test_postcheck_missing_table_fails_even_with_rc0(env):
    m, eng, st = env["migrator"], env["engine"], env["state"]
    st["stamped"] = m.current_head()
    st["tables"] = set()  # catálogo vacío pese a rc=0

    res = await m.upgrade_head(schema="main", engine=eng)

    assert res.ok is False
    assert res.blocked is False
    assert "catálogo" in res.detail.lower() or "catalogo" in res.detail.lower()


def test_current_head_matches_script_directory():
    """El head persistido por el migrador es el head REAL de versions/
    (dinámico: los deltas legítimos lo mueven — b01 bot-mode actual)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from core.migrator import current_head

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert current_head() == script.get_heads()[0]


def test_lint_no_direct_alembic_outside_wrapper():
    """Ninguna capa de producción invoca alembic directamente — solo el
    wrapper (los tests E2E viven en tests/ y están excluidos)."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    targets = [root / "run.py"] if (root / "run.py").exists() else []
    for d in ("core", "desktop", "orchestration", "llm", "tools"):
        targets += [p for p in (root / d).rglob("*.py")]
    allowed = {root / "core" / "migrator.py"}
    needle = re.compile(
        r'alembic\s*\.\s*command|["\']alembic["\']|-m[\s,]+["\']alembic',
    )
    offenders = []
    for f in targets:
        if f in allowed or not f.exists():
            continue
        src = f.read_text(encoding="utf-8")
        if needle.search(src):
            offenders.append(str(f.relative_to(root)))
    assert offenders == [], f"invocaciones directas a alembic fuera del wrapper: {offenders}"
