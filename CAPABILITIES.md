# Morphix — Capabilities & Testing Guide

**Tests:** ~698 | **Mypy:** 0 errors (0 exclusions) | **Pre-commit:** 7 hooks | **Sprints:** 26 (latest 26b, ~207 commits)

## System Overview

Morphix is an AI-powered coding assistant with multi-agent orchestration, MCP protocol support, and enterprise-grade security. It runs as a desktop GUI (PySide6).

### Architecture

```
GUI Desktop (PySide6)
       │
       ▼
WorkflowOrchestrator (4 routes)
    ├── Direct Tool Command
    ├── Collaborative (debate)
    ├── Coordinated (DAG parallel)
    └── Development (TaskAnalyzer → decompose → AgentRouter)
                │
                ▼
    Agent Loop (ReAct)  ←→  ToolOrchestrator
                │                    │
    LLM (DeepSeek/Ollama)     Tools (file, git, bash, code, search, MCP)
                                 │
                           Hooks System (pre/post tool)
                           Permission/Approval UI
                           Anti-distillation + Watermark
                           Token Budget + Cache

Layer map:
    desktop/          ← GUI + desktop/services/ (config, dashboard, analytics, history)
    orchestration/    ← workflow orchestration + executor + workflow strategies
    agents/           ← agent registry, loader, profiles, base execution, service, audit
    tools/            ← tool specs, registry, orchestrator, wrapper, loader + 12 implementations
    llm/              ← LLM controller, provider, parser, prompts, offline
    core/             ← business logic: database, config, memory, security, MCP, path resolver
```

---

## Quick Start

```bash
# GUI (recommended)
poetry run python run.py
```

---

## Workflows (4 wired)

### 1. Development — always orchestrates (no chat)

For software development tasks. AgentRouter picks the best agent per subtask.

**Template:** `workspaces/main/workflows/development.yaml`
**Agents:** developer, analista
**Tools:** file_manager, git_manager, bash_manager, lsp_manager, code_exec, test_runner, diff_editor, web_search, web_fetch

### 2. Collaborative — multi-agent debate

Panel of agents debate a topic, moderator produces consensus.

**Template:** `workspaces/main/workflows/collaborative.yaml`
**Agents:** developer, analista, moderador (moderator)
**Rounds:** 3

### 3. Coordinated — DAG parallel execution

MultiAgentCoordinator decomposes tasks into a DAG, executes independent subtasks in parallel with shared blackboard.

**Template:** `workspaces/main/workflows/coordinated.yaml`
**Agents:** developer, analista, moderador
**Max parallel:** 4

### 4. TDD — test-driven loop

Writes tests → runs → fixes → repeats until green (or max iterations). Active when the workflow is `tdd`. 300s per iteration.

**Strategy:** `orchestration/workflows/tdd.py`
**Agent:** `DEFAULT_AGENT` (developer)

> The `refactoring.yaml` template has been removed (it was an orphan with no Python wiring).

---

## Agents (5 profiles)

| Agent | Tools | Role | Best for |
|-------|-------|------|----------|
| **developer** | file_manager, git_manager, bash_manager, lsp_manager, code_exec, test_runner, diff_editor | agent | Coding, building, testing |
| **analista** | file_manager (read), lsp_manager, code_search, web_search | reasoning | Analysis, review, architecture |
| **architect** | lsp_manager, code_search | creativo | System design, planning, brainstorms |
| **moderador** | none | reasoning | Debate moderation, consensus |
| **conversacional** | none | agent | Quick chat, fallback |

> Web browsing via Playwright is possible by registering an **MCP server** (tools `mcp:browser.*`), but there is **no** built-in `navegador` agent.

---

## How to Use

### GUI — Desktop App

**Workflow mode (orchestrate):**
1. Dashboard → click a workflow card (development, collaborative, coordinated)
2. Create a project or select existing
3. Type your task → the system decomposes, assigns agents, executes

**Agent chat mode (direct):**
1. Dashboard → click an agent card (developer, analista, conversacional)
2. Type your message → direct conversation with that agent's tools
3. Or: click "💬 Chat" button, then click an agent in the panel

**Direct tool command:**
```
file_manager: read, path=src/main.py
bash_manager: ls, cwd=/tmp
```

---

## Example Prompts for Testing

### Development Workflow (Orchestrate)

Test TaskAnalyzer + TaskDecomposer + AgentRouter + execution:

```
Create a Python FastAPI app with a /health endpoint and a /users endpoint that returns a JSON list. Include tests with pytest.
```

```
Build a command-line TODO app in Python with add, list, and complete commands. Save to a JSON file.
```

```
Write a Python script that reads a CSV file and generates a summary report (row count, column names, basic stats).
```

### Collaborative Workflow (Debate)

Test multi-agent debate with moderator:

```
We need to choose between PostgreSQL and MongoDB for a new microservice that handles user profiles and session data. Discuss the trade-offs and recommend one.
```

```
The team wants to adopt micro-frontends vs a monolith frontend. Analyze both approaches considering team size (3 devs), deployment complexity, and maintenance.
```

```
Should we use async/await throughout the codebase or keep synchronous patterns? Consider readability, performance, and team familiarity.
```

### Coordinated Workflow (DAG)

Test parallel execution:

```
Create a REST API with user authentication. I need: 1) User model + DB schema, 2) Auth endpoints (register/login), 3) API endpoints (CRUD users), 4) Tests for all endpoints.
```

```
Set up a new project with: 1) Dockerfile for production, 2) docker-compose for dev with PostgreSQL and Redis, 3) CI pipeline (GitHub Actions), 4) README with setup instructions.
```

### Direct Agent Chat

Test agent with tools in chat mode:

```
Developer agent:
"Read the file src/main.py and explain what it does. Then suggest 3 improvements."

Analista agent:
"Review the architecture of this codebase. What patterns are used? What are the risks?"

Conversacional agent:
"Explain what a decorator is in Python with a simple example."
```

### Direct Tool Commands

```
file_manager: write, path=hello.py, content=print("Hello World")

bash_manager: python hello.py, cwd=code_projects/test1, timeout=10

git_manager: init, project_root=code_projects/test1

code_search: pattern=def test_, include=*.py
```

---

## MCP Tools (external servers via Model Context Protocol)

Morphix can connect to external MCP servers to extend its tool arsenal. Tools are registered with `mcp:<prefix>.<name>` format.

### Configuration

Edit `workspaces/<name>/mcp_servers.json` or the global `mcp_servers.json`:

```json
[
    {
        "name": "playwright",
        "command": "npx",
        "args": ["@playwright/mcp@latest"],
        "tools_prefix": "browser",
        "enabled": true
    }
]
```

### Run Morphix as MCP Server

```bash
poetry run morphix-mcp
```

Exposes the **11** native function-calling tools (from `TOOL_DEFINITIONS` in `tools/specs.py`) over stdio JSON-RPC for other MCP clients. `ask_clarification` is interception-only and is **not** exposed via MCP, so although Morphix registers 12 tools, the MCP server advertises 11.

---

## Features Reference

### New in auditoria-mayo-2026
- **Circuit breaker**: LLM providers auto-disabled after 5 consecutive failures, fall back to Ollama
- **Per-tool metrics**: Success/failure rates + latency per tool (accessible via `:stats`)
- **Aggregator with tools**: ResultAggregator can now correct file inconsistencies on disk
- **Stall detection improved**: File writes + tool success count as progress, no more false "stalled"

### Hooks System

6 interception points in every tool call:
- `on_before_tool` — before execution
- `on_after_tool` — after success
- `on_tool_error` — on failure
- `on_permission_denied` — permission check fail
- `on_token_budget_exceeded` — budget cap hit
- `on_tools_disabled` — globally disabled

Create hooks in `core/hooks/` (global) or `workspaces/<name>/hooks/` (per-workspace):

```python
from core.hooks_registry import hooks_registry, HookContext

@hooks_registry.register("on_before_tool")
def my_hook(ctx: HookContext):
    print(f"About to execute: {ctx.tool_name}")
```

### Permission/Approval

Dangerous tools trigger a confirmation dialog (desktop GUI):
- `bash_manager`, `code_exec`, `file_manager.delete`
- `git_manager.commit`, `git_manager.push`

Options: Allow Once / Always Allow / Deny.

### Anti-distillation

- Rotating watermarks on all outputs
- Distillation pattern detection (N similar queries)
- Escalation: warn → throttle → honeypot → lock
- Honeypot injects fake system info to waste attackers

### Token Budget + Prompt Caching

- Conversation compression at 90% of `MAX_CONTEXT_TOKENS`
- DeepSeek auto-cache monitoring (`prompt_cache_hit_tokens`/`prompt_cache_miss_tokens`)
- Cache-stable prefix compression (keeps prefix intact)

### Memory Consolidation (autoDream)

> "autoDream" is the docs' branding for `self_healing_check()` (the term does not appear in code).

`self_healing_check()` runs via daemon every 120s:
1. Quality check (LLM critiques recent docs)
2. Duplicate detection (FAISS similarity > 92%)
3. Contradiction resolution (LLM arbitrates similar pairs 65-92%)
4. Pruning (docs unaccessed for 30+ days)

---

## Key Commands

```bash
# Run all tests
poetry run pytest

# Run specific test
poetry run pytest tests/test_agent_loop.py -v

# Lint
poetry run ruff check .

# Format
poetry run black .

# Type check
poetry run mypy core/ llm/ agents/ tools/ orchestration/ desktop/

# Pre-commit all hooks
poetry run pre-commit run --all-files

# DB migrations
poetry run alembic upgrade head
```

---

## Configuration

Key settings in `.env`:

```bash
# Required
DATABASE_URL=postgresql://user:pass@localhost:5432/morphix
DEEPSEEK_API_KEY=sk-xxx

# Optional
DARK_MODE=true
OFFLINE_MODE=false
UNDERCOVER_MODE=true
CONTEXT_COMPRESSION=true
ALLOW_CODE_EXECUTION=true
HOOKS_ENABLED=true
MAX_SUBTASKS=8
DEFAULT_AGENT=developer
DEFAULT_WORKFLOW=development
```

---

## Project Structure

```
morphix/
├── core/              # Business logic (no UI dependencies)
│   ├── mcp/           # MCP protocol (client + server)
│   ├── memory/        # FAISS + MemoryManager
│   ├── security/      # Undercover, anti-distillation, frustration detector
│   ├── sandbox/       # RestrictedPython executor
│   ├── hooks/         # Global hook implementations (audit, distillation_guard)
│   └── repositories/  # Database repositories (ConversationRepository)
├── llm/               # LLM abstraction (controller, provider, parser, prompts, offline)
├── agents/            # Agent system (registry, loader, profiles, base, service, audit)
├── tools/             # Tool system + 12 implementations (file, git, bash, code, etc.)
├── orchestration/     # Workflow orchestration
│   ├── executor/      # Subtask execution (subtask, plan, verify, post)
│   └── workflows/     # Orchestration strategies (orchestrator, collaborative, coordinated, tdd, blackboard)
├── desktop/           # PySide6 GUI
│   ├── services/      # GUI business logic (config, dashboard, analytics, history)
│   └── widgets/       # Reusable widgets (agent_panel, bash_panel, chat_bubble — ChatBlock)
├── templates/         # Agent + workflow YAML templates
├── workspaces/        # Per-workspace configs (agents, workflows, hooks, mcp)
├── tests/             # 78 test modules (~698 test functions)
├── core/memory/        # Workspace memory (FAISS indices, user profile)
├── logs/              # Runtime logs (morphix.log)
└── exports/           # Conversation exports (json/md/pdf)
```
