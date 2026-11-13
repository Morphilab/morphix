# tests/_dsl_engine_support.py — helpers compartidos del split de test_dsl_engine
"""FakeRuntime y constructores de documentos DSL: compartidos por los
archivos test_dsl_engine*.py (módulo interno, NO colectado por pytest)."""

from typing import Any

from orchestration.dsl.compiler import CompiledWorkflow, compile_workflow
from orchestration.dsl.engine import AgentOutcome
from orchestration.dsl.run_state import ReplanDecision


def _doc(steps: list[dict], **over) -> dict:
    base = {
        "version": 1,
        "name": "demo",
        "agents": {"allowed": ["developer", "analista", "moderador"]},
        "tools": {"allowed": ["file_manager", "test_runner", "eval_suite"]},
        "steps": steps,
    }
    base.update(over)
    return base


def _agent(sid: str, agent: str = "developer", **over) -> dict:
    step = {"id": sid, "kind": "agent", "agent": agent, "goal": "haz"}
    step.update(over)
    return step


def _compile(steps: list[dict], **over) -> CompiledWorkflow:
    return compile_workflow(_doc(steps, **over))


class FakeRuntime:
    """Runtime determinista con respuestas programables."""

    def __init__(
        self,
        *,
        agent_output: str = "salida estandar",
        tool_results: dict | None = None,
        decide_answer: str | None = None,
        evaluate_answer: str | None = None,
        files_written: list[str] | None = None,
        files_written_sequence: list[list[str]] | None = None,
        tokens_per_call: int = 0,
        replan_decision: Any = None,
    ):
        self.agent_calls: list[tuple[str, str]] = []
        self.agent_contexts: list[str] = []
        self.agent_tools: list[list[str] | None] = []
        self.tool_calls: list[tuple[str, dict]] = []
        self.order: list[str] = []
        self.agent_output = agent_output
        self.tool_results = tool_results or {}
        self.decide_answer = decide_answer
        self.evaluate_answer = evaluate_answer
        self.files_written = list(files_written or [])
        self.files_written_sequence = [list(x) for x in (files_written_sequence or [])]
        self.tokens_per_call = tokens_per_call
        self.replan_decision = replan_decision
        self.replan_calls = 0
        self.replan_requests: list[dict] = []
        self.checkpoint_approved = False
        self.checkpoint_calls: list[str] = []
        self.aggregate_calls: list[Any] = []
        self.aggregate_results: list[list[dict]] = []
        self.emits: list[dict] = []
        self.budget_scopes: list[tuple] = []
        self._degraded_steps: list[str] = []

    async def call_agent(
        self,
        agent: str,
        prompt: str,
        *,
        model_role: str | None = None,
        step_id: str = "",
        path: str = "",
        max_iterations: int | None = None,
        extra_context: str = "",
        allowed_tools: list[str] | None = None,
    ) -> AgentOutcome:
        self.agent_calls.append((agent, prompt))
        self.agent_contexts.append(extra_context)
        self.agent_tools.append(allowed_tools)
        self.order.append(path or step_id)
        files = (
            self.files_written_sequence.pop(0)
            if self.files_written_sequence
            else list(self.files_written)
        )
        return AgentOutcome(
            status="completed",
            text=self.agent_output,
            files_written=files,
            iterations=1,
            tokens=self.tokens_per_call,
        )

    async def call_tool(self, tool: str, args: dict) -> dict:
        self.tool_calls.append((tool, args))
        return self.tool_results.get(tool, {"ok": True})

    async def replan(self, **kwargs) -> ReplanDecision:
        self.replan_calls += 1
        self.replan_requests.append(kwargs)
        return self.replan_decision or ReplanDecision(action="stop", reason="fake")

    async def decompose(self, query: str, strategy: str) -> Any:
        if strategy == "flat":
            return ["tarea_a", "tarea_b"]
        return {"tasks": [{"id": "n1", "depends_on": []}]}

    async def decide(
        self, question: str, options: list[str], context: str, *, step_id: str = ""
    ) -> str:
        # El runtime puede devolver salida inválida — el motor valida y aplica fallback
        return self.decide_answer if self.decide_answer is not None else options[0]

    async def evaluate(self, question: str, expect: str, context: str) -> str:
        return self.evaluate_answer if self.evaluate_answer is not None else expect

    async def checkpoint(self, question: str, step_id: str = "") -> bool:
        self.checkpoint_calls.append(question)
        return self.checkpoint_approved

    async def aggregate(self, strategy: str, results: list[dict], agent: str | None = None) -> str:
        self.aggregate_calls.append((strategy, len(results)))
        self.aggregate_results.append(list(results))
        return "RESUMEN_FINAL"

    def emit(self, **payload) -> None:
        self.emits.append(payload)

    async def begin_budget_scope(self, budget_tokens: int) -> None:
        self.budget_scopes.append(("begin", budget_tokens))

    async def end_budget_scope(self) -> None:
        self.budget_scopes.append(("end", None))


class _DictCatalog:
    def __init__(self, presets: dict[str, dict]):
        self.presets = presets

    def load(self, name: str) -> dict | None:
        return self.presets.get(name)
