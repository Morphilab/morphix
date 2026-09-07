# Tools

Morphix provides **24 registered tools** that agents use to interact with your project, plus the interception-only `ask_clarification`. Tools are loaded dynamically from `tools/*.py` at startup, and workspace-specific tools from `workspaces/<name>/tools/*.py` are loaded on workspace switch. All 24 are also exposed through the built-in MCP server.

!!! note "Registered name vs filename"
    Tool names registered with `@tools_registry.register("name")` may differ from their `.py` filenames. Notable differences: `code_execution.py` → `code_exec`, `pdf_reader.py` → `pdf_read`. Always use the **registered name** in prompts and tool calls.

---

## Tool Reference

| Tool | What it does |
|------|--------------|
| `file_manager` | Read, write, append, and delete files in the project workspace (paths relative to the project root). |
| `git_manager` | Manage Git repositories: init, add, commit, log, diff, and read a file at a ref (`show`). |
| `code_exec` | Execute Python in a sandboxed subprocess (RestrictedPython). Supports math, numpy, matplotlib; blocks filesystem and network access. |
| `lsp_manager` | Analyze Python code with LSP (Jedi): definitions, hover, diagnostics, references, plus real Ruff linting. |
| `pdf_read` | Extract text from PDF files in the project. |
| `memory_inspector` | Inspect the workspace's persistent memory: list keys, read values, delete a key (delete requires `confirm_delete=true`). |
| `file_view` | Open a file in a visualization window for the user (md/pdf/html rendered). Never returns content to the agent — to read, use `pdf_read` or `file_manager`. |
| `project_docs` | Project Knowledge Base (read-only): list/read/search/inject curated docs from `workspaces/<ws>/knowledge/`. |
| `vision_analyze` | Analyze an image with the vision role: describe, OCR (extract text), or interpret charts. Returns text. |
| `test_runner` | Run tests with pytest and return structured results (passed/failed/errors counts). |
| `diff_editor` | Edit files by applying unified diffs — surgical changes without rewriting whole files. |
| `bash_manager` | Run shell commands safely in the project workspace (sanitized, with blocklist and timeouts). |
| `web_search` | Search the web with Google Custom Search. Optional — needs `GOOGLE_API_KEY` and `GOOGLE_CX`. |
| `web_fetch` | Fetch a URL and return its content as plain text. |
| `code_search` | Regex search across project files (recursive grep). |
| `load_skill` | Load a procedural skill by name and get step-by-step instructions (use when the `<SKILLS>` section indicates one applies). |
| `goal_create` | Create a long-lived goal for the workspace (state machine active/paused/blocked/complete with CAS revisions). |
| `goal_get` | List the workspace's goals or read one by id (with its CAS revision). |
| `goal_update` | Update a goal with compare-and-swap (`expected_revision` required); `blocked→active` requires human authority. |
| `goal_round` | Record a work round on a goal (increments rounds with CAS; exceeding `max_rounds` auto-blocks). |
| `todo_write` | Replace the whole task list (CAS). More than one `in_progress` item is rejected unless explicitly allowed. |
| `todo_get` | Read the workspace's task list (with its CAS revision). |
| `plan_mode` | Activate (or replace) the workspace's active plan with its steps. |
| `exit_plan_mode` | End plan mode with an explicit decision: `Approve` (accept and deactivate the plan) or `Keep-planning`. |

### ask_clarification (special)

`ask_clarification` is **interception-only**: it is not in `TOOL_DEFINITIONS` and never reaches normal tool execution. When an agent needs more information, the loop intercepts the call, the workflow pauses, the `PausedSession` is persisted to the database, and the question appears in the Maestro chat. The workflow resumes when you answer (or can be discarded with the **⏸ Abandonar pausa** button). In contexts where pausing is not allowed (e.g. bot routines), the interception denies the request with a tool result instead.

---

## Security Notes

### bash_manager

- `command` is **mandatory**; without it the tool fast-fails. The shell starts in the project root — do **not** use `cd`.
- Extensive sanitization with a blocklist: command substitution (`$(...)`, backticks, process substitution `<(...)`), destructive commands (`rm -rf /`, `dd if=`, `mkfs.*`), privilege escalation (`sudo`, `chmod 777 /`), reverse shells (`nc -l`, `socat`, `/dev/tcp/`), arbitrary code (`python -c`, `perl -e`), remote download+exec, pip/npm-style installs, `ssh`/`scp`/remote `rsync`, and writes into workspace tool/agent/skill directories.
- Timeouts: most commands 120 s; package managers 300 s.
- `python` is automatically rewritten to `python3` at execution time.

### code_exec

- Runs in a **confined child subprocess** (RestrictedPython) — host process memory is not exposed to executed code, and a hard timeout kill-switch applies.
- Blocked: filesystem and network access, and modules like `os`, `sys`, `subprocess`, `socket`, `requests`, `pathlib`.
- Safe modules include `math`, `numpy`, `matplotlib` (charts saved under `charts/` only), `json`, `datetime`, `random`, `statistics`, and friends.
- Output is capped (64 KB) and a memory limit applies to the child.

### web content

Results from tools that carry external content — `web_search`, `web_fetch`, `pdf_read`, `git_manager`, `test_runner`, `code_search`, `vision_analyze`, and MCP tools — are wrapped as **untrusted data** (`⟪DATOS-NO-CONFIABLES⟫`) before entering the conversation, so embedded instructions from external content are not followed blindly.

---

## Tool Availability by Agent

Agents declare their tools in their YAML templates (`templates/agents/*.yaml`, copied to each workspace):

| Agent | Tools |
|-------|-------|
| **developer** | `file_manager`, `git_manager`, `bash_manager`, `lsp_manager`, `code_exec`, `test_runner`, `diff_editor`, `file_view`, `goal_create`, `goal_get`, `goal_update`, `goal_round`, `todo_write`, `todo_get` |
| **architect** | `file_manager`, `lsp_manager`, `code_search`, `web_search`, `file_view`, `plan_mode`, `exit_plan_mode` |
| **analista** | `file_manager`, `lsp_manager`, `code_search`, `web_search`, `web_fetch` |
| **conversacional** | none (pure chat) |
| **moderador** | none (pure synthesis) |

Workflows further restrict what is available during a run via their `tools.allowed` list — an agent only sees the intersection of its own tools and the active workflow's allowlist.

## MCP Tools

Morphix also loads external MCP tools from `mcp_servers.json`. MCP tool names are prefixed with `mcp:`; in DeepSeek strict mode colons are sanitized to underscores (e.g. `mcp_browser_browser_navigate`). Availability follows the agent/workflow allowlists via `expand_allowed_tools()`.

## Direct Tool Commands

You can call any tool directly from the input field using the format:

```
tool_name: action, key=value, key2=value2
```

This bypasses workflow orchestration entirely (fast path). The tool name is validated against the registry to prevent false positives on natural language queries.

**Examples:**
```
file_manager: read, path=src/main.py
git_manager: log, project_root=myapp
bash_manager: execute, command=pytest tests/ -v
```

Direct tool commands are only recognized when the tool name exists in the registry.
