# Adding Hooks

Hooks let you intercept tool calls at specific points in their lifecycle — before execution, after success, on error, and more. This guide shows how to create and register hooks.

## Architecture

Hooks are managed by `HooksRegistry` (`core/hooks_registry.py`), which follows the same decorator-based pattern as `ToolsRegistry`:

```python
class HooksRegistry:
    def register(self, hook_point: str) -> Callable: ...
    async def dispatch(self, hook_point: str, context: HookContext) -> None: ...
    def unregister(self, hook_point: str, func: Callable) -> bool: ...
    def clear_hook_point(self, hook_point: str) -> None: ...
    def clear(self) -> None: ...

hooks_registry = HooksRegistry()  # global singleton
```

## HookContext

Every hook receives a `HookContext` dataclass with all the information about the tool call:

```python
@dataclass
class HookContext:
    hook_point: str              # Which hook point fired (e.g., "on_before_tool")
    tool_name: str               # Name of the tool being called
    parameters: dict[str, Any]   # Parameters passed to the tool
    role: str = "agent"          # Calling role (agent, user, system)
    result: Any = None           # Tool result (only for on_after_tool)
    error: str | None = None     # Error message (only for on_tool_error)
    duration: float = 0.0        # Execution time in seconds (only for on_after_tool)
    attempt: int = 1             # Retry attempt number (only for on_tool_error)
    workspace: str = "main"      # Active workspace
    session_id: str | None = None  # Current session ID
```

## The 6 interception points

| Hook point | When it fires | Context fields available |
|-----------|---------------|-------------------------|
| `on_before_tool` | Before tool execution | `tool_name`, `parameters`, `role`, `workspace`, `session_id` |
| `on_after_tool` | After successful execution | + `result`, `duration` |
| `on_tool_error` | After execution fails | + `error`, `attempt` |
| `on_permission_denied` | When user denies tool permission | `tool_name`, `parameters`, `role` |
| `on_token_budget_exceeded` | When token budget is exceeded mid-workflow | `tool_name`, `parameters` |
| `on_tools_disabled` | When tools are globally disabled | `tool_name`, `role` |

## Step 1: Create a hook implementation

Create `core/hooks/my_monitor.py`:

```python
"""Custom hook: monitor tool calls and log summary statistics."""
import json
import logging
import time

from core.hooks_registry import HookContext, hooks_registry

logger = logging.getLogger(__name__)

# Track cumulative stats per tool
_stats: dict[str, dict] = {}


def _get_stats(tool_name: str) -> dict:
    if tool_name not in _stats:
        _stats[tool_name] = {
            "calls": 0,
            "successes": 0,
            "failures": 0,
            "total_duration_ms": 0.0,
        }
    return _stats[tool_name]


@hooks_registry.register("on_before_tool")
def monitor_before_tool(ctx: HookContext) -> None:
    """Count every tool invocation attempt."""
    stats = _get_stats(ctx.tool_name)
    stats["calls"] += 1
    logger.debug(f"▶  {ctx.tool_name} called with {list(ctx.parameters.keys())}")


@hooks_registry.register("on_after_tool")
def monitor_after_tool(ctx: HookContext) -> None:
    """Record successful execution and duration."""
    stats = _get_stats(ctx.tool_name)
    stats["successes"] += 1
    stats["total_duration_ms"] += ctx.duration * 1000

    # Log a summary every 10 calls
    if stats["calls"] % 10 == 0:
        avg = stats["total_duration_ms"] / stats["calls"]
        logger.info(
            f"📊 {ctx.tool_name}: {stats['calls']} calls, "
            f"{stats['successes']} ok / {stats['failures']} fail, "
            f"avg {avg:.1f}ms"
        )


@hooks_registry.register("on_tool_error")
def monitor_tool_error(ctx: HookContext) -> None:
    """Record failure and log the error."""
    stats = _get_stats(ctx.tool_name)
    stats["failures"] += 1
    logger.warning(
        f"⚠  {ctx.tool_name} failed (attempt {ctx.attempt}): {ctx.error}"
    )


@hooks_registry.register("on_token_budget_exceeded")
def monitor_budget_exceeded(ctx: HookContext) -> None:
    """Alert when token budget is exhausted."""
    logger.warning(
        f"💰 Token budget exceeded during {ctx.tool_name} call in workspace '{ctx.workspace}'"
    )
```

## Step 2: The decorator pattern

Use `@hooks_registry.register("hook_point_name")` to register. The decorator works like `@tools_registry.register()`:

```python
@hooks_registry.register("on_before_tool")
def my_hook(ctx: HookContext) -> None:
    # ctx contains all the context fields shown above
    print(f"About to call {ctx.tool_name}")
```

Hooks can be sync or async — the dispatcher handles both:

```python
# Sync hook
@hooks_registry.register("on_before_tool")
def sync_hook(ctx: HookContext) -> None:
    pass

# Async hook
@hooks_registry.register("on_after_tool")
async def async_hook(ctx: HookContext) -> None:
    await some_async_operation(ctx.result)
```

!!! warning "Hook exceptions are caught"
    If a hook raises an exception, the dispatcher logs a warning and continues. Hooks never break the tool execution pipeline.

## Step 3: Load your hook

### Option A: Global hooks (always active)

Place your hook in `core/hooks/` and import it in the application entry point or `__init__.py`. The built-in `audit.py` hook is loaded this way:

```python
# In run.py or core/__init__.py:
import core.hooks.audit       # registers via @hooks_registry.register
import core.hooks.my_monitor  # registers via @hooks_registry.register
```

### Option B: Workspace hooks (per-workspace)

Place hooks in `workspaces/<name>/hooks/*.py`. They are loaded and cleared on workspace switch via the same loader mechanism used for tools.

## Step 4: Test your hook

Create `tests/test_hooks_my_monitor.py`:

```python
import pytest
from unittest.mock import MagicMock

from core.hooks_registry import HookContext, HooksRegistry


@pytest.mark.asyncio
async def test_monitor_counts_calls():
    """The monitor hook should increment call counts."""
    reg = HooksRegistry()

    call_count = 0

    @reg.register("on_before_tool")
    def count_calls(ctx: HookContext) -> None:
        nonlocal call_count
        call_count += 1

    ctx = HookContext(
        hook_point="on_before_tool",
        tool_name="file_manager",
        parameters={"action": "read", "path": "test.py"},
    )

    await reg.dispatch("on_before_tool", ctx)
    await reg.dispatch("on_before_tool", ctx)

    assert call_count == 2


@pytest.mark.asyncio
async def test_error_hook_receives_error_field():
    """On tool error, the context should carry error info."""
    reg = HooksRegistry()
    captured_error = None

    @reg.register("on_tool_error")
    def capture_error(ctx: HookContext) -> None:
        nonlocal captured_error
        captured_error = ctx.error

    ctx = HookContext(
        hook_point="on_tool_error",
        tool_name="bash_manager",
        parameters={"command": ""},
        error="missing_required_param: command",
        attempt=1,
    )

    await reg.dispatch("on_tool_error", ctx)
    assert captured_error == "missing_required_param: command"


def test_hook_unregister():
    """Hooks can be removed from specific hook points."""
    reg = HooksRegistry()

    @reg.register("on_before_tool")
    def temp_hook(ctx: HookContext) -> None:
        pass

    assert len(reg.list_hooks().get("on_before_tool", [])) == 1

    reg.unregister("on_before_tool", temp_hook)
    assert len(reg.list_hooks().get("on_before_tool", [])) == 0
```

## Complete example: Audit logging hook

This is the built-in `core/hooks/audit.py` — it logs every tool call to the audit trail:

```python
"""Global hook: audit every tool call to the audit log."""
import json
import logging

from agents.audit import log_operation
from core.hooks_registry import HookContext, hooks_registry

logger = logging.getLogger(__name__)


@hooks_registry.register("on_before_tool")
def audit_on_before_tool(ctx: HookContext) -> None:
    """Log tool invocation attempt before execution."""
    log_operation(
        operation="tool_before",
        details=json.dumps({
            "tool": ctx.tool_name,
            "params": {k: str(v)[:200] for k, v in ctx.parameters.items()},
            "role": ctx.role,
            "workspace": ctx.workspace,
        }),
        success=True,
    )


@hooks_registry.register("on_after_tool")
def audit_on_after_tool(ctx: HookContext) -> None:
    """Log tool result after successful execution."""
    log_operation(
        operation="tool_after",
        details=json.dumps({
            "tool": ctx.tool_name,
            "duration": round(ctx.duration, 3),
            "role": ctx.role,
            "workspace": ctx.workspace,
        }),
        success=True,
    )


@hooks_registry.register("on_tool_error")
def audit_on_tool_error(ctx: HookContext) -> None:
    """Log tool failure with error details."""
    log_operation(
        operation="tool_error",
        details=json.dumps({
            "tool": ctx.tool_name,
            "error": ctx.error,
            "attempt": ctx.attempt,
            "role": ctx.role,
            "workspace": ctx.workspace,
        }),
        success=False,
    )
```

## Hook dispatch in the tool pipeline

Hooks fire from `tools/orchestrator.py` during `execute_tool()`:

```
execute_tool(tool_name, params)
    │
    ├─► hooks.dispatch("on_before_tool", ctx)
    │
    ├─► [execute tool]
    │       │
    │       ├─ success ─► hooks.dispatch("on_after_tool", ctx)
    │       │
    │       └─ failure ─► hooks.dispatch("on_tool_error", ctx)
    │
    └─► return result
```

## Checklist

- [ ] Hook function uses `@hooks_registry.register("hook_point")` decorator
- [ ] Function signature accepts `ctx: HookContext` parameter
- [ ] Sync or async — both supported; exceptions are caught and logged
- [ ] Hook placed in `core/hooks/` (global) or `workspaces/<name>/hooks/` (workspace-scoped)
- [ ] Imported in application entry point for global hooks
- [ ] Tests cover each registered hook point
- [ ] Tests verify context fields are populated correctly
