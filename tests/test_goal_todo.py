# tests/test_goal_todo.py
"""Tests de Goal · Todo · Plan-mode (CAS, SM, whole-list, atomic persist)."""

import pytest

from core.path_resolver import paths
from tools.goal_todo import (
    _goal_path,
    _plan_path,
    _todo_path,
    exit_plan_mode,
    goal_create,
    goal_get,
    goal_round,
    goal_update,
    plan_enter,
    plan_get,
    todo_get,
    todo_write,
)
from tools.registry import tools_registry

WS = "goal_todo_test_ws"


def _clean():
    for p in list(paths.memory_dir(WS).rglob("*")):
        if p.is_file():
            p.unlink()


def setup_function():
    _clean()


def teardown_function():
    _clean()


# ── Goal: ciclo básico ──────────────────────────────────────────────────


def test_goal_create_get():
    g = goal_create("Escribir la documentación", WS, "docs del módulo", max_rounds=3)
    assert g["status"] == "active"
    assert g["revision"] == 1
    assert g["rounds"] == 0
    got = goal_get(WS, g["id"])
    assert isinstance(got, dict)
    assert got["id"] == g["id"]
    listing = goal_get(WS)
    assert isinstance(listing, list) and len(listing) == 1


def test_goal_update_cas_ok():
    g = goal_create("T", WS)
    updated = goal_update(WS, g["id"], expected_revision=1, status="paused")
    assert updated["status"] == "paused"
    assert updated["revision"] == 2


def test_goal_update_cas_conflict():
    g = goal_create("T", WS)
    with pytest.raises(ValueError, match="CAS_CONFLICT"):
        goal_update(WS, g["id"], expected_revision=99, status="paused")


def test_goal_illegal_transition():
    g = goal_create("T", WS)
    # active→paused legal
    goal_update(WS, g["id"], expected_revision=1, status="paused")
    # paused→complete legal
    goal_update(WS, g["id"], expected_revision=2, status="complete")
    # complete es terminal
    with pytest.raises(ValueError, match="Transición ilegal"):
        goal_update(WS, g["id"], expected_revision=3, status="paused")


def test_goal_blocked_requires_human_to_reactivate():
    g = goal_create("T", WS)
    goal_update(WS, g["id"], expected_revision=1, status="blocked")
    with pytest.raises(ValueError, match="REQUIRES_HUMAN"):
        goal_update(WS, g["id"], expected_revision=2, status="active")
    back = goal_update(WS, g["id"], expected_revision=2, status="active", authority="human")
    assert back["status"] == "active"
    assert back["authority"] == "human"


def test_goal_round_budget_blocks():
    g = goal_create("T", WS, max_rounds=2)
    g = goal_round(WS, g["id"], expected_revision=1)
    assert g["rounds"] == 1 and g["status"] == "active"
    g = goal_round(WS, g["id"], expected_revision=2)
    assert g["status"] == "active"  # rounds == max aún permitido
    g = goal_round(WS, g["id"], expected_revision=3)
    assert g["status"] == "blocked", "superar max_rounds debe bloquear"
    assert g["rounds"] == 3


def test_goal_round_blocked_authority_goal_round():
    """Al bloquear por max_rounds, authority debe ser 'goal_round'."""
    g = goal_create("T", WS, max_rounds=1)
    g = goal_round(WS, g["id"], expected_revision=1)
    assert g["status"] == "active"
    g = goal_round(WS, g["id"], expected_revision=2)
    assert g["status"] == "blocked"
    assert g["authority"] == "goal_round", "autoridad debe ser goal_round al bloquear por rounds"


def test_goal_round_done_rejected():
    g = goal_create("T", WS)
    goal_update(WS, g["id"], expected_revision=1, status="complete")
    with pytest.raises(ValueError, match="GOAL_DONE"):
        goal_round(WS, g["id"], expected_revision=2)


def test_goal_missing():
    with pytest.raises(KeyError):
        goal_get(WS, "no-existe")


# ── Todo: whole-list + guard de paralelismo ─────────────────────────────


def test_todo_write_read():
    state = todo_write(
        WS,
        [
            {"text": "A", "status": "pending"},
            {"text": "B", "status": "done"},
        ],
    )
    assert state["revision"] == 1
    assert len(state["items"]) == 2
    got = todo_get(WS)
    assert got["revision"] == 1
    assert {i["text"] for i in got["items"]} == {"A", "B"}


def test_todo_replaces_whole_list():
    todo_write(WS, [{"text": "A"}])
    state = todo_write(WS, [{"text": "B"}, {"text": "C"}], expected_revision=1)
    assert state["revision"] == 2
    assert {i["text"] for i in state["items"]} == {"B", "C"}


def test_todo_parallel_in_progress_rejected():
    with pytest.raises(ValueError, match="TODO_PARALLEL_IN_PROGRESS"):
        todo_write(
            WS,
            [
                {"text": "A", "status": "in_progress"},
                {"text": "B", "status": "in_progress"},
            ],
        )
    state = todo_write(
        WS,
        [
            {"text": "A", "status": "in_progress"},
            {"text": "B", "status": "in_progress"},
        ],
        allow_parallel_in_progress=True,
    )
    assert state["revision"] == 1


def test_todo_cas_conflict():
    todo_write(WS, [{"text": "A"}])
    with pytest.raises(ValueError, match="CAS_CONFLICT"):
        todo_write(WS, [{"text": "Z"}], expected_revision=7)


def test_todo_invalid_status():
    with pytest.raises(ValueError, match="Estado de item inválido"):
        todo_write(WS, [{"text": "A", "status": "wip"}])


# ── Plan-mode ───────────────────────────────────────────────────────────


def test_plan_enter_and_exit_approve():
    plan_enter(WS, "Refactorizar", ["p1", "p2"])
    plan = plan_get(WS)
    assert plan["active"] is True
    assert plan["steps"] == ["p1", "p2"]
    plan = exit_plan_mode(WS, "Approve", expected_revision=1)
    assert plan["active"] is False
    assert plan["decision"] == "approve"
    assert plan_get(WS)["active"] is False


def test_plan_keep_planning():
    plan_enter(WS, "P", ["p1"])
    plan = exit_plan_mode(WS, "Keep-planning", expected_revision=1)
    assert plan["active"] is True
    assert plan["decision"] == "keep-planning"


def test_exit_without_active_plan_rejected():
    with pytest.raises(ValueError, match="PLAN_INACTIVE"):
        exit_plan_mode(WS, "Approve")


def test_plan_invalid_decision():
    plan_enter(WS, "P", [])
    with pytest.raises(ValueError, match="Decisión inválida"):
        exit_plan_mode(WS, "Talvez")


# ── Persistencia atómica / ficheros ─────────────────────────────────────


def test_state_persisted_to_disk():
    g = goal_create("T", WS)
    assert _goal_path(WS, g["id"]).is_file()
    todo_write(WS, [{"text": "A"}])
    assert _todo_path(WS).is_file()
    plan_enter(WS, "P", ["x"])
    assert _plan_path(WS).is_file()


def test_no_temp_files_left():
    goal_create("T", WS)
    assert not list(paths.memory_dir(WS).rglob("*.tmp"))


# ── Registro en el registry ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "goal_create",
        "goal_get",
        "goal_update",
        "goal_round",
        "todo_write",
        "todo_get",
        "plan_mode",
        "exit_plan_mode",
    ],
)
def test_tool_registered(name):
    assert tools_registry.get_tool(name) is not None


# ── workspace sin validar → path traversal vía MCP ────────────────


def test_workspace_traversal_rechazado():
    """workspace con traversal debe ValueError, sin crear dirs fuera."""
    import pytest as _pytest

    for evil in ("../../x", "/etc", "a/b", "Upper", "con espacio", ""):
        with _pytest.raises(ValueError, match="workspace"):
            goal_create("t", evil, "")
        with _pytest.raises(ValueError, match="workspace"):
            goal_get(evil)
        with _pytest.raises(ValueError, match="workspace"):
            todo_write(evil, [])
        with _pytest.raises(ValueError, match="workspace"):
            plan_enter(evil, "p", ["s1"])
    assert not (paths.memory_dir("").resolve().parent / "x").exists()


def test_workspace_valido_aceptado():
    """El patrón canónico [a-z][a-z0-9_]* sigue funcionando."""
    goal_create("ok-goal", "goal_todo_h4_ws", "")
    data = todo_get("goal_todo_h4_ws")
    assert isinstance(data, dict)
