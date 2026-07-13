# Config Tab

The Config tab shows the current configuration of Morphix — which LLM models are active, what tools are registered, and live system resource usage.

## Layout: Inner Tabs

The Config tab contains three sub-tabs:

| Sub-tab | Content |
|---------|---------|
| **Modelos** | LLM model configuration per role |
| **Herramientas** | List of registered tools with descriptions |
| **Sistema** | Live CPU and Memory monitor |

## Modelos Tab

Displays the LLM configuration for each role. The model roles are defined in `settings.model_roles` in `core/config.py`.

Each role shows:
- **Role name** — the purpose of the role (e.g., `default`, `fast`, `reasoning`, `agent`, `creative`, `critique`)
- **Provider** — which LLM provider is used (DeepSeek, OpenAI, Ollama)
- **Model** — the specific model name (default: `deepseek-v4-flash` for all roles)
- **Temperature** — the sampling temperature for that role

Additional configuration displayed:
- **Ollama** — the Ollama model and base URL (for offline fallback, default: `phi3:mini` at `http://localhost:11434`)
- **Timeout** — LLM request timeout in seconds (default: 60s)

!!! note "Configuration source"
    These values come from your `.env` file at the project root. To change them, edit `.env` and restart Morphix. Key environment variables:
    - `DEEPSEEK_API_KEY` — your DeepSeek API key
    - `OPENAI_API_KEY` — OpenAI key (optional, for OpenAI provider)
    - `OLLAMA_MODEL` — model name for Ollama (default: `phi3:mini`)
    - `OLLAMA_BASE_URL` — Ollama server URL (default: `http://localhost:11434`)
    - `LLM_TIMEOUT` — request timeout in seconds (default: 60)

## Herramientas Tab

Lists all registered tools with their names and descriptions:

- Title shows total count: "🔧 12 herramientas:"
- Each tool is listed with its registered name and a description excerpt (first 80 characters)
- The descriptions come from `TOOL_DEFINITIONS` in `tools/specs.py`

Registered tools include:

| Tool | Purpose |
|------|---------|
| `file_manager` | Read, write, append, delete files |
| `bash_manager` | Execute shell commands |
| `git_manager` | Git operations (init, add, commit, log, diff) |
| `test_runner` | Run test suites |
| `lsp_manager` | Language server protocol (definitions, diagnostics, hover) |
| `code_exec` | Execute Python in a RestrictedPython sandbox |
| `diff_editor` | Create and apply unified diffs |
| `web_search` | Search the web (Google CSE) |
| `web_fetch` | Fetch and extract page content |
| `code_search` | Pattern search across the codebase |
| `pdf_read` | Extract text from PDF files |
| `ask_clarification` | Pause workflow and ask user a question |

!!! note "ask_clarification is special"
    `ask_clarification` does not appear in `TOOL_DEFINITIONS` because it's intercepted directly in the agent loop rather than invoked via LLM function-calling.

## Sistema Tab

Live system resource monitor that updates every 3 seconds:

### CPU Usage

A progress bar showing current CPU utilization (percentage). Uses `psutil.cpu_percent()`.

### Memory Usage

A progress bar showing current RAM utilization (percentage). Uses `psutil.virtual_memory().percent`.

Both bars use the accent color (`#1066ae`) for the filled portion. The monitor refreshes automatically every 3 seconds via a QTimer.

!!! tip "Monitoring during workflows"
    Keep the Sistema tab open during heavy workflows to watch CPU and memory usage in real time. This helps identify when the system is under load from indexing, LLM processing, or tool execution.

## Connection Status (Status Bar)

While not in the Config tab itself, the main window's status bar provides connection status indicators:

- **Database**: Green when connected to PostgreSQL. If the connection fails, workflows cannot execute.
- **LLM**: Green when online (DeepSeek/OpenAI reachable). Amber when in offline mode (Ollama only).
- **Redis**: Optional — if configured, shown as connected/disconnected.

## Key Configuration Files

The Config tab displays information from these sources:

| Source | Location | Purpose |
|--------|----------|---------|
| `.env` | Project root | Environment variables (API keys, URLs, feature flags) |
| `core/config.py` | `core/` | Pydantic settings model that loads `.env` |
| `tools/specs.py` | `tools/` | `TOOL_DEFINITIONS` dict with function-calling specs |
