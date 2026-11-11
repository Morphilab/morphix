# tests/test_skills_curator.py — contratos del curator
"""Sin red y sin BD: sidecar JSON + filesystem tmp (patrón test_core_skills).

Contratos del curator:
- walk determinista: active→stale→archived por edad; uso despierta stale.
- solo created_by=agent: bundled/copias de template son islas intocables.
- archive es el máximo: mueve a .archive/ (restorable), jamás borra.
- pinned exento de todo, incluido el archivo manual.
- reviewer inyectable puede vetar ("keep"), nunca forzar.
- should_run_now: flag opt-in + pausa + intervalo + idle.
"""

from datetime import UTC, datetime, timedelta

import pytest

from core import skills as sk
from core import skills_curator as cur


def _mk_skill(root, name, created_by=None, desc="d", body="# B"):
    d = root / name
    d.mkdir(parents=True)
    front = f'name: {name}\ndescription: "{desc}"'
    if created_by:
        front += f"\ncreated_by: {created_by}"
    (d / "SKILL.md").write_text(f"---\n{front}\n---\n\n{body}\n", encoding="utf-8")


@pytest.fixture
def env(tmp_path, monkeypatch):
    g, w = tmp_path / "g", tmp_path / "w"
    g.mkdir()
    w.mkdir()
    monkeypatch.setattr(sk.Paths, "templates_skills_dir", staticmethod(lambda: g))
    monkeypatch.setattr(
        sk.Paths, "workspace_skills_dir", staticmethod(lambda ws: w / ws / "skills")
    )
    monkeypatch.setattr(cur.Paths, "templates_skills_dir", staticmethod(lambda: g))
    monkeypatch.setattr(
        cur.Paths, "workspace_skills_dir", staticmethod(lambda ws: w / ws / "skills")
    )
    return w / "ws1" / "skills"


@pytest.fixture
def cfg(monkeypatch):
    """Acelera el reloj del walk: días en vez de meses."""
    monkeypatch.setattr(cur.settings, "skills_curator_enabled", True)
    monkeypatch.setattr(cur.settings, "skills_curator_stale_after_days", 30)
    monkeypatch.setattr(cur.settings, "skills_curator_archive_after_days", 60)


def _age_record(ws, name, days):
    """Fija la última actividad de una skill a N días atrás."""
    data = cur._load_store(ws)
    data["skills"].setdefault(
        name,
        {
            "use_count": 0,
            "view_count": 0,
            "patch_count": 0,
            "state": "active",
            "pinned": False,
            "created_by": "agent",
        },
    )["last_activity_at"] = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    cur._save_store(ws, data)


def test_record_activity_bumps_counts_and_wakes_stale(env):
    ws = "ws1"
    _mk_skill(env, "s-agent", created_by="agent")
    cur.record_activity(ws, "s-agent", "view")
    cur.record_activity(ws, "s-agent", "use")
    st = cur.curator_status(ws)
    rec = st["records"]["s-agent"]
    assert rec["view_count"] == 1 and rec["use_count"] == 1
    assert rec["state"] == "active"
    # forzar stale y despertar con uso
    data = cur._load_store(ws)
    data["skills"]["s-agent"]["state"] = "stale"
    cur._save_store(ws, data)
    cur.record_activity(ws, "s-agent", "view")
    assert cur._load_store(ws)["skills"]["s-agent"]["state"] == "active"


def test_record_activity_rejects_bad_kind(env):
    with pytest.raises(ValueError):
        cur.record_activity("ws1", "x", "destroy")


def test_load_skill_records_view(env):
    """Integración: core/skills.load_skill alimenta la telemetría."""
    ws = "ws1"
    _mk_skill(env, "hooked", created_by="agent")
    sk.load_skill("hooked", ws)
    rec = cur.curator_status(ws)["records"]["hooked"]
    assert rec["view_count"] == 1
    # skill global sin workspace: no hay sidecar donde registrar
    _mk_skill(cur.Paths.templates_skills_dir(), "pure-global")
    sk.load_skill("pure-global")
    assert "pure-global" not in cur.curator_status(ws)["records"]


async def test_walk_stale_then_archive_by_age(env, cfg):
    ws = "ws1"
    _mk_skill(env, "vieja", created_by="agent")
    _age_record(ws, "vieja", days=45)  # > stale (30), < archive (60)
    res = await cur.apply_automatic_transitions(ws)
    assert res["stale"] == ["vieja"] and res["archived"] == []
    assert (env / "vieja").is_dir()  # stale se queda en su sitio

    _age_record(ws, "vieja", days=90)  # > archive (60)
    res = await cur.apply_automatic_transitions(ws)
    assert res["archived"] == ["vieja"]
    assert not (env / "vieja").exists()
    archived = env / ".archive" / "vieja"
    assert archived.is_dir(), "archive mueve, jamás borra"
    assert cur._load_store(ws)["skills"]["vieja"]["state"] == "archived"
    # discovery ya no la ve (glob excluye dot-dirs)
    assert "vieja" not in {s.name for s in sk.discover_skills(ws)}


async def test_bundled_skills_are_islands(env, cfg):
    ws = "ws1"
    _mk_skill(env, "copiadita")  # sin created_by: copia de template
    _age_record(ws, "copiadita", days=365)
    res = await cur.apply_automatic_transitions(ws)
    assert res["stale"] == [] and res["archived"] == []
    assert (env / "copiadita").is_dir()
    # provenance user tampoco entra al walk automático
    _mk_skill(env, "la-mia", created_by="user")
    _age_record(ws, "la-mia", days=365)
    res = await cur.apply_automatic_transitions(ws)
    assert res["archived"] == []


async def test_pinned_exempt_from_everything(env, cfg):
    ws = "ws1"
    _mk_skill(env, "querida", created_by="agent")
    _age_record(ws, "querida", days=400)
    cur.record_activity(ws, "querida", "view")  # crea el registro
    cur.set_pinned(ws, "querida", True)
    _age_record(ws, "querida", days=400)
    res = await cur.apply_automatic_transitions(ws)
    assert res["stale"] == [] and res["archived"] == []
    with pytest.raises(PermissionError):
        cur.archive_skill(ws, "querida")
    cur.set_pinned(ws, "querida", False)
    res = await cur.apply_automatic_transitions(ws)
    assert res["archived"] == ["querida"]


async def test_reviewer_can_veto_but_not_force(env, cfg):
    ws = "ws1"
    _mk_skill(env, "dudosa", created_by="agent")
    _mk_skill(env, "clara", created_by="agent")
    _age_record(ws, "dudosa", days=90)
    _age_record(ws, "clara", days=90)

    async def fake_reviewer(candidates):
        assert {c["name"] for c in candidates} == {"dudosa", "clara"}
        return {"dudosa": "keep"}  # veto solo sobre dudosa

    res = await cur.apply_automatic_transitions(ws, reviewer=fake_reviewer)
    assert res["archived"] == ["clara"]
    assert res["kept"] == ["dudosa"]
    assert (env / "dudosa").is_dir() and not (env / "clara").exists()


async def test_default_reviewer_fail_open_to_keep(env, cfg, monkeypatch):
    """Sin red/respuesta ilegible: nada se archiva por veto conservador."""

    async def broken_call(**_kwargs):
        raise ConnectionError("sin red")

    import llm

    monkeypatch.setattr(llm.models, "call", broken_call)
    ws = "ws1"
    _mk_skill(env, "candidata", created_by="agent")
    _age_record(ws, "candidata", days=90)
    verdicts = await cur.default_reviewer(
        [{"name": "candidata", "view_count": 0, "use_count": 0, "last_activity_at": None}]
    )
    assert verdicts == {"candidata": "keep"}


async def test_dry_run_moves_nothing(env, cfg):
    ws = "ws1"
    _mk_skill(env, "frozen", created_by="agent")
    _age_record(ws, "frozen", days=90)
    res = await cur.apply_automatic_transitions(ws, dry_run=True)
    assert res["archived"] == ["frozen"]
    assert (env / "frozen").is_dir()
    assert cur._load_store(ws)["last_run_at"] is None  # dry-run no marca corrida


async def test_restore_round_trip(env, cfg):
    ws = "ws1"
    _mk_skill(env, "boomerang", created_by="agent")
    cur.record_activity(ws, "boomerang", "view")
    dest = cur.archive_skill(ws, "boomerang")
    assert dest.is_dir()
    back = cur.restore_skill(ws, "boomerang")
    assert back == env / "boomerang" and back.is_dir()
    assert cur._load_store(ws)["skills"]["boomerang"]["state"] == "active"
    with pytest.raises(FileNotFoundError):
        cur.restore_skill(ws, "inexistente")


def test_should_run_now_gates(env, cfg):
    ws = "ws1"
    assert cur.should_run_now(ws) is True  # nunca corrido → toca
    data = cur._load_store(ws)
    data["last_run_at"] = datetime.now(UTC).isoformat()
    cur._save_store(ws, data)
    assert cur.should_run_now(ws) is False  # intervalo no transcurrido
    data = cur._load_store(ws)
    data["last_run_at"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    cur._save_store(ws, data)
    assert cur.should_run_now(ws) is True
    cur.set_paused(ws, True)
    assert cur.should_run_now(ws) is False  # pausa gana
    cur.set_paused(ws, False)


def test_should_run_now_disabled_flag(env, monkeypatch):
    monkeypatch.setattr(cur.settings, "skills_curator_enabled", False)
    assert cur.should_run_now("ws1") is False


def test_status_classifies_provenance(env):
    ws = "ws1"
    _mk_skill(env, "del-agente", created_by="agent")
    _mk_skill(env, "template-copy")
    st = cur.curator_status(ws)
    assert st["on_disk"]["del-agente"] == "agent"
    assert st["on_disk"]["template-copy"] == "template"
    assert st["config"]["stale_after_days"] == 30
