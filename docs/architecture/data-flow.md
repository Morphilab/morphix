# Data Flow

This document describes how data moves through Morphix — from user message to final response — covering both the high-level orchestration path and the low-level streaming mechanics.

## High-Level Flow

```mermaid
graph TD
    A[User Message] --> B[WorkflowOrchestrator.run_full_workflow]
    B --> C{undercover.check_query}
    C -->|blocked| Z[Security Rejection]
    C -->|allowed| D{Direct tool cmd?}
    D -->|yes| E[Direct Tool Route]
    D -->|no| F{Bot-owned conversation?}
    F -->|yes| G[bots_runner - canonical turn]
    F -->|no| H{Doc has version: 1?}
    H -->|no| X[Actionable rejection]
    H -->|yes| I[compile + validate]
    I --> J[WorkflowEngine + ProductionRuntime]
    J --> K[Agent Loop - ReAct]
    K --> L[ToolOrchestrator]
    L --> M[Tool Execution]
    M --> K
    J --> N[ResultAggregator]
    N --> O[Finalizer]
    O --> P[User Response]
    E --> P
    G --> P
    X --> P
```

### 1. Entry: `run_full_workflow()`

```python
# orchestration/workflows/orchestrator.py:270
async def run_full_workflow(session: Session) -> str | None:
    ctx = session.context
    events = session.events
    query = ctx.query
```

The entry point receives a `Session` dataclass combining `WorkflowContext` (immutable task state) and `WorkflowEvents` (callback interface). No UI framework objects are passed — the orchestrator is completely decoupled from PySide6.

### 2. Security Check

The `undercover.check_query()` method inspects the query for prohibited patterns. Blocked queries return `None` immediately, never reaching the LLM.

### 3. Direct Tool Fast Path

If the query matches `tool_name: action, key=val` (e.g., `git_manager: commit, message="fix bug"`) and the tool exists in the registry, the orchestrator skips all analysis and executes the tool directly:

```python
# orchestration/workflows/orchestrator.py:301
direct_tool = _parse_direct_tool_command(query)
if direct_tool:
    return await self._run_direct_tool(direct_tool, query, ...)
```

The tool name is validated against the registry to prevent false positives on natural language (e.g., `navega y analiza : URL` won't match because `navega` is not a registered tool).

### 4. Route Dispatch (DSL)

`_dispatch_route()` resolves the active workflow document with `load_workflow_document()` (workspace override → product templates → global). A document with `version: 1` enters the DSL path; anything else (missing doc or legacy format) is rejected with an actionable error pointing at `python -m orchestration.dsl.cli new`. If the conversation is owned by a bot, the turn is dispatched via `bots_dispatch.dispatch_canonical_turn()` before any template is loaded.

### 5. Decomposition

DSL presets decompose explicitly with a `decompose` step (`strategy: flat | dag`) executed by the engine through `ProductionRuntime.decompose()`, which calls `decompose_task()` / `decompose_task_with_phases()` from `orchestration/decomposer.py`. For complex tasks this breaks the query into 2–5 subtasks; the resulting list lands in a state variable that a `loop over` iterates.

### 6. Agent Loop (ReAct)

Each agent step runs via `ProductionRuntime.call_agent()` → `execute_agent_loop()`. The loop:

1. Builds the system prompt with tool definitions and instructions
2. Calls the LLM (streaming preferred, non-streaming fallback)
3. Parses tool calls from the response
4. Executes tools via `safe_tool_call()`
5. Appends observations to the conversation history
6. Checks for stalls, clarification requests, or completion
7. Repeats until done or max iterations reached

```python
# orchestration/loop.py — simplified loop
while iteration < config.max_agent_iterations:
    full_text, tool_calls, finish_reason, reasoning = await _call_llm(messages, ...)
    if not tool_calls:
        break  # Natural completion
    actions_taken, modified, files, stalls, early = await _execute_tool_calls_and_check_stall(...)
    if early:
        break  # Stall detected
    iteration += 1
```

### 7. Aggregation + Finalization

When the DSL document ends with an `aggregate` step, `ResultAggregator.aggregate_results()` synthesizes all subtask results into a coherent response (deterministic per-subtask success/partial/failure evaluation, conformance checks, disk reads of written files). `finalize_workflow()` persists the conversation to the database and generates a scorecard. DSL `commit_after` phases trigger git commits only after declared phases with completed subtasks and files written.

## Context Management

`WorkflowContext` carries all task state as a dataclass:

```python
@dataclass
class WorkflowContext:
    query: str
    mode: str = "chat"
    conversation_history: list[dict] = field(default_factory=list)
    current_pdf_text: str = ""
    workspace: str = "main"
    project_root: str | None = None
    active_workflow: str = "default"
    force_agent: str | None = None
    allowed_tools: list[str] | None = None
    conversation_id: int | None = None
    is_follow_up: bool = False
    cancelled: bool = False
    last_clarification: str = ""
    blackboard: Any = None
```

Token budget management follows a tiered approach:

| Threshold | Action |
|-----------|--------|
| 70% of max (simple conversations) | Compress history |
| 80% of max (full orchestration) | Compress history |
| 90% of max (LLM calls) | Compress before sending to API |

Default `MAX_CONTEXT_TOKENS`: **128,000** (configurable via `.env`).

Compression uses `ContextManager.compress_history()` which preserves system messages and recent turns while trimming older content. Compressed target: 70% of max tokens.

## Streaming Data Flow

### Chunk Path: LLM → Controller → Accumulator → GUI

```mermaid
sequenceDiagram
    participant LLM as LLM API (SSE)
    participant Ctrl as ModelsController
    participant Loop as _accumulate_stream
    participant GUI as ChatBlock

    LLM->>Ctrl: SSE stream (OpenAI-compatible)
    Ctrl->>Ctrl: _stream_openai_async()
    Ctrl->>Loop: yield StreamChunk(text="Hel")
    Ctrl->>Loop: yield StreamChunk(text="lo")
    Ctrl->>Loop: yield StreamChunk(tool_name="file_manager", tool_call_id="call_1")
    Ctrl->>Loop: yield StreamChunk(tool_arguments='{"action":', tool_call_id="call_1")
    Ctrl->>Loop: yield StreamChunk(tool_arguments='"write"}', tool_call_id="call_1")
    Ctrl->>Loop: StreamChunk(finish_reason="stop", is_done=True)
    Loop->>GUI: on_stream_chunk("Hel")
    Loop->>GUI: on_stream_chunk("lo")
    Note over GUI: Debounce 70ms → setMarkdown
    Loop->>Loop: Reassemble tool calls by index
    Loop->>GUI: Emit agent/tool messages
```

### StreamChunk Structure

```python
# llm/controller.py:45-55
@dataclass
class StreamChunk:
    text: str | None = None
    tool_name: str | None = None
    tool_arguments: str | None = None
    tool_call_id: str | None = None
    finish_reason: str | None = None
    reasoning_content: str | None = None
    usage: dict[str, int] | None = None
    is_done: bool = False
```

### Tool-Call Argument Accumulation (Sprint 25b Fix)

A critical detail in streaming tool calls: the OpenAI/DeepSeek SSE protocol sends tool-call deltas where **only the first delta carries `id` and `name`**. Subsequent deltas have `id=None` and only `function.arguments` fragments. The controller accumulates by **stable `index`**, not by `id`:

```python
# llm/controller.py:394-419 — key logic
if delta.tool_calls:
    for tc in delta.tool_calls:
        idx = getattr(tc, "index", None)
        tc_id = getattr(tc, "id", None)
        key = idx if idx is not None else tc_id  # Use index as primary key
        if key is None:
            continue
        if key not in tool_acc:
            tool_acc[key] = {
                "id": tc_id or f"call_{len(tool_acc)}",
                "name": "",
                "arguments": "",
            }
        entry = tool_acc[key]
        if tc_id:
            entry["id"] = tc_id  # Update real ID when available
        func = getattr(tc, "function", None)
        if func:
            if getattr(func, "name", None):
                entry["name"] = func.name
            if getattr(func, "arguments", None):
                entry["arguments"] += func.arguments
```

The **accumulator** in `_accumulate_stream()` (`orchestration/loop.py:62-132`) then reassembles tool calls from the chunk stream. It handles the edge case where arguments arrive before the name by deferring until the name is known:

```python
# orchestration/loop.py:86-108
if chunk.tool_name and chunk.tool_call_id:
    tid = chunk.tool_call_id
    if tid not in tool_call_by_id:
        tool_call_by_id[tid] = {
            "id": tid,
            "function": {"name": chunk.tool_name, "arguments": ""},
        }
    else:
        tool_call_by_id[tid]["function"]["name"] = chunk.tool_name

if chunk.tool_arguments and chunk.tool_call_id:
    tid = chunk.tool_call_id
    if tid not in tool_call_by_id:
        if chunk.tool_name:
            tool_call_by_id[tid] = {
                "id": tid, "function": {"name": chunk.tool_name, "arguments": ""},
            }
        else:
            continue  # Defer until name arrives
    tool_call_by_id[tid]["function"]["arguments"] += chunk.tool_arguments
```

### GUI Debounce: 70ms Batch Rendering

`ChatBlock.update_text()` coalesces streaming updates to prevent O(n²) re-renders. `QTextBrowser.setMarkdown()` re-parses and re-lays-out the entire document on every call, so rendering on every token would be quadratic.

```python
# desktop/widgets/chat_bubble.py:157-171
def update_text(self, text: str):
    self._text = text
    self._pending_text = text
    if self._stream_timer is None:
        self._stream_timer = QTimer(self)
        self._stream_timer.setSingleShot(True)
        self._stream_timer.timeout.connect(self._flush_stream)
    if not self._stream_timer.isActive():
        self._stream_timer.start(70)  # Render at most once per ~70ms
```

At stream end, `flush_stream()` renders any remaining pending text immediately.

### Non-Streaming Path (Comparison)

The non-streaming path (`call()`) uses the OpenAI SDK's non-streaming endpoint. Tool calls are returned already assembled — no accumulation needed:

```python
# llm/controller.py:184
response = client.chat.completions.create(**call_kwargs)
# response.choices[0].message.tool_calls is already complete
```

Streaming is preferred for UX (users see text as it's generated), but the non-streaming fallback kicks in when:
- Retries exhausted (streams fail and can't be recovered)
- The provider doesn't support streaming
- `stream=False` is explicitly requested

## Events System

The orchestrator communicates with the UI exclusively through `WorkflowEvents` callbacks:

```python
@dataclass
class WorkflowEvents:
    on_system_message: Callable[[str], Awaitable[None]] | None = None
    on_assistant_message: Callable[[str], Awaitable[None]] | None = None
    on_user_message: Callable[[str], Awaitable[None]] | None = None
    on_stream_chunk: Callable[[str], Awaitable[None]] | None = None
    on_stats_update: Callable[[dict], Awaitable[None]] | None = None
    on_ui_refresh: Callable[[], Awaitable[None]] | None = None
    on_approval_required: Callable[[str, dict[str, Any]], Awaitable[bool]] | None = None
    on_agent_message: Callable[[str, str, str], Awaitable[None]] | None = None
```

Helper functions (`emit_system()`, `emit_stats()`, `emit_stream_chunk()`, etc.) fire callbacks safely — if a callback is `None` or raises an exception, it's silently skipped rather than crashing the workflow.
