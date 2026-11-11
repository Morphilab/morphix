# tools/goal_todo.py
"""Goal · Todo · Plan-mode — herramientas durables con CAS.

Modelo goal/plan/todo con estado durable (integraciones-herramientas)
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
from core.utils import truncar_en_palabra
from orchestration.context import plan_mode_active
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


_BLACKBOARD_OPEN = "<BLACKBOARD>"
_BLACKBOARD_CLOSE = "</BLACKBOARD>"
_BLACKBOARD_MAX_CHARS = 1200
_BLACKBOARD_MAX_TOKENS = 320  # < cap de la capa blackboard (350)
_TODO_MARKERS = {"pending": "⬜", "in_progress": "🟡", "done": "✅", "blocked": "⛔"}
_TODO_ORDER = {"in_progress": 0, "pending": 1, "blocked": 2, "done": 3}


def _sanitize_blackboard_text(text: str) -> str:
    out = str(text or "")
    return out.replace(_BLACKBOARD_OPEN, "[BLACKBOARD]").replace(_BLACKBOARD_CLOSE, "[/BLACKBOARD]")


def _clip_section(lines: list[str], budget: int) -> str:
    """Recorta una sección por chars preservando su encabezado (va primero)."""
    if not lines or budget <= 0:
        return ""
    text = "\n".join(lines)
    if len(text) <= budget:
        return text
    clipped = truncar_en_palabra(text, budget)
    if len(clipped) > budget:
        clipped = clipped[:budget].rstrip()
    return clipped


def blackboard_context(
    workspace: str,
    max_chars: int = _BLACKBOARD_MAX_CHARS,
    max_tokens: int = _BLACKBOARD_MAX_TOKENS,
) -> str:
    """Snapshot compacto de plan/goals/todos del workspace, para el prompt.

    Los tools `goal_*`/`todo_*` y `plan_enter` persistían un plan durable que
    NADIE leía de vuelta (el agente seguía amnésico; de plan-mode solo se
    consumía el flag `active`). Este bloque es el consumidor: plan activo
    (título + pasos), goals no completos y todo vivo (in_progress → pending →
    blocked → done), con caps duros de chars Y tokens y cierre garantizado.

    El plan se inyecta SOLO si el loop aplicó plan-mode (`plan_mode_active`):
    mostrar "Plan activo" a un preset sin `exit_plan_mode` —que además no
    recibe las reglas ni la denegación de escritura— sería una fachada. Y el
    todo tiene presupuesto reservado: es el trabajo abierto que el blackboard
    debe exponer aunque haya un plan largo.
    """
    goals = [
        g for g in goal_get(workspace) if isinstance(g, dict) and g.get("status") != "complete"
    ][:3]
    todo_state = todo_get(workspace)
    items = [it for it in (todo_state.get("items") or []) if isinstance(it, dict)]
    items.sort(key=lambda it: _TODO_ORDER.get(it.get("status", "pending"), 9))
    items = items[:8]
    plan = plan_get(workspace)
    plan_active = bool(plan.get("active")) and plan_mode_active.get()
    plan_steps = [str(s) for s in (plan.get("steps") or [])][:5]

    plan_lines: list[str] = []
    if plan_active:
        title = truncar_en_palabra(
            _sanitize_blackboard_text(str(plan.get("title") or "(sin título)")), 120
        )
        plan_lines.append(f"Plan activo: {title}")
        for idx, step in enumerate(plan_steps, 1):
            plan_lines.append(
                f"  {idx}. {truncar_en_palabra(_sanitize_blackboard_text(step), 120)}"
            )

    goal_lines: list[str] = []
    if goals:
        goal_lines.append("Goals:")
        for goal in goals:
            title = truncar_en_palabra(
                _sanitize_blackboard_text(str(goal.get("title") or "(sin título)")), 120
            )
            goal_lines.append(
                f"- [{goal.get('status', '?')}] {title} "
                f"(ronda {goal.get('rounds', 0)}/{goal.get('max_rounds', '?')}, "
                f"rev {goal.get('revision', 0)})"
            )

    todo_lines: list[str] = []
    if items:
        todo_lines.append(f"Todo (rev {todo_state.get('revision', 0)}):")
        for item in items:
            marker = _TODO_MARKERS.get(item.get("status", "pending"), "⬜")
            text = truncar_en_palabra(_sanitize_blackboard_text(str(item.get("text") or "")), 100)
            todo_lines.append(f"- {marker} {text}")

    if not plan_lines and not goal_lines and not todo_lines:
        return ""

    from orchestration.prompt_budget import count_tokens, fit_marked_block, fit_text

    overhead_chars = len(_BLACKBOARD_OPEN) + len(_BLACKBOARD_CLOSE) + 2
    tags_tokens = count_tokens(f"{_BLACKBOARD_OPEN}\n\n{_BLACKBOARD_CLOSE}")
    available_chars = max(0, max_chars - overhead_chars)
    available_tokens = max(0, max_tokens - tags_tokens - 4)
    if available_chars <= 0 or available_tokens <= 0:
        return ""

    # Reserva del todo en CHARS y en TOKENS: el fit final recorta por tokens
    # desde el final del body, así que un plan/goals token-denso (CJK) se
    # comía la sección del trabajo abierto aunque hubiera reserva en chars.
    todo_reserve_chars = (
        min(available_chars, max(240, int(available_chars * 0.3))) if todo_lines else 0
    )
    todo_reserve_tokens = (
        min(available_tokens, max(110, int(available_tokens * 0.35))) if todo_lines else 0
    )
    head_chars = max(0, available_chars - todo_reserve_chars - (2 if todo_lines else 0))
    head_tokens = max(0, available_tokens - todo_reserve_tokens - (2 if todo_lines else 0))

    plan_text = ""
    if plan_lines and head_chars > 0:
        plan_cap = min(len("\n".join(plan_lines)), max(1, int(head_chars * 0.6)))
        plan_text = _clip_section(plan_lines, plan_cap)
    goals_budget = max(0, head_chars - len(plan_text) - (1 if plan_text else 0))
    goals_text = _clip_section(goal_lines, goals_budget)
    head_text = "\n".join(part for part in (plan_text, goals_text) if part)
    if head_text:
        head_text = fit_text(head_text, max_chars=head_chars, max_tokens=head_tokens)

    todo_text = ""
    if todo_lines:
        used_chars = len(head_text) + (1 if head_text else 0)
        used_tokens = count_tokens(head_text) + (1 if head_text else 0)
        todo_text = fit_text(
            "\n".join(todo_lines),
            max_chars=max(0, available_chars - used_chars),
            max_tokens=max(0, available_tokens - used_tokens),
        )

    body = "\n".join(part for part in (head_text, todo_text) if part)
    return fit_marked_block(
        body,
        _BLACKBOARD_OPEN,
        _BLACKBOARD_CLOSE,
        max_chars=max_chars,
        max_tokens=max_tokens,
    )


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
    plan_mode_active.set(True)
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
    if plan.get("active") is False:
        plan_mode_active.set(False)
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
