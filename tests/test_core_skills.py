"""Skills procedimentales: rutas, descubrimiento con precedencia y bootstrap."""

import pytest

from core import skills as sk


def _mk_skill(root, name, desc="d", body="# B"):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f'---\nname: {name}\ndescription: "{desc}"\n---\n\n{body}\n', encoding="utf-8"
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    g, w = tmp_path / "g", tmp_path / "w"
    g.mkdir()
    w.mkdir()
    monkeypatch.setattr(sk.Paths, "templates_skills_dir", staticmethod(lambda: g))
    monkeypatch.setattr(
        sk.Paths, "workspace_skills_dir", staticmethod(lambda ws: w / ws / "skills")
    )
    return g, w / "ws1" / "skills"


def test_paths_skills_dirs_exist_as_methods():
    from core.path_resolver import PathResolver

    assert PathResolver.templates_skills_dir().name == "skills"
    assert PathResolver.templates_skills_dir().parent.name == "templates"
    ws = PathResolver.workspace_skills_dir("main")
    assert ws.parts[-2:] == ("main", "skills")


def test_discover_and_workspace_precedence(env):
    g, wdir = env
    _mk_skill(g, "a")
    _mk_skill(g, "b", desc="global_b")
    wdir.mkdir(parents=True)
    _mk_skill(wdir, "b", desc="ws_b")
    found = {s.name: s for s in sk.discover_skills("ws1")}
    assert set(found) == {"a", "b"}
    assert found["b"].source == "workspace"
    assert found["b"].description == "ws_b"


def test_load_skill_precedence_and_missing(env):
    g, wdir = env
    _mk_skill(g, "a", body="GLOBAL")
    wdir.mkdir(parents=True)
    _mk_skill(wdir, "a", body="WS")
    assert "WS" in sk.load_skill("a", "ws1").body
    with pytest.raises(sk.SkillNotFoundError):
        sk.load_skill("zzz", "ws1")


def test_load_skill_reports_workspace_origin(env):
    g, wdir = env
    _mk_skill(g, "dup")
    wdir.mkdir(parents=True)
    _mk_skill(wdir, "dup")
    assert sk.load_skill("dup", "ws1").origin == "workspace"


def test_load_skill_global_origin(env):
    g, _ = env
    _mk_skill(g, "only_global")
    assert sk.load_skill("only_global").origin == "global"


def test_bootstrap_warns_on_workspace_overrides(env):
    from core.skills_approval import approve_skill

    g, wdir = env
    _mk_skill(g, "g1")
    wdir.mkdir(parents=True)
    _mk_skill(wdir, "local_ws")
    # las skills workspace solo se inyectan si están aprobadas.
    approve_skill("ws1", "local_ws", "# B")
    text = sk.build_bootstrap("ws1")
    assert "LOCALES" in text
    assert "local_ws" in text
    assert "workspace" in text
    assert text.index("SEGURIDAD") < text.index("Skills disponibles:")


def test_bootstrap_no_warning_without_locals(env):
    g, _ = env
    _mk_skill(g, "g1")
    text = sk.build_bootstrap(None)
    assert "LOCALES" not in text


def test_malformed_frontmatter_skipped(env):
    g, _ = env
    bad = g / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("sin frontmatter", encoding="utf-8")
    _mk_skill(g, "ok")
    assert [s.name for s in sk.discover_skills(None)] == ["ok"]


def test_bootstrap_lists_skills(env):
    g, _ = env
    _mk_skill(g, "brainstorming", desc="explora diseño")
    text = sk.build_bootstrap(None)
    assert text.startswith("<SKILLS>")
    assert "- **brainstorming** (global): explora diseño" in text
    assert "load_skill" in text


def test_apply_skills_bootstrap_disabled_is_noop():
    ctx, tools = sk.apply_skills_bootstrap("CTX", ["file_manager"], enabled=False, workspace="w")
    assert ctx == "CTX"
    assert tools == ["file_manager"]


def test_apply_skills_bootstrap_enabled_expands_allowlist(monkeypatch):
    monkeypatch.setattr(
        "core.skills.discover_skills",
        lambda ws: [sk.SkillSummary("writing-plans", "planes", "global")],
    )
    ctx, tools = sk.apply_skills_bootstrap("CTX", ["file_manager"], enabled=True, workspace="w")
    assert ctx.startswith("<SKILLS>")
    assert "- **writing-plans** (global): planes" in ctx
    assert tools == ["file_manager", "load_skill"]


def test_apply_skills_bootstrap_none_allowlist_becomes_list(monkeypatch):
    monkeypatch.setattr("core.skills.discover_skills", lambda ws: [])
    _ctx, tools = sk.apply_skills_bootstrap("CTX", None, enabled=True, workspace=None)
    assert tools == ["load_skill"]


def test_apply_skills_bootstrap_no_duplicate_load_skill(monkeypatch):
    monkeypatch.setattr("core.skills.discover_skills", lambda ws: [])
    _ctx, tools = sk.apply_skills_bootstrap("CTX", ["load_skill"], enabled=True, workspace=None)
    assert tools.count("load_skill") == 1


def test_workspace_bootstrap_copies_skills(tmp_path, monkeypatch):
    """Tarea 5: el switch copia templates/skills/<n> al workspace (aditivo)."""
    from core.workspaces import Workspaces

    templates_skills = tmp_path / "tpl_skills"
    ws_skills = tmp_path / "ws" / "main" / "skills"
    _mk_skill(templates_skills, "demo", desc="demo skill")

    # Segunda corrida idempotente: modificar origen no re-copia
    Workspaces._bootstrap_workspace_skills(ws_skills, templates_skills)
    assert (ws_skills / "demo" / "SKILL.md").exists()

    _mk_skill(templates_skills, "otra")
    Workspaces._bootstrap_workspace_skills(ws_skills, templates_skills)
    assert (ws_skills / "otra" / "SKILL.md").exists()
    assert (ws_skills / "demo" / "SKILL.md").exists()
