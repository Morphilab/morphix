# Tools Layer

The `tools/` layer provides the complete tool system — OpenAI function-calling specs, dynamic registration from `.py` files, orchestration with hook interception and token budgets, and 12 tool implementations.

## Module Inventory

### Specs (`specs.py`)

```python
@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    required: list[str] | None = None

    def to_openai_spec(strict: bool = False) -> dict

TOOL_DEFINITIONS: dict[str, ToolDefinition]  # 11 tools with full parameter schemas

def build_tool_definitions(allowed_tools: list[str] | None = None) -> list[dict]
def build_tool_instructions(allowed_tools, project_root, plan_mode=True) -> str
def expand_allowed_tools(allowed_tools: list[str] | None) -> list[str] | None
def tool_matches_allowlist(tool_name: str, allowlist: list[str]) -> bool
```

- **`ToolDefinition`**: A dataclass holding the name, description, parameter schema, and optional explicit `required` list. Its `to_openai_spec()` method produces a JSON Schema dict compatible with OpenAI's function-calling API.
- **Strict mode**: When `settings.deepseek_strict_mode` is `True`, specs add `"strict": true` and `"additionalProperties": false` for DeepSeek compatibility. MCP-prefixed tools are excluded from strict mode since their schemas are generated externally.
- **`TOOL_DEFINITIONS`**: Dictionary of 11 tools with full parameter type definitions, enums, and descriptions.
- **`expand_allowed_tools()`**: Bridges agent profiles (which may use short names like `"browser"`) with MCP-registered tool names (`"mcp_browser_browser_navigate"`). Supports exact match, prefix match, and sanitized MCP prefix expansion.
- **Legacy support**: `build_tool_instructions()` generates text-based tool instructions for Ollama/fallback modes where native function-calling is unavailable.

### Registry (`registry.py`)

```python
class ToolsRegistry:
    def register(name: str) -> Callable   # Decorator
    def get_tool(name: str) -> Callable | None
    def list_tools() -> dict[str, Callable]
    def unregister(name: str) -> bool
    def clear()

# Global legacy instance
tools_registry = ToolsRegistry()
```

- **Instanciable**, not a singleton. The global `tools_registry` is kept for backward compatibility; `ToolsRegistry()` can be created fresh for tests.
- **`@tools_registry.register("name")`**: The decorator pattern used by all tool implementations. Each `.py` file in `tools/` calls this to make its tool available.
- **`unregister()`**: Used during workspace switching to remove workspace-specific tools without affecting globals.

### Orchestrator (`orchestrator.py`)

```python
class ToolOrchestrator:
    MAX_RETRIES = settings.tool_max_retries            # default: 3
    BACKOFF_BASE = settings.tool_backoff_base          # default: 1.5
    MAX_TOKENS_PER_WORKFLOW = settings.tool_max_tokens_per_workflow  # default: 8000
    ENABLE_TOKEN_BUDGET = settings.tool_enable_token_budget

    DANGEROUS_ACTIONS: set[str] = {
        "bash_manager", "code_exec", "file_manager.delete",
        "git_manager.commit", "git_manager.push",
    }

    on_approval_required: Callable[[str, dict], Awaitable[bool]] | None

    @staticmethod
    async def execute_tool(tool_name, parameters, role, max_tokens, workspace, session_id) -> dict
    @staticmethod
    def reset_token_budget()

tool_orchestrator = ToolOrchestrator()  # Global instance
```

**Execution flow per tool call:**

1. **Tools disabled check** — If `settings.tools_enabled` is False, dispatches `on_tools_disabled` hook and returns immediately
2. **Tool existence check** — Validates the tool name exists in the registry
3. **Permission check** — Uses `kairos` feature flags (`allow_{tool_name}_{role}`) and `ALLOW_CODE_EXECUTION` gating
4. **Interactive approval** — For dangerous actions, invokes `on_approval_required` callback (wired by the UI)
5. **Token budget check** — `contextvars.ContextVar` isolates budget per async task; estimates tokens from parameter JSON
6. **Hook dispatch** — Fires `on_before_tool` before execution
7. **Execution with retry** — Up to `MAX_RETRIES` attempts with exponential backoff (`backoff_base ** attempt + jitter`)
8. **Fast-fail paths** — Skips retry for file-not-found errors, workspace boundary violations, and deterministic test failures
9. **Post-execution hooks** — Fires `on_after_tool` or `on_tool_error` as appropriate

**6 interception hooks** (via `hooks_registry`):

| Hook | Point | Context |
|------|-------|---------|
| `on_before_tool` | Before execution | tool_name, parameters, attempt, workspace |
| `on_after_tool` | After success | tool_name, result, duration, attempt |
| `on_tool_error` | On exception | tool_name, error, attempt |
| `on_permission_denied` | Permission check fails | tool_name, role, workspace |
| `on_token_budget_exceeded` | Budget exceeded | tool_name, parameters |
| `on_tools_disabled` | Tools disabled globally | — |

**Token budget** is isolated per async task using `contextvars.ContextVar` (Python 3.12 copies contextvars in `create_task`). `reset_token_budget()` sets it to 0 at the start of each workflow.

### Wrapper (`wrapper.py`)

```python
async def safe_tool_call(
    tool_name: str,
    parameters: dict,
    role: str = "agent",
    workspace: str = "main",
    session_id: str | None = None,
) -> dict
```

High-level wrapper used by the workflow orchestrator. Key features:

- **Empty name guard**: Returns error if `tool_name` is empty or whitespace
- **bash_manager fast-fail**: Immediately fails if `command` parameter is missing or empty
- **MCP routing**: Routes `mcp:`-prefixed tool names to the appropriate MCP client
- **Metrics**: Records per-tool latency and success/failure via `tool_metrics.record_call()`
- **Delegation**: Non-MCP tools are forwarded to `tool_orchestrator.execute_tool()`

### Loader (`loader.py`)

```python
def load_global_tools()
def load_workspace_tools(workspace: str)
def unload_workspace_tools()
```

- **`load_global_tools()`**: Imports all `.py` files from `tools/` at startup (skipping `_`-prefixed files like `__init__.py`). Each file's module-level decorators register into `tools_registry`.
- **`load_workspace_tools()`**: Imports `.py` files from `workspaces/<name>/tools/`, registering workspace-specific tools.
- **`unload_workspace_tools()`**: Removes workspace tools from both the registry and `sys.modules`, preparing for a clean workspace switch.

## 12 Tool Implementations

| Registered Name | Source File | Key Actions / Purpose |
|----------------|-------------|----------------------|
| `file_manager` | `file_manager.py` | `write`, `read`, `append`, `delete` — File CRUD within the project workspace |
| `bash_manager` | `bash_manager.py` | Shell command execution with security sandbox (requires `command` parameter) |
| `git_manager` | `git_manager.py` | `init`, `add`, `commit`, `log`, `diff` — Git repository management |
| `test_runner` | `test_runner.py` | Pytest execution with pass/fail/error counts and output capture |
| `lsp_manager` | `lsp_manager.py` | `definition`, `hover`, `diagnostics`, `references`, `ruff_check` — Jedi-based code analysis |
| `code_exec` | `code_execution.py` | Sandboxed Python execution (RestrictedPython: math, numpy, matplotlib; blocks I/O) |
| `diff_editor` | `diff_editor.py` | `apply`, `create` — Surgical unified diff editing without rewriting entire files |
| `web_search` | `web_search.py` | Google Custom Search with configurable result count |
| `web_fetch` | `web_fetch.py` | URL content fetching and text extraction |
| `code_search` | `code_search.py` | Regex-based recursive search across project files with glob filtering |
| `pdf_read` | `pdf_reader.py` | PDF text extraction from project files |
| `ask_clarification` | `ask_clarification.py` | Pauses workflow to ask user a question (interception-only; not in `TOOL_DEFINITIONS`) |

> **Naming note**: Registered names differ from file names for two tools: `code_execution.py` → `code_exec`, `pdf_reader.py` → `pdf_read`. The registered name is the key used in `TOOL_DEFINITIONS` and by agents.

**`ask_clarification`** is special — it has no function-calling spec in `TOOL_DEFINITIONS`. When an agent calls it during the loop, the agent loop intercepts it, pauses execution, persists the state to `PausedSession`, and returns a `clarification_needed` status. The UI renders the question, waits for user input, and resumes the loop.

## kits/ and skills/

### Tool Kits (`tools/kits/`)

4 YAML-based predefined multi-tool workflows:

| Kit | Goal | Steps |
|-----|------|-------|
| `tdd_workflow.yaml` | Test-driven development cycle | Write test → Run test → Implement → Run test → Refactor |
| `debug_cycle.yaml` | Systematic debugging | Reproduce → Diagnose → Fix → Verify |
| `project_setup.yaml` | New project initialization | Create structure → Init git → Install deps → Verify |
| `code_quality.yaml` | Code quality improvement | Lint → Analyze → Fix → Verify |

Kits are loaded by `_load_tool_kits()` in the agent loop and injected into the system prompt as structured instructions. Only kits whose tools are in the agent's allowlist are included.

### Tool Skills (`tools/skills/`)

8 YAML-based per-tool skill definitions:

| Skill File | Tool | Content |
|------------|------|---------|
| `file_manager.yaml` | file_manager | When to use (read before write, avoid large reads), examples, tips |
| `bash_manager.yaml` | bash_manager | When NOT to use (don't use for git, use git_manager), command patterns |
| `git_manager.yaml` | git_manager | Standard git workflow, version control guidance |
| `code_exec.yaml` | code_exec | Sandbox restrictions, when to use bash_manager instead |
| `test_runner.yaml` | test_runner | Test execution patterns, test naming conventions |
| `lsp_manager.yaml` | lsp_manager | Code navigation, diagnostics usage |
| `code_search.yaml` | code_search | Search pattern examples, result interpretation |
| `diff_editor.yaml` | diff_editor | Surgical editing guidance, diff format conventions |

Skills are loaded by `_load_tool_skills()` and injected into the system prompt. Each skill defines: `when_to_use`, `when_not_to_use`, `examples`, and `tips`.

## Tool Lifecycle

```mermaid
graph TD
    A[.py file in tools/] --> B[load_global_tools / load_workspace_tools]
    B --> C[@tools_registry.register decorator]
    C --> D[get_tool lookup]
    D --> E[safe_tool_call wrapper]
    E --> F[ToolOrchestrator.execute_tool]
    F --> G{Hooks: on_before_tool}
    G --> H[Tool function execution]
    H --> I{Hooks: on_after_tool / on_tool_error}
    I --> J[Result returned to agent loop]
```

1. **Registration** — Tool `.py` files use `@tools_registry.register("name")` decorators; loaded at startup (global) or workspace switch (local)
2. **Spec binding** — Each registered name maps to a `ToolDefinition` in `TOOL_DEFINITIONS` for function-calling parameter schemas
3. **Execution** — `safe_tool_call()` wraps with validation, MCP routing, and metrics; delegates to `ToolOrchestrator.execute_tool()` for hook lifecycle, retries, and token budgeting
4. **Context injection** — Skills and kits YAMLs are injected into the agent loop's system prompt to guide proper tool usage
