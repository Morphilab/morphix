# features/maestro/services/multi_agent_coordinator.py
"""Multi-Agent Coordinator — manager-worker pattern with DAG execution.

Replaces the sequential linear subtask execution in full orchestration
with a proper DAG, parallel execution of independent subtasks, shared
blackboard for inter-agent communication, and confidence-weighted aggregation.

Architecture:
    1. decompose_task_dag() → LLM produces structured DAG (JSON)
    2. assign_agents() → LLM-based quality routing (replaces keyword supervisor)
    3. execute_dag() → parallel asyncio.gather per level, respecting dependencies
    4. aggregate_with_confidence() → LLM evaluates + synthesizes
"""

import asyncio
import json
import logging
from typing import Any

from core.config import settings
from core.utils import clean_llm_response
from llm import models
from orchestration.context import emit_agent
from orchestration.loop import AgentLoopConfig, execute_agent_loop
from orchestration.router import AgentRouter
from orchestration.workflows.blackboard import SharedBlackboard
from tools.specs import tool_matches_allowlist

logger = logging.getLogger(__name__)

DAG_DECOMPOSE_PROMPT = """You are a task decomposition expert. Break the user's request into subtasks
with dependencies. Produce ONLY valid JSON in this exact format:
{{
  "subtasks": [
    {{"id": "short_id", "description": "...", "depends_on": [], "agent_hint": "developer"}},
    {{"id": "other_id", "description": "...", "depends_on": ["short_id"], "agent_hint": "analista"}}
  ]
}}
Rules:
- id: short snake_case identifier
- depends_on: list of ids this subtask waits for (empty = can start immediately)
- agent_hint: which agent type fits best ("developer", "analista", "architect", "moderador", "conversacional")
- Independent subtasks (same level, no mutual dependencies) can run in parallel
- Maximum 6 subtasks
- Subtasks should be specific and actionable, not vague

User request: {query}

JSON:"""


class MultiAgentCoordinator:
    """Orchestrates multiple agents with DAG-based execution and shared blackboard."""

    def __init__(self):
        self.blackboard = SharedBlackboard()

    # ── PHASE 1: DECOMPOSE INTO DAG ──
    async def decompose_task_dag(self, query: str) -> dict[str, Any]:
        """Ask the LLM to decompose a task into a structured DAG.

        Returns: {"subtasks": [...], "raw_response": "..."}
        Each subtask: {"id": str, "description": str, "depends_on": [str], "agent_hint": str}
        """
        prompt = DAG_DECOMPOSE_PROMPT.format(query=query)

        try:
            response = await models.call(
                messages=[{"role": "user", "content": prompt}],
                role="reasoning",
                temperature=0.1,
            )
            raw = clean_llm_response(response)
            content = raw.choices[0].message.content if hasattr(raw, "choices") else str(raw)

            # Extract JSON from response
            data = self._parse_dag_json(content)
            if data and isinstance(data.get("subtasks"), list) and len(data["subtasks"]) >= 1:
                # Validate and clean subtasks
                valid = []
                for st in data["subtasks"]:
                    if not isinstance(st, dict):
                        continue
                    sid = st.get("id", "")
                    desc = st.get("description", "")
                    if sid and desc and len(desc) > 5:
                        st.setdefault("depends_on", [])
                        st.setdefault("agent_hint", "developer")
                        valid.append(st)
                if valid:
                    return {"subtasks": valid[:6], "raw_response": content}

        except Exception as e:
            logger.warning(f"DAG decomposition failed: {e}")

        # Fallback: single linear subtask
        return {
            "subtasks": [
                {
                    "id": "main_task",
                    "description": query,
                    "depends_on": [],
                    "agent_hint": "developer",
                }
            ],
            "raw_response": "",
        }

    @staticmethod
    def _parse_dag_json(text: str) -> dict | None:
        """Extract JSON object from LLM response text."""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning(
                "Failed to parse JSON from LLM response, attempting fallback extraction",
                exc_info=True,
            )
        # Try to find JSON between braces containing "subtasks"
        idx = text.find('"subtasks"')
        if idx < 0:
            return None
        start = text.rfind("{", 0, idx)
        if start < 0:
            return None
        depth = 0
        end = start
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                logger.warning(
                    "Failed to parse extracted JSON fragment from LLM response", exc_info=True
                )
        return None

    # ── PHASE 2: ASSIGN AGENTS ──
    async def assign_agents(
        self,
        subtasks: list[dict],
        allowed_agents: list[str] | None = None,
        force_agent: str | None = None,
    ) -> dict[str, str]:
        """Assign the best agent for each subtask.

        Uses AgentRouter first, then LLM quality review for confidence.
        Falls back to keyword matching if LLM is unavailable.

        Returns: {subtask_id: agent_name}
        """
        assignments: dict[str, str] = {}

        for st in subtasks:
            sid = st["id"]
            if force_agent:
                assignments[sid] = force_agent
                continue

            hint = st.get("agent_hint", "")
            desc = st.get("description", "")

            # Try AgentRouter first
            try:
                picked = await AgentRouter.select_best_agent(desc, "coordinated", allowed_agents)
                if picked and (allowed_agents is None or picked in allowed_agents):
                    assignments[sid] = picked
                    continue
            except Exception:
                logger.warning(
                    "AgentRouter selection failed, using fallback assignment", exc_info=True
                )

            # Fallback: agent_hint or first allowed
            if hint and (allowed_agents is None or hint in allowed_agents):
                assignments[sid] = hint
            elif allowed_agents:
                assignments[sid] = allowed_agents[0]
            else:
                assignments[sid] = "developer"

        return assignments

    # ── PHASE 3: EXECUTE DAG WITH PARALLELISM ──
    async def execute_dag(
        self,
        subtasks: list[dict],
        assignments: dict[str, str],
        project_root: str | None = None,
        workspace: str | None = None,
        allowed_tools: list[str] | None = None,
        events=None,
        session=None,
    ) -> dict[str, dict]:
        """Execute subtasks respecting DAG dependencies with parallel branches.

        Topological level execution:
        - Level 0: subtasks with no dependencies → all run in parallel
        - Level 1: subtasks whose dependencies are all in level 0 → parallel
        - etc.

        Returns: {subtask_id: {status, result, agent, files_written, error}}
        """
        if workspace is None:
            workspace = settings.active_workspace
        results: dict[str, dict] = {}
        completed: set[str] = set()
        remaining = {st["id"]: st for st in subtasks}

        while remaining:
            # Find subtasks whose dependencies are all completed
            ready = []
            for sid, st in list(remaining.items()):
                deps = st.get("depends_on", [])
                if all(d in completed for d in deps):
                    ready.append((sid, st))

            if not ready:
                # Circular dependency or all stuck — execute remaining sequentially
                logger.warning("DAG stuck — executing remaining subtasks sequentially")
                for sid, st in remaining.items():
                    result = await self._execute_one(
                        sid,
                        st,
                        assignments.get(sid, "developer"),
                        project_root,
                        workspace,
                        allowed_tools,
                        events,
                        session,
                    )
                    results[sid] = result
                    completed.add(sid)
                break

            logger.info(
                f"DAG level: {len(ready)} subtask(s) in parallel: " f"{[s[0] for s in ready]}"
            )

            # Execute ready subtasks in parallel
            tasks = [
                self._execute_one(
                    sid,
                    st,
                    assignments.get(sid, "developer"),
                    project_root,
                    workspace,
                    allowed_tools,
                    events,
                    session,
                )
                for sid, st in ready
            ]

            parallel_results = await asyncio.gather(*tasks, return_exceptions=True)

            for (sid, st), raw_result in zip(ready, parallel_results, strict=True):
                if isinstance(raw_result, BaseException):
                    logger.error(f"Subtask '{sid}' failed: {raw_result}")
                    if isinstance(raw_result, asyncio.TimeoutError):
                        logger.warning(
                            f"Subtask {sid} timed out — not retrying with different agent, marking as failed"
                        )
                        results[sid] = {
                            "status": "failed",
                            "result": "Timeout after 180s",
                            "error": "timeout",
                            "files_written": [],
                        }
                        continue
                    # Retry with different agent
                    fallback = next(
                        (
                            a
                            for a in (assignments.get(sid, "developer"), "developer", "analista")
                            if a != assignments.get(sid)
                        ),
                        "developer",
                    )
                    logger.info(f"Retrying '{sid}' with agent '{fallback}'")
                    try:
                        result = await self._execute_one(
                            sid,
                            st,
                            fallback,
                            project_root,
                            workspace,
                            allowed_tools,
                            events,
                            session,
                        )
                    except Exception as e2:
                        result = {
                            "status": "failed",
                            "result": f"Failed after retry: {e2}",
                            "agent": fallback,
                            "files_written": [],
                            "error": str(e2),
                        }
                else:
                    result = raw_result

                if isinstance(result, dict):
                    status = result.get("status", "")
                    error = result.get("error", "")
                    if status == "skipped":
                        logger.info(
                            "⏭️  Subtask %s SKIPPED: %s",
                            sid,
                            result.get("result", "")[:120],
                        )
                    elif status == "failed":
                        logger.warning(
                            "Subtask %s FAILED: %s (agent=%s)",
                            sid,
                            error or result.get("result", "unknown")[:120],
                            result.get("agent", "?"),
                        )

                results[sid] = result
                completed.add(sid)
                remaining.pop(sid, None)

        return results

    async def _execute_one(
        self,
        sid: str,
        st: dict,
        agent: str,
        project_root: str | None,
        workspace: str,
        allowed_tools: list[str] | None,
        events,
        session,
    ) -> dict:
        """Execute a single subtask with one agent, injecting blackboard context."""
        desc = st.get("description", st.get("id", ""))

        # ── Pre-execution skip: avoid redundant work when a previous subtask
        #     already created the target file with substantial code.
        should_skip, skip_reason = await self._should_skip_subtask(sid, st, project_root, workspace)
        if should_skip:
            logger.info("⏭️  Subtask %s SKIPPED: %s", sid, skip_reason)
            return {
                "status": "skipped",
                "result": f"⏭️  Skipped: {skip_reason}",
                "agent": agent,
                "files_written": [],
                "error": None,
            }

        # Build blackboard context for this agent
        blackboard_ctx = await self.blackboard.get_agent_context()
        if blackboard_ctx:
            blackboard_ctx = (
                "⚠️  SHARED CONTEXT — Resultados de otros agentes en este workflow:\n"
                + blackboard_ctx
                + "\n\nUsa esta información para evitar trabajo duplicado. "
                "Si un archivo ya fue creado por otro agente, NO lo recrees."
            )

        # Filter tools against agent profile
        agent_filtered_tools = allowed_tools
        from agents.registry import agents_registry as _reg

        agent_profile = _reg.get_profile(agent)
        if agent_profile and agent_profile.get("tools"):
            profile_tools = agent_profile.get("tools", [])
            from tools.specs import expand_allowed_tools

            expanded_profile = expand_allowed_tools(profile_tools) or []
            if allowed_tools is not None:
                # Intersect using prefix/component matching (supports MCP tool names)
                agent_filtered_tools = [
                    t for t in expanded_profile if tool_matches_allowlist(t, allowed_tools)
                ]
            else:
                agent_filtered_tools = expanded_profile

        try:
            result = await asyncio.wait_for(
                execute_agent_loop(
                    task=desc,
                    agent_type=agent,
                    allowed_tools=agent_filtered_tools,
                    project_root=project_root,
                    workspace=workspace,
                    extra_context=blackboard_ctx,
                    session=session,
                    config=AgentLoopConfig(max_agent_iterations=settings.max_agent_iterations),
                ),
                timeout=180,
            )
        except TimeoutError:
            return {
                "status": "failed",
                "result": "Subtask timed out after 180s",
                "agent": agent,
                "files_written": [],
                "error": "Timeout",
            }
        except Exception as e:
            return {
                "status": "failed",
                "result": str(e),
                "agent": agent,
                "files_written": [],
                "error": str(e),
            }

        # Emit agent response to UI for real-time visibility
        result_text = result.get("result", "") if isinstance(result, dict) else str(result)
        if session and hasattr(session, "events") and session.events:
            await emit_agent(session.events, agent, desc[:50], str(result_text)[:500])

        return {
            "status": result.get("status", "done") if isinstance(result, dict) else "done",
            "result": result_text,
            "agent": agent,
            "files_written": result.get("files_written", []) if isinstance(result, dict) else [],
            "error": None,
        }

    async def _should_skip_subtask(
        self,
        sid: str,
        st: dict,
        project_root: str | None,
        workspace: str,
    ) -> tuple[bool, str]:
        """Check if a subtask's work was already done by a previous subtask.

        Heuristic: if the target .py file already exists with multiple function
        definitions and substantial content (>500 chars), the work is done.

        Returns (should_skip: bool, reason: str).
        """
        import re

        from core.path_resolver import paths

        desc = st.get("description", st.get("id", ""))

        # Only skip implement/create/write tasks (never skip verify/test/analyze)
        implement_kw = ("implement", "crear", "write", "escribir", "codificar", "code")
        if not any(kw in desc.lower() for kw in implement_kw):
            return False, ""

        # Extract candidate .py filenames from the subtask description
        filenames = re.findall(r"[\w_-]+\.py", desc)
        if not filenames:
            return False, ""

        if not project_root or not workspace:
            return False, ""

        base = paths.memory_dir(workspace) / project_root
        if not base.exists():
            return False, ""

        for fname in filenames:
            target = base / fname
            if not target.exists():
                continue
            try:
                content = target.read_text(encoding="utf-8")
            except Exception:
                continue
            func_count = len(re.findall(r"^\s*def\s+\w+\s*\(", content, re.MULTILINE))
            if func_count >= 2 and len(content) > 500:
                return True, (
                    f"Work already done: '{fname}' exists with {func_count} functions "
                    f"({len(content)} chars)"
                )

        return False, ""

    # ── PHASE 4: AGGREGATE WITH CONFIDENCE ──
    async def aggregate_with_confidence(
        self,
        query: str,
        results: dict[str, dict],
        project_root: str | None = None,
        workspace: str | None = None,
    ) -> str:
        """Delegate to ResultAggregator for unified, deterministic synthesis.

        Uses the same evaluation logic as the development workflow:
        - If files exist on disk → programmatic or filtered-LLM response
        - If no files → normal LLM synthesis (backward-compatible)
        """
        from orchestration.aggregator import ResultAggregator

        files_written: list[str] = []
        for r in results.values():
            if isinstance(r, dict):
                files_written.extend(r.get("files_written", []))

        return await ResultAggregator.aggregate_results(
            query=query,
            results=results,
            G=None,
            task_analysis={},
            files_written=files_written or None,
            project_root=project_root,
            workspace=workspace,
        )
