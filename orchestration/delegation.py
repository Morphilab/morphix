# orchestration/delegation.py — delegación ad-hoc a subagentes
"""``delegate_task``: subagentes con contexto aislado, reutilizando el loop.

Diseño propio, adaptado al modelo asyncio EN PROCESO de Morphix — sin
replicar lo que ya existe:
el fan-out DECLARATIVO ya vive en el DSL (``dsl/engine._loop_parallel``
con semáforo); lo que faltaba era la llamada AD-HOC que el modelo hace
en caliente. Los "children" de v1 son corrutinas del mismo proceso que
corren ``execute_agent_loop`` con allowlist restringida por ROL — por lo
que kernels/handoff-de-procesos/heartbeat NO aplican aquí
(sin procesos OS); el contrato que sí portamos:

- **Roles**: ``leaf`` (default) corre sin ``delegate_task`` ni
  ``ask_clarification`` ni ``memory_inspector``; ``orchestrator`` conserva
  la delegación, gated por ``delegation_orchestrator_enabled`` y acotado
  por ``delegation_max_spawn_depth`` (default 2).
- **Batch con units por grupo**: tasks que comparten ``group`` se
  reportan juntas; el semáforo (``delegation_max_concurrent_children``)
  acota hijos CONCURRENTES por llamada.
- **output_schema nunca descarta**: un fallo de validación tras UN
  reintento conserva el texto crudo con ``schema_valid: false`` +
  ``schema_errors`` (contrato: jamás tirar el trabajo del hijo).
- **Stop fan-out**: ``interrupt_for_session(session_id)`` cancela las
  delegaciones activas de esa sesión; el summary del interrumpido es el
  último texto real del hijo.
- **Background**: devuelve ``delegation_id`` al instante; el resultado se
  recupera con :func:`get_delegation` y una cola de completions lista
  para el drenaje de cualquier superficie (patrón bots_wake).

El ejecutor es INYECTABLE: ``default_child_executor`` envuelve
``execute_agent_loop`` (import lazy); los tests inyectan fakes sin red.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from core.config import settings

logger = logging.getLogger(__name__)

ROLE_LEAF = "leaf"
ROLE_ORCHESTRATOR = "orchestrator"

# Allowlist que el rol leaf NO recibe (leaf pierde
# delegate/clarify/memory/send_message/cronjob — equivalencia Morphix).
LEAF_EXCLUDED_TOOLS = frozenset({"delegate_task", "ask_clarification", "memory_inspector"})

ChildSpec = dict[str, Any]
ChildExecutor = Callable[[ChildSpec], Awaitable[dict[str, Any]]]


@dataclass
class DelegationConfig:
    """Configuración inyectable (patrón AgentLoopConfig)."""

    max_concurrent_children: int = 3
    child_timeout_seconds: float = 600.0
    orchestrator_enabled: bool = True
    max_spawn_depth: int = 2

    @classmethod
    def from_settings(cls) -> DelegationConfig:
        return cls(
            max_concurrent_children=settings.delegation_max_concurrent_children,
            child_timeout_seconds=settings.delegation_child_timeout_seconds,
            orchestrator_enabled=settings.delegation_orchestrator_enabled,
            max_spawn_depth=settings.delegation_max_spawn_depth,
        )


# Profundidad de anidamiento: el padre corre a 0; cada hijo la ve +1.
_spawn_depth: ContextVar[int] = ContextVar("delegation_spawn_depth", default=0)

# Registro de delegaciones activas por sesión (stop fan-out).
_ACTIVE: dict[str, set[asyncio.Task]] = {}

# Registro de delegaciones emitidas (background + polling).
_REGISTRY: dict[str, dict[str, Any]] = {}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    return f"d{secrets.token_hex(4)}"


# ── Validación de output_schema (subconjunto JSON schema) ───────────────────


_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list, tuple),
    "null": (type(None),),
}


def validate_output_schema(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Valida un subconjunto de JSON schema: type/required/properties/items.

    Devuelve lista de errores legibles; vacía = válido. Sin dependencias
    externas — el contrato exige veredicto, no una librería.
    """
    errors: list[str] = []
    if not isinstance(schema, dict) or not schema:
        return errors  # sin schema no hay nada que validar

    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        allowed: tuple[type, ...] = tuple(t for name in types for t in _TYPE_MAP.get(str(name), ()))
        if allowed and not isinstance(value, allowed):
            found = type(value).__name__
            errors.append(f"{path}: espera {expected}, encuentra {found}")
            return errors
        if "integer" in types and isinstance(value, bool):
            errors.append(f"{path}: espera integer, encuentra bool")
            return errors

    if isinstance(value, dict):
        for req in schema.get("required", []) or []:
            if req not in value:
                errors.append(f"{path}: falta la clave requerida '{req}'")
        properties = schema.get("properties", {}) or {}
        if properties:
            unknown_ok = schema.get("additionalProperties", True) is not False
            if not unknown_ok:
                for key in value:
                    if key not in properties:
                        errors.append(f"{path}: clave inesperada '{key}'")
            for key, sub in properties.items():
                if key in value:
                    errors.extend(validate_output_schema(value[key], sub, f"{path}.{key}"))

    if isinstance(value, (list, tuple)) and "items" in schema:
        for i, item in enumerate(value):
            errors.extend(validate_output_schema(item, schema["items"], f"{path}[{i}]"))
    return errors


# ── Ejecutor por defecto (import lazy del loop real) ───────────────────────


def default_child_executor(
    *,
    agent_type: str | None,
    workspace: str | None,
    project_root: str | None,
    allowed_tools: list[str] | None,
):
    """Envuelve execute_agent_loop; el spec lleva goal/context/role del hijo."""

    async def _run(spec: ChildSpec) -> dict[str, Any]:
        from orchestration.loop import execute_agent_loop

        task_text = str(spec.get("goal") or "")
        context = str(spec.get("context") or "")
        return await execute_agent_loop(
            task=task_text,
            agent_type=spec.get("agent_type") or agent_type,
            history=[],
            allowed_tools=spec.get("allowed_tools", allowed_tools),
            workspace=spec.get("workspace") or workspace,
            project_root=spec.get("project_root") or project_root,
            extra_context=context,
        )

    return _run


def role_allowlist(role: str, allowed_tools: list[str] | None) -> list[str] | None:
    """Allowlist del hijo según rol: leaf pierde delegación/clarify/memoria."""
    base = list(allowed_tools) if allowed_tools is not None else None
    if role == ROLE_LEAF and base is not None:
        return [t for t in base if t not in LEAF_EXCLUDED_TOOLS]
    return base


# ── Núcleo ──────────────────────────────────────────────────────────────────


def _normalize_tasks(goal: str | None, tasks: list | None) -> list[dict[str, Any]]:
    if tasks:
        if not isinstance(tasks, list):
            raise ValueError("tasks debe ser una lista")
        specs: list[dict[str, Any]] = []
        for i, item in enumerate(tasks):
            if isinstance(item, str):
                specs.append({"goal": item, "group": None})
            elif isinstance(item, dict) and item.get("goal"):
                specs.append(
                    {
                        "goal": str(item["goal"]),
                        "group": item.get("group"),
                        "context": item.get("context", ""),
                        "agent_type": item.get("agent_type"),
                    }
                )
            else:
                raise ValueError(f"tasks[{i}] inválida: falta 'goal'")
        return specs
    if not goal:
        raise ValueError("falta 'goal' (o provee 'tasks' para batch)")
    return [{"goal": str(goal), "group": None}]


def _units_of(specs: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Tasks con mismo group se reportan juntas; sin grupo, cada una sola."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for spec in specs:
        key = str(spec["group"]) if spec.get("group") else f"__solo__{id(spec)}"
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(spec)
    return [grouped[k] for k in order]


async def _run_child(
    spec: dict[str, Any],
    *,
    executor: ChildExecutor,
    cfg: DelegationConfig,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    """Un hijo: timeout + fallo aislado; el resultado NUNCA se descarta."""
    entry: dict[str, Any] = {"goal": spec["goal"], "status": "failed", "result": ""}
    async with semaphore:
        token = _spawn_depth.set(_spawn_depth.get() + 1)
        try:
            raw = await asyncio.wait_for(executor(spec), timeout=cfg.child_timeout_seconds)
        except TimeoutError:
            entry["error"] = f"timeout tras {cfg.child_timeout_seconds}s"
        except asyncio.CancelledError:
            entry["status"] = "interrupted"
            entry["error"] = "cancelado por interrupt_for_session"
            raise
        except Exception as exc:  # aislamiento: una entrada failed, seguir
            entry["error"] = f"{type(exc).__name__}: {exc}"[:300]
        else:
            entry["status"] = str(raw.get("status") or "completed")
            entry["result"] = str(raw.get("result") or "")
            entry["iterations"] = int(raw.get("iterations") or 0)
            entry["actions_taken"] = int(raw.get("actions_taken") or 0)
            entry["tokens_used_call"] = int(raw.get("tokens_used_call") or 0)
            if raw.get("reasoning"):
                entry["reasoning"] = str(raw["reasoning"])
        finally:
            _spawn_depth.reset(token)
    return entry


async def _validate_with_retry(
    entry: dict[str, Any],
    spec: dict[str, Any],
    *,
    output_schema: dict | None,
    executor: ChildExecutor,
    cfg: DelegationConfig,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    """Contrato: un reintento; luego crudo + schema_valid=false."""
    if output_schema is None or entry["status"] != "completed":
        return entry
    errors = validate_output_schema(_maybe_json(entry["result"]), output_schema)
    if not errors:
        entry["schema_valid"] = True
        entry["schema_errors"] = []
        return entry
    logger.info("output_schema miss (%s) — reintento único", errors[:2])
    retry_spec = dict(spec)
    retry_spec["context"] = (
        str(spec.get("context") or "")
        + "\n\n[IMPORTANT] Your previous answer did not conform to the required "
        "output schema. Answer AGAIN conforming EXACTLY to: "
        f"{output_schema}"
    )
    retry = await _run_child(retry_spec, executor=executor, cfg=cfg, semaphore=semaphore)
    if retry["status"] == "completed":
        retry_errors = validate_output_schema(_maybe_json(retry["result"]), output_schema)
        if not retry_errors:
            retry["schema_valid"] = True
            return retry
        retry["schema_valid"] = False
        retry["schema_errors"] = retry_errors
        return retry
    return entry  # el reintento falló peor: queda el original
    # en ningún caso se descarta el texto del hijo


def _maybe_json(result: str) -> Any:
    import json

    text = (result or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text  # valida como string si el schema lo permite


async def delegate_task(
    goal: str | None = None,
    tasks: list | None = None,
    *,
    agent_type: str | None = None,
    context: str = "",
    background: bool = False,
    output_schema: dict | None = None,
    role: str = ROLE_LEAF,
    allowed_tools: list[str] | None = None,
    workspace: str | None = None,
    project_root: str | None = None,
    session_id: str | None = None,
    executor: ChildExecutor | None = None,
    config: DelegationConfig | None = None,
) -> dict[str, Any]:
    """Delegación single/batch con roles, schema y stop fan-out."""
    cfg = config or DelegationConfig.from_settings()
    run = executor or default_child_executor(
        agent_type=agent_type,
        workspace=workspace,
        project_root=project_root,
        allowed_tools=allowed_tools,
    )
    role = role if role in (ROLE_LEAF, ROLE_ORCHESTRATOR) else ROLE_LEAF
    child_allowlist = role_allowlist(role, allowed_tools)
    if role == ROLE_ORCHESTRATOR and not cfg.orchestrator_enabled:
        return {
            "success": False,
            "error": "orchestrator deshabilitado (delegation_orchestrator_enabled)",
        }
    if _spawn_depth.get() >= cfg.max_spawn_depth:
        return {
            "success": False,
            "error": f"profundidad máxima alcanzada ({cfg.max_spawn_depth})",
        }

    specs = _normalize_tasks(goal, tasks)
    for spec in specs:
        spec.setdefault("context", context)
        spec.setdefault("agent_type", None)
        spec["allowed_tools"] = child_allowlist
        spec["workspace"] = workspace
        spec["project_root"] = project_root

    delegation_id = _new_id()
    record: dict[str, Any] = {
        "delegation_id": delegation_id,
        "status": "running",
        "created_at": _now_iso(),
        "units": [],
    }
    _REGISTRY[delegation_id] = record

    async def _dispatch() -> dict[str, Any]:
        semaphore = asyncio.Semaphore(cfg.max_concurrent_children)
        units = _units_of(specs)
        fan: list[asyncio.Task] = []
        if session_id:
            _ACTIVE.setdefault(session_id, set())

        async def _run_unit(unit: list[dict[str, Any]]) -> dict[str, Any]:
            # El await DENTRO de la comprehension que alimenta el gather
            # evaluaba cada hijo antes de crear la corrutina siguiente: el
            # grupo corría SECUENCIAL pese al docstring. Primero las
            # corrutinas, luego el gather — la concurrencia la acota el
            # semáforo que _run_child adquiere internamente.
            async def _run_and_validate(s: dict[str, Any]):
                result = await _run_child(s, executor=run, cfg=cfg, semaphore=semaphore)
                return await _validate_with_retry(
                    result,
                    s,
                    output_schema=output_schema,
                    executor=run,
                    cfg=cfg,
                    semaphore=semaphore,
                )

            entries = await asyncio.gather(*[_run_and_validate(s) for s in unit])
            statuses = {e["status"] for e in entries}
            unit_status = (
                "interrupted"
                if "interrupted" in statuses
                else "completed" if statuses == {"completed"} else "partial"
            )
            return {
                "group": unit[0].get("group"),
                "goals": [s["goal"] for s in unit],
                "status": unit_status,
                "results": list(entries),
            }

        try:
            for unit in units:
                task = asyncio.create_task(_run_unit(unit))
                if session_id:
                    _ACTIVE[session_id].add(task)
                fan.append(task)
            unit_results = await asyncio.gather(*fan)
        except asyncio.CancelledError:
            record["status"] = "interrupted"
            raise
        finally:
            if session_id:
                _ACTIVE.get(session_id, set()).difference_update(fan)

        record["units"] = unit_results
        statuses = {u["status"] for u in unit_results}
        record["status"] = (
            "interrupted"
            if "interrupted" in statuses
            else "completed" if statuses == {"completed"} else "partial"
        )
        record["finished_at"] = _now_iso()
        record["success"] = record["status"] == "completed"
        return record

    if background:
        runner = asyncio.create_task(_dispatch())
        if session_id:
            _ACTIVE.setdefault(session_id, set()).add(runner)
        return {
            "success": True,
            "delegation_id": delegation_id,
            "status": "running",
            "note": "recupera el resultado con get_delegation(delegation_id)",
        }

    try:
        return await _dispatch()
    except asyncio.CancelledError:
        record["status"] = "interrupted"
        record["success"] = False
        return record


def get_delegation(delegation_id: str) -> dict[str, Any]:
    """Snapshot de una delegación (running o terminada)."""
    record = _REGISTRY.get(delegation_id)
    if record is None:
        return {"success": False, "error": f"delegación desconocida: {delegation_id}"}
    out = dict(record)
    out["success"] = record.get("status") == "completed"
    return out


async def interrupt_for_session(session_id: str) -> int:
    """Cancela las delegaciones activas de la sesión (stop fan-out).

    Devuelve cuántas corrutinas canceló. El summary del interrumpido es
    el último texto real del hijo (el entry ya carries result si llegó).
    """
    active = _ACTIVE.get(session_id)
    if not active:
        return 0
    doomed = [t for t in active if not t.done()]
    for task in doomed:
        task.cancel()
    if doomed:
        await asyncio.gather(*doomed, return_exceptions=True)
    _ACTIVE[session_id] = {t for t in active if not t.done()}
    return len(doomed)
