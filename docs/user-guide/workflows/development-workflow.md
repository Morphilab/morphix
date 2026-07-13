# Development Workflow

The Development workflow is Morphix's general-purpose orchestration engine. It handles everyday software engineering tasks — from "create a Flask API" to "refactor this module" — by decomposing your request, routing it to the right agents, executing subtasks, and aggregating results.

## Pipeline Overview

When you submit a task in Development mode, Morphix runs a 6-stage pipeline:

```
TaskAnalyzer → Decomposer (3-5 subtasks) → AgentRouter → Agent Loop (ReAct) → Supervisor → Aggregator
```

### Stage 1: TaskAnalyzer

The `TaskAnalyzer` inspects your query and classifies it:

- **Complexity**: simple, medium, or complex
- **Type**: simple_conversation, creative, analyst, executor, planner, investigator, or mixed
- **Orchestration decision**: whether the task requires full orchestration or can be handled as a simple conversation

Results are cached in an LRU cache (500 entries, 5-minute TTL) for faster repeat queries.

### Stage 2: Decomposer

The Decomposer breaks your task into **3–5 actionable subtasks**. Each subtask is a clear, specific instruction for a single agent.

Subtasks are sequential — each depends on the previous one completing successfully. The decomposer adapts its output based on whether this is a new task or a follow-up in an existing conversation (sets `is_follow_up=True` for modifications/extensions vs. greenfield creation).

### Stage 3: AgentRouter

For each subtask, the `AgentRouter` selects the best agent based on:
- Subtask description and keywords
- Task primary type from TaskAnalyzer
- Allowed agents from the workflow template

### Stage 4: Supervisor

The `WorkflowSupervisor` reviews all router selections and corrects assignments where needed. This Safety Net check prevents mismatches like assigning the analista (read-only) to a code-writing subtask.

### Stage 5: Agent Loop (ReAct)

Each subtask runs through the `execute_agent_loop` with function-calling:

```python
# Pseudocode of the agent loop
for iteration in range(max_agent_iterations):
    response = await llm_call_with_tools(task, history, tools)
    if response has tool_calls:
        for tool_call in tool_calls:
            result = await execute_tool(tool_call)
            history.append(tool_result)
        continue  # feed tool results back
    break  # final text response, subtask done
```

The loop supports DeepSeek's reasoning mode (`reasoning_content`) and streams responses to the UI.

### Stage 6: Aggregator

The `ResultAggregator` collects all subtask results, reads actual files from disk (not LLM output), and synthesizes a final response. It also performs a **global project verification** if a project root is set — checking all files with LSP diagnostics and applying automatic corrections.

## How to Use

### From the Dashboard

1. Click the **Development** workflow card
2. Morphix switches to the **Maestro** tab in Orchestrate mode
3. Select a project from the project dropdown (or leave unset)
4. Type your task in the input field
5. Press **Ctrl+Enter** to submit

### Example Session

```
> Create a REST API with FastAPI for managing a todo list.
  Include endpoints for CRUD operations, use SQLAlchemy for the database,
  and add input validation with Pydantic.
```

**What happens:**

1. TaskAnalyzer classifies this as `ejecutor` (executor), medium complexity, requires full orchestration
2. Decomposer creates subtasks:
   - Subtask 1: "Set up FastAPI project structure and dependencies" → **developer**
   - Subtask 2: "Create SQLAlchemy models for Todo items" → **developer**
   - Subtask 3: "Implement CRUD API endpoints with Pydantic schemas" → **developer**
   - Subtask 4: "Add input validation and error handling" → **developer**
   - Subtask 5: "Write tests and verify all endpoints" → **developer**
3. AgentRouter + Supervisor confirm agent assignments
4. Each subtask executes sequentially, with the agent reading/writing files via `file_manager`
5. Global verification runs LSP diagnostics
6. Aggregator reads the final files from disk and produces the response

## Progress Tracking

The execution panel (left column) shows real-time progress:

| Element | Description |
|---------|-------------|
| **Progress bar** | Fills as subtasks complete (e.g., 3/5 = 60%) |
| **Subtask list** | Each subtask with a status icon: `✅` completed, `🔵` running, `❌` failed, `⏳` pending |
| **Stats** | Subtask count, elapsed time, tokens used, current agent |

The subtask list updates after each subtask completes, driven by the `subtask_list` key in `emit_stats` payloads.

## Safety Net

The Development workflow includes two layers of protection:

1. **Analysis agents never write files** — The `analista` agent has `file_manager` (read-only) and `lsp_manager`/`code_search`/`web_search`, but no write permissions. The Supervisor ensures code-writing subtasks are assigned to the `developer` agent.

2. **Secondary review** — The `WorkflowSupervisor.review_and_correct()` inspects agent assignments after routing. If a destructive or write-intensive subtask was misrouted to a read-only agent, the Supervisor reassigns it.

## Configuration Limits

| Parameter | Default | Description |
|-----------|:---:|-------------|
| `max_agent_iterations` | 8 | Maximum ReAct loop iterations per subtask |
| `max_subtasks` | 8 | Maximum number of decomposed subtasks |
| `context_compression` | enabled | Compresses conversation history when tokens exceed 80% of `max_context_tokens` |

Subtask timeouts are not enforced in the Development workflow (sequential execution without explicit per-subtask timeout, unlike Coordinated's 180s or TDD's 300s). The agent loop itself will stop after `max_agent_iterations` or when the LLM produces a final response without tool calls.

## When to Use vs Other Workflows

| Scenario | Recommended workflow |
|----------|---------------------|
| Build a feature, fix a bug, refactor code | **Development** |
| Multi-step task with independent phases | Coordinated |
| Architecture decision or design debate | Collaborative |
| Feature with guaranteed test coverage | TDD |
| Quick question or explanation | Chat mode (no workflow) |

!!! tip "Chat mode vs Orchestrate mode"
    For quick questions, explanations, or simple code snippets, switch to **Chat mode** and select an agent directly. This bypasses orchestration entirely and uses the `_run_simple_conversation` fast path — faster and cheaper for simple queries.
