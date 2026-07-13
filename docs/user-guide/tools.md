# Tools

Morphix provides 12 registered tools that agents use to interact with your project. Tools are loaded dynamically from `tools/*.py` at startup, and workspace-specific tools from `workspaces/<name>/tools/*.py` are loaded on workspace switch.

!!! note "Registered name vs filename"
    Tool names registered with `@tools_registry.register("name")` may differ from their `.py` filenames. Notable differences: `code_execution.py` → `code_exec`, `pdf_reader.py` → `pdf_read`. Always use the **registered name** in prompts and tool calls.

---

## Tool Reference

### 1. file_manager

| | |
|---|---|
| **Registered name** | `file_manager` |
| **Filename** | `tools/file_manager.py` |
| **Description** | Read, write, append, and delete files in the project workspace. All paths are relative to the project root. |

**Actions:**

| Action | Description |
|--------|-------------|
| `read` | Read the full contents of a file |
| `write` | Create or overwrite a file with given content |
| `append` | Append content to the end of a file |
| `delete` | Delete a file |

**Parameters:**

| Parameter | Required | Description |
|-----------|:---:|-------------|
| `action` | Yes | One of: `write`, `read`, `append`, `delete` |
| `path` | Yes | Relative path (e.g., `src/main.py`, `tests/test_app.py`) |
| `content` | For write/append | The content to write |

**Security:** All paths are validated to prevent directory traversal outside the workspace.

**Example:**
```
file_manager: read, path=src/app.py
file_manager: write, path=src/models.py, content=from sqlalchemy import Column...
```

---

### 2. git_manager

| | |
|---|---|
| **Registered name** | `git_manager` |
| **Filename** | `tools/git_manager.py` |
| **Description** | Manage Git repositories: initialize, stage files, commit, view log and diffs. |

**Actions:**

| Action | Description |
|--------|-------------|
| `init` | Initialize a new Git repository |
| `add` | Stage files (uses `git add .`) |
| `commit` | Commit staged changes with a message |
| `log` | Show commit history |
| `diff` | Show uncommitted changes |

**Parameters:**

| Parameter | Required | Description |
|-----------|:---:|-------------|
| `action` | Yes | One of: `init`, `add`, `commit`, `log`, `diff` |
| `message` | For commit | Commit message |
| `project_root` | No | Project directory (e.g., `code_projects/myapp`) |

**Example:**
```
git_manager: init, project_root=myapp
git_manager: commit, message=Add user authentication module
```

---

### 3. code_exec

| | |
|---|---|
| **Registered name** | `code_exec` |
| **Filename** | `tools/code_execution.py` |
| **Description** | Execute Python code in a secure sandbox (RestrictedPython). Supports math, numpy, matplotlib. Blocks filesystem and network access. |

**Parameters:**

| Parameter | Required | Description |
|-----------|:---:|-------------|
| `code` | Yes | Python code to execute (10s timeout) |

**Security:** Uses RestrictedPython sandbox. Blocked modules: `os`, `sys`, `subprocess`, `io`, `socket`, `requests`, `pathlib`. Safe modules: `math`, `numpy`, `matplotlib`, `ast`, `json`, `datetime`, `collections`, `itertools`, `functools`, `typing`, `re`, `random`, `statistics`, `decimal`, `fractions`, `hashlib`, `base64`, `csv`, `textwrap`.

**Example:**
```
code_exec: execute, code=import math\nprint(math.sqrt(16))
```

---

### 4. lsp_manager

| | |
|---|---|
| **Registered name** | `lsp_manager` |
| **Filename** | `tools/lsp_manager.py` |
| **Description** | Analyze Python code using LSP (Jedi): definitions, hover information, diagnostics, references, and Ruff linting. |

**Actions:**

| Action | Description |
|--------|-------------|
| `definition` | Go to definition of a symbol |
| `hover` | Get type/ docstring info for a symbol |
| `diagnostics` | Get Jedi diagnostics for a file |
| `references` | Find all references to a symbol |
| `ruff_check` | Run the Ruff linter on a file |

**Parameters:**

| Parameter | Required | Description |
|-----------|:---:|-------------|
| `action` | Yes | One of: `definition`, `hover`, `diagnostics`, `references`, `ruff_check` |
| `file` | No | File path to analyze |
| `line` | No | Line number (0-indexed) |
| `character` | No | Character number (0-indexed) |
| `project_root` | No | Project directory |

**Example:**
```
lsp_manager: diagnostics, file=src/app.py
lsp_manager: references, file=src/models.py, line=15, character=8
```

---

### 5. pdf_read

| | |
|---|---|
| **Registered name** | `pdf_read` |
| **Filename** | `tools/pdf_reader.py` |
| **Description** | Extract text from PDF files in the project. |

**Parameters:**

| Parameter | Required | Description |
|-----------|:---:|-------------|
| `path` | Yes | Relative path to the PDF file |
| `project_root` | No | Project directory |

**Example:**
```
pdf_read: read, path=docs/specification.pdf
```

---

### 6. test_runner

| | |
|---|---|
| **Registered name** | `test_runner` |
| **Filename** | `tools/test_runner.py` |
| **Description** | Run tests with pytest and return results (passed, failed, errors). |

**Parameters:**

| Parameter | Required | Description |
|-----------|:---:|-------------|
| `file_path` | Yes | Test file path or directory (e.g., `tests/test_app.py`, `.`) |
| `test_name` | No | Specific test name (e.g., `test_login` or `TestUser::test_create`) |
| `project_root` | Yes | Project directory |
| `workspace` | No | Workspace name (default: `main`) |
| `timeout` | No | Max timeout in seconds (default: 30) |

**Pytest flags applied:** `--rootdir=<project>`, `-p no:cacheprovider`, `-q` (quiet mode).

**Example:**
```
test_runner: run, file_path=./tests, project_root=myapp
test_runner: run, file_path=tests/test_auth.py, test_name=test_login
```

---

### 7. diff_editor

| | |
|---|---|
| **Registered name** | `diff_editor` |
| **Filename** | `tools/diff_editor.py` |
| **Description** | Edit files by applying unified diffs — surgical changes without rewriting entire files. Also accepts `content` as an alias for `diff_content` and `path` as an alias for `file_path`. |

**Actions:**

| Action | Description |
|--------|-------------|
| `apply` | Apply a unified diff to a file |
| `create` | Generate a diff of current changes |

**Parameters:**

| Parameter | Required | Description |
|-----------|:---:|-------------|
| `file_path` | Yes | File path to edit |
| `action` | Yes | `apply` or `create` |
| `diff_content` | For apply | Unified diff content |
| `project_root` | No | Project directory |

**Example:**
```
diff_editor: apply, file_path=src/app.py, diff_content=@@ -10,7 +10,7 @@
 def get_users():
-    return User.query.all()
+    return User.query.limit(100).all()
```

---

### 8. bash_manager

| | |
|---|---|
| **Registered name** | `bash_manager` |
| **Filename** | `tools/bash_manager.py` |
| **Description** | Execute shell commands safely in the project workspace. |

**Parameters:**

| Parameter | Required | Description |
|-----------|:---:|-------------|
| `command` | **Yes (mandatory)** | Shell command to execute. The shell is already in the project directory — do NOT use `cd`. |

**Security:** Extensive sanitization. The following are blocked:

- Command substitution: `$(...)` and backticks
- Destructive commands: `rm -rf /`, `rm -rf *`, `dd if=`, `mkfs.*`
- Privilege escalation: `sudo`, `chmod 777 /`, `chown -R`
- Reverse shells: `nc -l`, `ncat -e`, `socat`, `/dev/tcp/`
- Arbitrary code: `python -c`, `perl -e`, `ruby -e`
- Data exfiltration: `curl -d @`, `base64 -d | sh`
- Process detachment: `nohup`, `disown`, `setsid`

Timeouts:
- Most commands: 120 seconds
- Package managers (`pip`, `npm`, `apt`, `yum`, `brew`, `dnf`): 300 seconds

`python3` is automatically rewritten to `python` at execution time.

**Example:**
```
bash_manager: execute, command=pytest tests/ -v
bash_manager: execute, command=pip install requests
```

!!! warning "Always provide 'command'"
    The `command` parameter is mandatory. Without it, `bash_manager` fast-fails with `requires 'command'`. Do not use `cd` — the shell starts in the project root directory.

---

### 9. web_search

| | |
|---|---|
| **Registered name** | `web_search` |
| **Filename** | `tools/web_search.py` |
| **Description** | Search the web using Google Custom Search API. |
| **Status** | **Optional** — requires `GOOGLE_API_KEY` and `GOOGLE_CX` |

**Parameters:**

| Parameter | Required | Description |
|-----------|:---:|-------------|
| `query` | Yes | Search terms |
| `num` | No | Number of results (max 10, default 5) |

**Configuration:** Set in `.env`:
```bash
GOOGLE_API_KEY=your_api_key
GOOGLE_CX=your_custom_search_engine_id
```

If not configured, the tool returns: `"Google Search not configured."`.

**Example:**
```
web_search: search, query=FastAPI dependency injection best practices
```

---

### 10. web_fetch

| | |
|---|---|
| **Registered name** | `web_fetch` |
| **Filename** | `tools/web_fetch.py` |
| **Description** | Fetch the content of a URL and return it as plain text. Useful for reading documentation, blog posts, or any web page. |

**Parameters:**

| Parameter | Required | Description |
|-----------|:---:|-------------|
| `url` | Yes | Full URL (must start with `http://` or `https://`) |

**Example:**
```
web_fetch: fetch, url=https://docs.python.org/3/library/asyncio.html
```

---

### 11. code_search

| | |
|---|---|
| **Registered name** | `code_search` |
| **Filename** | `tools/code_search.py` |
| **Description** | Search for regex patterns in project files. Equivalent to recursive grep. |

**Parameters:**

| Parameter | Required | Description |
|-----------|:---:|-------------|
| `pattern` | Yes | Regex pattern to search for (e.g., `def foo`, `import os`, `class.*Manager`) |
| `path` | No | Search directory relative to project root (default: `.`) |
| `include` | No | File glob to include (default: `*.py`, use `*.*` for all files) |
| `max_results` | No | Max results (default: 20) |

**Example:**
```
code_search: search, pattern=def (get|set)_user
code_search: search, pattern=TODO|FIXME, include=*.py, path=src
```

---

### 12. ask_clarification

| | |
|---|---|
| **Registered name** | `ask_clarification` |
| **Filename** | `tools/ask_clarification.py` |
| **Description** | Allows agents to pause the workflow and ask the user a question. |
| **Status** | **Interception-only** — not in function-calling specs |

This tool is special: it's **intercepted** by the agent loop before normal tool execution. When an agent wants to ask a clarifying question, the workflow pauses, the `PausedSession` is persisted to the database, and the user sees a dialog with the question and optional choices. The workflow resumes when the user answers.

**Parameters:**

| Parameter | Required | Description |
|-----------|:---:|-------------|
| `question` | Yes | The question to ask the user |
| `options` | No | Optional list of choices for the user |

**Example (internal):**
```
ask_clarification: ask, question="Should this be a class-based view or a function-based view?",
options=["Class-based", "Function-based"]
```

---

## Tool Availability by Agent

| Tool | Developer | Analista | Architect | Moderador | Conversacional |
|------|:---:|:---:|:---:|:---:|:---:|
| file_manager | ✅ r/w | ✅ read only | ✅ read only | ❌ | ❌ |
| git_manager | ✅ | ❌ | ❌ | ❌ | ❌ |
| code_exec | ✅ | ❌ | ❌ | ❌ | ❌ |
| lsp_manager | ✅ | ✅ | ✅ | ❌ | ❌ |
| pdf_read | ✅ | ✅ | ✅ | ❌ | ❌ |
| test_runner | ✅ | ❌ | ❌ | ❌ | ❌ |
| diff_editor | ✅ | ❌ | ❌ | ❌ | ❌ |
| bash_manager | ✅ | ❌ | ❌ | ❌ | ❌ |
| web_search | ❌ | ✅ | ✅ | ❌ | ❌ |
| web_fetch | ❌ | ✅ | ✅ | ❌ | ❌ |
| code_search | ❌ | ✅ | ✅ | ❌ | ❌ |
| ask_clarification | ✅ (loop) | ✅ (loop) | ✅ (loop) | ❌ | ❌ |

## MCP Tools

Morphix supports MCP (Model Context Protocol) tools loaded from `mcp_servers.json`. MCP tools are prefixed with `mcp:` in their tool names. In DeepSeek strict mode, colons are sanitized to underscores (e.g., `mcp_browser_browser_navigate`). MCP tools are available to agents based on their allowed tool list via `expand_allowed_tools()`.

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
