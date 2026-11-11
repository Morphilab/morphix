# orchestration/dsl/run_state.py
"""Estado de entrega de un run DSL.

El motor no confía en el "completed" que declara el agente: cada subtarea
tiene un status derivado de evidencia (archivos + verificación), y el estado
viaja en el snapshot para el resume y en el journal para el post-mortem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from core.utils import truncar_en_palabra
from orchestration.prompt_budget import fit_marked_block

_MAX_TAIL = 500
_MAX_NOTES = 3
_MAX_TRIED = 50
_MAX_REPORT_BODY = 4000
_MAX_MEMORY_BLOCK = 2000
_MAX_MEMORY_BLOCK_TOKENS = 760  # < cap de la capa run_state (800)
_MEMORY_OPEN = "<RUN_STATE>"
_MEMORY_CLOSE = "</RUN_STATE>"
_MEMORY_RULES = (
    "Rules: no repitas una acción idéntica sin cambiar algo (archivo, comando o "
    "enfoque); no declares completado sin el artefacto/test que lo pruebe."
)


@dataclass
class Verification:
    """Resultado medido de una verificación determinista."""

    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    passed: bool | None = None
    counts: dict[str, Any] = field(default_factory=dict)
    output_tail: str = ""

    def to_dict(self, *, public: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "tool": self.tool,
            "args": dict(self.args),
            "passed": self.passed,
            "counts": dict(self.counts),
        }
        if not public:
            data["output_tail"] = (self.output_tail or "")[:_MAX_TAIL]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Verification | None:
        if not data:
            return None
        return cls(
            tool=str(data.get("tool") or ""),
            args=dict(data.get("args") or {}),
            passed=data.get("passed"),
            counts=dict(data.get("counts") or {}),
            output_tail=str(data.get("output_tail") or ""),
        )


@dataclass
class PlanItem:
    """Una unidad del plan: fase, subtarea o paso de texto."""

    kind: str = "subtask"  # phase | subtask | text
    id: str = ""
    goal: str = ""
    path: str | None = None
    status: str = "pending"  # pending|running|delivered|failed|unmeasured|skipped|blocked
    attempts: int = 0
    tokens: int = 0
    iterations: int = 0
    files_written: list[str] = field(default_factory=list)
    verification: Verification | None = None
    satisfied_by: str | None = None
    notes: list[str] = field(default_factory=list)
    last_error: str | None = None

    def to_dict(self, *, public: bool = False) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "goal": self.goal,
            "path": self.path,
            "status": self.status,
            "attempts": self.attempts,
            "tokens": self.tokens,
            "iterations": self.iterations,
            "files_written": list(self.files_written),
            "verification": (
                self.verification.to_dict(public=public) if self.verification is not None else None
            ),
            "satisfied_by": self.satisfied_by,
            "notes": list(self.notes[:_MAX_NOTES]),
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanItem:
        return cls(
            kind=str(data.get("kind") or "subtask"),
            id=str(data.get("id") or ""),
            goal=str(data.get("goal") or ""),
            path=data.get("path"),
            status=str(data.get("status") or "pending"),
            attempts=int(data.get("attempts") or 0),
            tokens=int(data.get("tokens") or 0),
            iterations=int(data.get("iterations") or 0),
            files_written=list(data.get("files_written") or []),
            verification=Verification.from_dict(data.get("verification")),
            satisfied_by=data.get("satisfied_by"),
            notes=list(data.get("notes") or []),
            last_error=data.get("last_error"),
        )


@dataclass
class ReplanRecord:
    at: str = ""
    trigger: str = ""
    reason: str = ""
    action: str = ""
    before: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "trigger": self.trigger,
            "reason": self.reason,
            "action": self.action,
            "before": list(self.before),
            "after": list(self.after),
            "tokens": self.tokens,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReplanRecord:
        return cls(
            at=str(data.get("at") or ""),
            trigger=str(data.get("trigger") or ""),
            reason=str(data.get("reason") or ""),
            action=str(data.get("action") or ""),
            before=list(data.get("before") or []),
            after=list(data.get("after") or []),
            tokens=int(data.get("tokens") or 0),
        )


@dataclass
class SkipItem:
    item_id: str = ""
    reason: str = ""
    satisfied_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"item_id": self.item_id, "reason": self.reason, "satisfied_by": self.satisfied_by}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkipItem:
        return cls(
            item_id=str(data.get("item_id") or ""),
            reason=str(data.get("reason") or ""),
            satisfied_by=data.get("satisfied_by"),
        )


class ReplanItem(BaseModel):
    """Una subtarea propuesta por el replanner."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=500)
    produces: list[str] = Field(default_factory=list)


class ReplanDecision(BaseModel):
    """Decisión del replanner — validada por el motor antes de aplicarse."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["retry", "replace", "skip", "stop"]
    items: list[ReplanItem] = Field(default_factory=list)
    skipped: list[SkipItem] = Field(default_factory=list)
    reason: str = Field(default="", max_length=500)


@dataclass
class BudgetState:
    limit: int = 0
    spent: int = 0
    reserve_final: int = 0
    reserve_replan: int = 0
    # Parte de reserve_final que un step de cierre ya consumió: la reserva
    # es one-shot; sin esto el mismo step (o un resume) podía volver a
    # abrirla y el reporte mostraba spent > limit sin explicar por qué.
    reserve_final_spent: int = 0

    def remaining(self) -> int:
        return self.limit - self.spent

    def available_for_work(self) -> int:
        return (
            self.limit
            - self.spent
            - max(0, self.reserve_final - self.reserve_final_spent)
            - self.reserve_replan
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "limit": self.limit,
            "spent": self.spent,
            "reserve_final": self.reserve_final,
            "reserve_replan": self.reserve_replan,
            "reserve_final_spent": self.reserve_final_spent,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> BudgetState:
        data = data or {}
        return cls(
            limit=int(data.get("limit") or 0),
            spent=int(data.get("spent") or 0),
            reserve_final=int(data.get("reserve_final") or 0),
            reserve_replan=int(data.get("reserve_replan") or 0),
            reserve_final_spent=int(data.get("reserve_final_spent") or 0),
        )


@dataclass
class RunMemory:
    """Intentos por item (firma → count).

    El consumidor es `RunState.render_memory_block`: los intentos viajan al
    prompt de cada paso como "Already tried: …" para cortar la repetición
    idéntica entre steps amnésicos.
    """

    tried: dict[str, dict[str, int]] = field(default_factory=dict)
    artifacts: dict[str, list[str]] = field(default_factory=dict)
    notes: dict[str, list[str]] = field(default_factory=dict)

    def record_tool(self, item_id: str, signature: str) -> None:
        bucket = self.tried.setdefault(item_id or "-", {})
        if signature in bucket:
            bucket[signature] += 1
        elif len(bucket) < _MAX_TRIED:
            bucket[signature] = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "tried": {k: dict(v) for k, v in self.tried.items()},
            "artifacts": {k: list(v) for k, v in self.artifacts.items()},
            "notes": {k: list(v[:_MAX_NOTES]) for k, v in self.notes.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RunMemory:
        data = data or {}
        return cls(
            tried={
                str(k): {str(s): int(c) for s, c in (v or {}).items()}
                for k, v in (data.get("tried") or {}).items()
            },
            artifacts={str(k): list(v or []) for k, v in (data.get("artifacts") or {}).items()},
            notes={str(k): list(v or []) for k, v in (data.get("notes") or {}).items()},
        )


@dataclass
class RunState:
    run_id: str = ""
    goal: str = ""
    plan: list[PlanItem] = field(default_factory=list)
    budget: BudgetState = field(default_factory=BudgetState)
    replans: list[ReplanRecord] = field(default_factory=list)
    memory: RunMemory = field(default_factory=RunMemory)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "goal": self.goal,
            "plan": [p.to_dict() for p in self.plan],
            "budget": self.budget.to_dict(),
            "replans": [r.to_dict() for r in self.replans],
            "memory": self.memory.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RunState:
        data = data or {}
        return cls(
            run_id=str(data.get("run_id") or ""),
            goal=str(data.get("goal") or ""),
            plan=[PlanItem.from_dict(p) for p in (data.get("plan") or [])],
            budget=BudgetState.from_dict(data.get("budget")),
            replans=[ReplanRecord.from_dict(r) for r in (data.get("replans") or [])],
            memory=RunMemory.from_dict(data.get("memory")),
        )

    def public(self) -> dict[str, Any]:
        """Vista para GUI/journal: sin tails crudos ni notas largas."""
        return {
            "run_id": self.run_id,
            "goal": self.goal,
            "plan": [p.to_dict(public=True) for p in self.plan],
            "budget": self.budget.to_dict(),
            "replans": [r.to_dict() for r in self.replans],
            "memory": self.memory.to_dict(),
        }

    def find(self, *, path: str | None = None, item_id: str | None = None) -> PlanItem | None:
        for item in self.plan:
            if path is not None and item.path == path:
                return item
            if item_id is not None and item.id == item_id:
                return item
        return None

    def find_for_path(self, qpath: str) -> PlanItem | None:
        """Item dueño de un qpath cualificado (el prefijo más específico)."""
        best: PlanItem | None = None
        for item in self.plan:
            path = item.path
            if not path:
                continue
            if (qpath == path or qpath.startswith(path + "/") or qpath.startswith(path + "#")) and (
                best is None or len(path) > len(best.path or "")
            ):
                best = item
        return best

    def counts(self) -> dict[str, int]:
        """Conteo por subtarea (delivery), no por paso emitido."""
        return _count_status(self.plan)

    def render_memory_block(
        self,
        *,
        current_path: str = "",
        attempt: int = 1,
        max_chars: int = _MAX_MEMORY_BLOCK,
        max_tokens: int = _MAX_MEMORY_BLOCK_TOKENS,
    ) -> str:
        """Bloque compacto `<RUN_STATE>` inyectado en cada `call_agent`.

        El agente no comparte historial entre steps: este bloque es el puente
        de estado (plan, archivos, última verificación, intentos y budget).
        Se recorta duro por chars Y por tokens (el presupuesto de su capa es
        en tokens y `clip_to_budget` recorta por líneas: sin esto el cierre
        podía quedar fuera del prompt); el marcador de cierre SIEMPRE queda y
        los textos que produce un LLM no pueden inyectar uno propio.
        """
        if not self.plan and not self.goal.strip() and not self.budget.limit:
            return ""

        lines: list[str] = []
        if self.goal.strip():
            lines.append(f"Goal: {truncar_en_palabra(_sanitize_block_text(self.goal), 200)}")

        # Las subtareas son el grano de entrega del run; un workflow sin
        # decompose/loop no tiene subtasks y su plan son las fases. Mostrar
        # AMBOS duplica el loop como "fase" ruidosa.
        items = [p for p in self.plan if p.kind == "subtask"] or [
            p for p in self.plan if p.kind == "phase"
        ]
        if items:
            lines.append("Plan:")
            for idx, item in enumerate(items[:12], 1):
                icon = _ICONS.get(item.status, "•")
                goal = truncar_en_palabra(_sanitize_block_text(item.goal or item.id), 80)
                lines.append(f"{idx}. {icon} {goal}")
            if len(items) > 12:
                lines.append(f"… +{len(items) - 12} ítem(s)")

        current = self.find_for_path(current_path) if current_path else None
        if current is not None:
            goal = truncar_en_palabra(_sanitize_block_text(current.goal or current.id), 80)
            lines.append(f"Current: {goal} (intento {max(1, int(attempt))})")

        files: list[str] = []
        for item in self.plan:
            for path in item.files_written:
                if path and path not in files:
                    files.append(path)
        if files:
            shown = _sanitize_block_text(", ".join(files[-8:]))
            lines.append(f"Files: {truncar_en_palabra(shown, 240)}")

        last = next((p for p in reversed(self.plan) if p.verification is not None), None)
        if last is not None and last.verification is not None:
            verification = last.verification
            icon = "✅" if verification.passed else ("❌" if verification.passed is False else "⚠️")
            detail = f"Last verification: {verification.tool or 'medición'} {icon}"
            counts = _format_counts(verification.counts)
            if counts:
                detail += f" ({counts})"
            tail = (verification.output_tail or "").strip().splitlines()
            if tail:
                detail += f" — {truncar_en_palabra(_sanitize_block_text(tail[0]), 120)}"
            lines.append(detail)

        # Errores abiertos y última decisión del replanner: la tesis pide que
        # el blackboard lleve "decisiones y errores abiertos", no solo el plan.
        open_errors = [
            p for p in self.plan if p.last_error and p.status in ("failed", "unmeasured", "blocked")
        ]
        if open_errors:
            detail_errors = "; ".join(
                f"{p.id}: {truncar_en_palabra(_sanitize_block_text(p.last_error or ''), 100)}"
                for p in open_errors[:3]
            )
            lines.append(f"Open errors: {detail_errors}")
        if self.replans:
            last_replan = self.replans[-1]
            reason = truncar_en_palabra(_sanitize_block_text(last_replan.reason), 80)
            lines.append(f"Replan: {len(self.replans)} (último: {last_replan.action} — {reason})")

        if current is not None:
            bucket = self.memory.tried.get(current.id) or {}
            top = sorted(bucket.items(), key=lambda kv: (-kv[1], kv[0]))[:4]
            if top:
                tried = "; ".join(
                    f"{truncar_en_palabra(_sanitize_block_text(sig), 80)} ×{count}"
                    for sig, count in top
                )
                lines.append(f"Already tried: {tried}")

        if self.budget.limit:
            available = max(0, self.budget.available_for_work())
            lines.append(
                f"Remaining budget: ~{available // 1000}k de "
                f"{max(0, self.budget.limit) // 1000}k tokens"
            )
        lines.append(_MEMORY_RULES)

        body = "\n".join(lines)
        return fit_marked_block(
            body,
            _MEMORY_OPEN,
            _MEMORY_CLOSE,
            max_chars=max_chars,
            max_tokens=max_tokens,
        )


def _sanitize_block_text(text: str) -> str:
    """Neutraliza marcadores del bloque embebidos en texto producido por un LLM."""
    out = str(text or "")
    return out.replace(_MEMORY_OPEN, "[RUN_STATE]").replace(_MEMORY_CLOSE, "[/RUN_STATE]")


def _format_counts(counts: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, label in (
        ("passed_count", "passed"),
        ("failed_count", "failed"),
        ("error_count", "errors"),
        ("total_count", "total"),
    ):
        value = (counts or {}).get(key)
        if isinstance(value, int | float):
            parts.append(f"{int(value)} {label}")
    return ", ".join(parts)


def _es_skip_de_contrato(item: PlanItem) -> bool:
    """Descartado por contrato del preset (p.ej. sdd escribe los tests en el
    body): queda VISIBLE en el plan como skipped, pero no es trabajo del run."""
    return item.status == "skipped" and any("contrato" in n for n in item.notes)


def _count_status(plan: list[PlanItem]) -> dict[str, int]:
    subtasks = [p for p in plan if p.kind == "subtask"]
    # El denominador es el plan EJECUTABLE: un descarte por contrato no es
    # una subtarea que el run dejó sin entregar (el plan la sigue mostrando).
    # PERO si el descartado tiene evidencia (su artefacto ya existe) SÍ entra:
    # cuenta como entregado, no se excluye de los dos lados.
    ejecutables = [p for p in subtasks if not (_es_skip_de_contrato(p) and not p.satisfied_by)]
    delivered = sum(
        1 for p in subtasks if p.status == "delivered" or (p.status == "skipped" and p.satisfied_by)
    )
    verified = sum(
        1
        for p in subtasks
        if p.status == "delivered" and p.verification is not None and p.verification.passed
    )
    return {
        "total": len(ejecutables),
        "delivered": delivered,
        "verified": verified,
        "failed": sum(1 for p in ejecutables if p.status == "failed"),
        "unmeasured": sum(1 for p in ejecutables if p.status == "unmeasured"),
        "pending": sum(1 for p in ejecutables if p.status in ("pending", "running", "blocked")),
        "skipped": sum(1 for p in subtasks if p.status == "skipped"),
    }


_ICONS = {
    "delivered": "✅",
    "failed": "❌",
    "unmeasured": "⚠️",
    "pending": "⏳",
    "blocked": "⛔",
    "running": "🔵",
    "skipped": "⏭️",
}


def build_run_report(state: RunState, scorecard: dict, final_output: str) -> str:
    """Abre el mensaje final con el estado de entrega y el detalle acotado."""
    if scorecard.get("calidad") == "Alta":
        return final_output

    counts = _count_status(state.plan)
    calidad = scorecard.get("calidad", "Parcial")
    parts = [f"entrega {counts['delivered']}/{counts['total']}"]
    if counts["verified"]:
        parts.append(f"{counts['verified']} verificada(s)")
    if counts["pending"]:
        parts.append(f"{counts['pending']} pendiente(s)")
    if counts["failed"]:
        parts.append(f"{counts['failed']} fallida(s)")
    if counts["unmeasured"]:
        parts.append(f"{counts['unmeasured']} sin medición")
    if counts["skipped"]:
        parts.append(f"{counts['skipped']} saltada(s)")
    if scorecard.get("calidad_proceso"):
        parts.append(f"proceso: {scorecard['calidad_proceso']}")
    if scorecard.get("degradadas"):
        parts.append(f"{scorecard['degradadas']} degradada(s)")
    if scorecard.get("motivo"):
        parts.append(str(scorecard["motivo"]))

    header = f"⚠️ **Calidad: {calidad}** (" + "; ".join(parts) + ")"
    lines = [header]

    subtasks = [p for p in state.plan if p.kind == "subtask"]
    if subtasks:
        plan_bits = []
        for idx, item in enumerate(subtasks, 1):
            icon = _ICONS.get(item.status, "•")
            plan_bits.append(f"{idx}. {icon} {truncar_en_palabra(item.goal, 80)}")
        lines.append("Plan: " + "; ".join(plan_bits))

    process_items = [
        p
        for p in state.plan
        if p.kind != "subtask" and p.status in ("failed", "unmeasured", "blocked")
    ]
    if process_items:
        process_bits = []
        for item in process_items:
            detail = item.last_error or ""
            if not detail and item.verification is not None and item.verification.passed is False:
                detail = (item.verification.output_tail or "").splitlines()[0]
            if not detail:
                detail = "sin medición"
            process_bits.append(f"{item.id} ({item.status}: {truncar_en_palabra(detail, 120)})")
        lines.append("Proceso: " + "; ".join(process_bits))

    if state.replans:
        lines.append(f"Replan: {len(state.replans)}")
    if state.budget.limit:
        lines.append(
            f"Budget: {state.budget.spent}/{state.budget.limit} tokens "
            f"(reserva final {state.budget.reserve_final}, usada "
            f"{state.budget.reserve_final_spent})"
        )

    body = final_output or ""
    if len(body) > _MAX_REPORT_BODY:
        body = truncar_en_palabra(body, _MAX_REPORT_BODY)
    return "\n".join(lines) + "\n\n---\n\n" + body
