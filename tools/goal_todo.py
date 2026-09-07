# tools/goal_todo.py
"""Goal · Todo · Plan-mode — herramientas durables con CAS.

Adaptación del modelo goal/plan/todo de deepseek-harness (integraciones-herramientas)
a morphix. Estado durable en JSON por workspace (memory/<ws>/), con revisión CAS
en toda mutación, escritura atómica y máquina de estados de goal validada.

- Goal SM: active → paused|blocked|complete; paused → active|blocked;
  blocked → active(solo autoridad humana)|complete; complete es terminal.
  Rounds: cada goal_round incrementa; superar max_rounds ⇒ blocked.
- Todo: reemplazo whole-list; >1 in_progress rechazado salvo allow_parallel.
- Plan-mode: plan activo con pasos; exit_plan_mode requiere decisión Approve.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from core.path_resolver import paths
from tools.registry import tools_registry

logger = logging.getLogger(__name__)

GOAL_VALID_STATUSES = {"active", "paused", "blocked", "complete"}
_TODO_VALID_STATUSES = {"pending", "in_progress", "done", "blocked"}
_GOAL_TRANSITIONS: dict[str, set[str]] = {
    "active": {"paused", "blocked", "complete"},
    "paused": {"active", "blocked", "complete"},
    "blocked": {"active", "complete"},
    "complete": set(),
}
_MAX_ROUNDS_DEFAULT = 5

_locks: dict[str, threading.RLock] = {}
_locks_lock = threading.RLock()


def _get_lock(workspace: str) -> threading.RLock:
    """Obtiene (o crea) el lock para un workspace específico."""
    with _locks_lock:
        if workspace not in _locks:
            _locks[workspace] = threading.RLock()
        return _locks[workspace]


# ── Persistencia atómica ────────────────────────────────────────────────


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    """Escritura atómica: temp same-dir + os.replace + fsync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning("goal_todo: estado corrupto en %s — tratado como vacío", path)
        return None


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# ── Helpers de ruta ─────────────────────────────────────────────────────

_WS_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _check_workspace(workspace: str) -> None:
    """El parámetro workspace es expuesto al LLM/MCP — sin
    validación, `workspace='../../x'` crearía directorios y escribiría
    fuera del workspace (paridad con el guard de file_manager)."""
    if not isinstance(workspace, str) or not _WS_RE.match(workspace):
        raise ValueError(
            f"workspace inválido: '{workspace}'. "
            "Solo minúsculas, números y guiones bajos (patrón [a-z][a-z0-9_]*)."
        )


def _goals_dir(workspace: str) -> Path:
    _check_workspace(workspace)
    return paths.memory_dir(workspace) / "goals"


def _goal_path(workspace: str, goal_id: str) -> Path:
    # Sanitizar id contra path traversal
    safe = Path(goal_id).name
    return _goals_dir(workspace) / f"{safe}.json"


def _todo_path(workspace: str) -> Path:
    _check_workspace(workspace)
    return paths.memory_dir(workspace) / "todo.json"


def _plan_path(workspace: str) -> Path:
    _check_workspace(workspace)
    return paths.memory_dir(workspace) / "plan.json"


# ── Goal ────────────────────────────────────────────────────────────────


def goal_create(
    title: str,
    workspace: str,
    description: str = "",
    max_rounds: int = _MAX_ROUNDS_DEFAULT,
) -> dict[str, Any]:
    """Crea un goal activo con revision=1. authority=goal_round."""
    with _get_lock(workspace):
        gid = str(uuid.uuid4())
        goal = {
            "id": gid,
            "workspace": workspace,
            "title": title,
            "description": description,
            "status": "active",
            "revision": 1,
            "rounds": 0,
            "max_rounds": max(1, int(max_rounds)),
            "authority": "goal_round",
            "created_at": _now(),
            "updated_at": _now(),
        }
        _atomic_write(_goal_path(workspace, gid), goal)
        return goal


def goal_get(workspace: str, goal_id: str | None = None) -> list[dict[str, Any]] | dict[str, Any]:
    """Retorna un goal o el listado completo (ordenado por updated_at desc)."""
    with _get_lock(workspace):
        if goal_id:
            data = _load_json(_goal_path(workspace, goal_id))
            if data is None:
                raise KeyError(f"Goal no encontrado: {goal_id}")
            return data
        gdir = _goals_dir(workspace)
        if not gdir.is_dir():
            return []
        goals = []
        for p in gdir.glob("*.json"):
            data = _load_json(p)
            if data:
                goals.append(data)
        goals.sort(key=lambda g: g.get("updated_at", ""), reverse=True)
        return goals


def goal_update(
    workspace: str,
    goal_id: str,
    expected_revision: int,
    status: str | None = None,
    title: str | None = None,
    description: str | None = None,
    authority: str | None = None,
) -> dict[str, Any]:
    """Actualiza un goal con CAS. Valida transiciones de estado."""
    with _get_lock(workspace):
        goal = _load_json(_goal_path(workspace, goal_id))
        if goal is None:
            raise KeyError(f"Goal no encontrado: {goal_id}")
        if goal.get("revision") != expected_revision:
            raise ValueError(
                f"CAS_CONFLICT: revisión esperada {expected_revision}, actual {goal.get('revision')}"
            )

        reactivated_by_human = False
        if status is not None and status != goal["status"]:
            if status not in GOAL_VALID_STATUSES:
                raise ValueError(f"Estado inválido: {status}")
            allowed = _GOAL_TRANSITIONS.get(goal["status"], set())
            if status not in allowed:
                raise ValueError(f"Transición ilegal: {goal['status']} → {status}")
            if status == "active" and goal["status"] == "blocked":
                # Desbloquear es decisión humana
                if authority != "human" and goal.get("authority") != "human":
                    raise ValueError(
                        "TRANSITION_REQUIRES_HUMAN: blocked → active exige authority='human'"
                    )
                reactivated_by_human = True

        if title is not None:
            goal["title"] = title
        if description is not None:
            goal["description"] = description
        if status is not None:
            goal["status"] = status
            if status in ("complete", "blocked"):
                goal["authority"] = authority or goal.get("authority") or "goal_round"
            elif reactivated_by_human:
                # Desbloqueo con autoridad humana queda registrado
                goal["authority"] = authority or "human"

        goal["revision"] += 1
        goal["updated_at"] = _now()
        _atomic_write(_goal_path(workspace, goal_id), goal)
        return goal


def goal_round(workspace: str, goal_id: str, expected_revision: int) -> dict[str, Any]:
    """Incrementa el contador de rounds (presupuesto del goal). Superar max ⇒ blocked."""
    with _get_lock(workspace):
        goal = _load_json(_goal_path(workspace, goal_id))
        if goal is None:
            raise KeyError(f"Goal no encontrado: {goal_id}")
        if goal.get("revision") != expected_revision:
            raise ValueError(
                f"CAS_CONFLICT: revisión esperada {expected_revision}, actual {goal.get('revision')}"
            )
        if goal["status"] == "complete":
            raise ValueError("GOAL_DONE: no se puede avanzar rounds de un goal completado")

        goal["rounds"] += 1
        if goal["rounds"] > goal.get("max_rounds", _MAX_ROUNDS_DEFAULT):
            goal["status"] = "blocked"
            goal["authority"] = "goal_round"
        goal["revision"] += 1
        goal["updated_at"] = _now()
        _atomic_write(_goal_path(workspace, goal_id), goal)
        return goal


def _format_goal(goal: dict[str, Any]) -> str:
    return (
        f"🎯 Goal {goal['id'][:8]} [{goal['status']}]\n"
        f"   Título: {goal['title']}\n"
        f"   Descripción: {goal.get('description') or '(sin descripción)'}\n"
        f"   Rounds: {goal['rounds']}/{goal['max_rounds']} · autoridad: {goal['authority']}\n"
        f"   Revisión (CAS): {goal['revision']}"
    )


# ── Todo ────────────────────────────────────────────────────────────────


def todo_write(
    workspace: str,
    items: list[dict[str, Any]],
    expected_revision: int | None = None,
    allow_parallel_in_progress: bool = False,
) -> dict[str, Any]:
    """Reemplazo whole-list con CAS. >1 in_progress rechazado salvo allow."""
    with _get_lock(workspace):
        path = _todo_path(workspace)
        current = _load_json(path)
        rev = (current or {}).get("revision", 0)
        if expected_revision is not None and rev != expected_revision:
            raise ValueError(f"CAS_CONFLICT: revisión esperada {expected_revision}, actual {rev}")

        normalized: list[dict[str, Any]] = []
        for it in items:
            text = str(it.get("text", "")).strip()
            if not text:
                continue
            status = it.get("status", "pending")
            if status not in _TODO_VALID_STATUSES:
                raise ValueError(f"Estado de item inválido: {status}")
            normalized.append(
                {
                    "id": it.get("id") or str(uuid.uuid4()),
                    "text": text,
                    "status": status,
                }
            )

        in_progress = sum(1 for it in normalized if it["status"] == "in_progress")
        if in_progress > 1 and not allow_parallel_in_progress:
            raise ValueError(
                "TODO_PARALLEL_IN_PROGRESS: >1 item in_progress requiere "
                "allow_parallel_in_progress=true"
            )

        state = {
            "workspace": workspace,
            "revision": rev + 1,
            "updated_at": _now(),
            "items": normalized,
        }
        _atomic_write(path, state)
        return state


def todo_get(workspace: str) -> dict[str, Any]:
    """Retorna el todo actual (o vacío)."""
    with _get_lock(workspace):
        state = _load_json(_todo_path(workspace)) or {
            "workspace": workspace,
            "revision": 0,
            "updated_at": _now(),
            "items": [],
        }
        return state


def _format_todo(state: dict[str, Any]) -> str:
    lines = [f"📋 Todo (revisión {state.get('revision', 0)}):"]
    items = state.get("items", [])
    if not items:
        lines.append("   (vacío)")
    for it in items:
        marker = {
            "pending": "⬜",
            "in_progress": "🟡",
            "done": "✅",
            "blocked": "⛔",
        }.get(it.get("status", "pending"), "⬜")
        lines.append(f"   {marker} {it['text']} [{it.get('status')}]")
    return "\n".join(lines)


# ── Plan-mode ───────────────────────────────────────────────────────────


def plan_enter(workspace: str, title: str, steps: list[str]) -> dict[str, Any]:
    """Activa (o reemplaza) el plan activo. CAS vía revisión incremental."""
    with _get_lock(workspace):
        path = _plan_path(workspace)
        current = _load_json(path)
        rev = (current or {}).get("revision", 0)
        plan = {
            "workspace": workspace,
            "active": True,
            "title": title,
            "steps": [str(s) for s in steps if str(s).strip()],
            "revision": rev + 1,
            "updated_at": _now(),
        }
        _atomic_write(path, plan)
        return plan


def exit_plan_mode(
    workspace: str, decision: str = "Keep-planning", expected_revision: int | None = None
) -> dict[str, Any]:
    """Salida de plan-mode. Approve ⇒ desactiva el plan; Keep-planning ⇒ continúa."""
    with _get_lock(workspace):
        path = _plan_path(workspace)
        plan = _load_json(path)
        if plan is None or not plan.get("active"):
            raise ValueError("PLAN_INACTIVE: no hay plan activo para salir")
        rev = plan.get("revision", 0)
        if expected_revision is not None and rev != expected_revision:
            raise ValueError(f"CAS_CONFLICT: revisión esperada {expected_revision}, actual {rev}")
        decision = (decision or "Keep-planning").strip()
        if decision.lower() == "approve":
            plan["active"] = False
            plan["decision"] = "approve"
        elif decision.lower() in ("keep-planning", "keep"):
            plan["decision"] = "keep-planning"
        else:
            raise ValueError(f"Decisión inválida: {decision} (use Approve | Keep-planning)")
        plan["revision"] += 1
        plan["updated_at"] = _now()
        _atomic_write(path, plan)
        return plan


def plan_get(workspace: str) -> dict[str, Any]:
    with _get_lock(workspace):
        plan = _load_json(_plan_path(workspace))
        if plan is None:
            return {"active": False, "steps": []}
        return plan


def _format_plan(plan: dict[str, Any]) -> str:
    if not plan.get("active"):
        return "📐 No hay plan activo."
    lines = [f"📐 Plan activo: {plan.get('title', '')} (revisión {plan.get('revision', 0)})"]
    for i, s in enumerate(plan.get("steps", []), 1):
        lines.append(f"   {i}. {s}")
    return "\n".join(lines)


# ── Tools (handlers registrados) ────────────────────────────────────────


async def _goal_create_tool(
    title: str = "",
    workspace: str | None = None,
    description: str = "",
    max_rounds: int = _MAX_ROUNDS_DEFAULT,
    **kwargs,
) -> str:
    from core.config import settings

    ws = workspace or settings.active_workspace
    if not title.strip():
        return "❌ goal_create requiere 'title'."
    goal = await asyncio.to_thread(goal_create, title, ws, description, max_rounds)
    return f"Goal creado.\n{_format_goal(goal)}"


async def _goal_get_tool(goal_id: str = "", workspace: str | None = None, **kwargs) -> str:
    from core.config import settings

    ws = workspace or settings.active_workspace
    try:
        result = await asyncio.to_thread(goal_get, ws, goal_id or None)
    except KeyError as e:
        return f"❌ {e}"
    if isinstance(result, list):
        if not result:
            return "🎯 No hay goals en este workspace."
        return "\n\n".join(_format_goal(g) for g in result)
    return _format_goal(result)


async def _goal_update_tool(
    goal_id: str = "",
    expected_revision: int = 0,
    workspace: str | None = None,
    status: str = "",
    title: str = "",
    description: str = "",
    authority: str = "",
    **kwargs,
) -> str:
    from core.config import settings

    ws = workspace or settings.active_workspace
    if not goal_id:
        return "❌ goal_update requiere 'goal_id'."
    try:
        goal = await asyncio.to_thread(
            goal_update,
            ws,
            goal_id,
            expected_revision,
            status or None,
            title or None,
            description or None,
            authority or None,
        )
    except (KeyError, ValueError) as e:
        return f"❌ {e}"
    return f"Goal actualizado.\n{_format_goal(goal)}"


async def _goal_round_tool(
    goal_id: str = "", expected_revision: int = 0, workspace: str | None = None, **kwargs
) -> str:
    from core.config import settings

    ws = workspace or settings.active_workspace
    if not goal_id:
        return "❌ goal_round requiere 'goal_id'."
    try:
        goal = await asyncio.to_thread(goal_round, ws, goal_id, expected_revision)
    except (KeyError, ValueError) as e:
        return f"❌ {e}"
    return f"Round registrado.\n{_format_goal(goal)}"


async def _todo_write_tool(
    items: list | None = None,
    workspace: str | None = None,
    expected_revision: int | None = None,
    allow_parallel_in_progress: bool = False,
    **kwargs,
) -> str:
    from core.config import settings

    ws = workspace or settings.active_workspace
    try:
        state = await asyncio.to_thread(
            todo_write, ws, items or [], expected_revision, allow_parallel_in_progress
        )
    except ValueError as e:
        return f"❌ {e}"
    return _format_todo(state)


async def _todo_get_tool(workspace: str | None = None, **kwargs) -> str:
    from core.config import settings

    ws = workspace or settings.active_workspace
    state = await asyncio.to_thread(todo_get, ws)
    return _format_todo(state)


async def _plan_mode_tool(
    title: str = "", steps: list | None = None, workspace: str | None = None, **kwargs
) -> str:
    from core.config import settings

    ws = workspace or settings.active_workspace
    if not title.strip():
        return "❌ plan_mode (enter) requiere 'title'."
    plan = await asyncio.to_thread(plan_enter, ws, title, steps or [])
    return f"Plan activado.\n{_format_plan(plan)}"


async def _exit_plan_mode_tool(
    decision: str = "Keep-planning",
    expected_revision: int | None = None,
    workspace: str | None = None,
    **kwargs,
) -> str:
    from core.config import settings

    ws = workspace or settings.active_workspace
    try:
        plan = await asyncio.to_thread(exit_plan_mode, ws, decision, expected_revision)
    except ValueError as e:
        return f"❌ {e}"
    return f"Plan-mode: {plan.get('decision')}.\n{_format_plan(plan)}"


# ── Registro ────────────────────────────────────────────────────────────

for _name, _fn in {
    "goal_create": _goal_create_tool,
    "goal_get": _goal_get_tool,
    "goal_update": _goal_update_tool,
    "goal_round": _goal_round_tool,
    "todo_write": _todo_write_tool,
    "todo_get": _todo_get_tool,
    "plan_mode": _plan_mode_tool,
    "exit_plan_mode": _exit_plan_mode_tool,
}.items():
    tools_registry.register(_name)(_fn)
