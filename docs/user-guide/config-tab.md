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

- Title shows the total count: "🔧 24 herramientas:"
- Each tool is listed with its registered name and a description excerpt (first 80 characters)
- The descriptions come from `TOOL_DEFINITIONS` in `tools/specs.py`

Morphix registers **24 tools** — file management, git, bash, LSP, sandboxed code execution, test runner, diff editor, file viewer, memory inspector, project docs, vision, web search/fetch, code search, skill loader, the goal/todo family, and plan mode. `ask_clarification` is not listed because it is intercepted in the agent loop rather than invoked via LLM function-calling. See the [Tools reference](tools.md) for the full table.

## Sistema Tab

Live system resource monitor for CPU and memory.

### On-Demand Monitoring

The monitor **starts stopped** — nothing polls until you ask for it:

- Click **▶ Actualizar** to start: it takes a first reading immediately and then refreshes periodically.
- The button changes to **⏹ Detener** — click it to stop the monitor.
- Leaving the Config tab (hide) also stops the monitor automatically.

### Readings

- **CPU Usage** — progress bar with current CPU utilization (`psutil.cpu_percent()`).
- **Memory Usage** — progress bar with current RAM utilization (`psutil.virtual_memory().percent`).

Both bars use the accent color (`#1066ae`) for the filled portion.

!!! tip "Monitoring during workflows"
    Start the monitor (▶) during heavy workflows to watch CPU and memory usage in real time. It stops on its own when you switch tabs, so it never polls in the background for nothing.

## Connection Status

The main window's **sidebar footer** shows the workspace selector next to a status dot:

- **● green** — online (DeepSeek/OpenAI reachable).
- **● amber** — offline mode (Ollama only).

Toggle offline mode from the Dashboard or the Maestro top bar.

## Key Configuration Files

The Config tab displays information from these sources:

| Source | Location | Purpose |
|--------|----------|---------|
| `.env` | Project root | Environment variables (API keys, URLs, feature flags) |
| `core/config.py` | `core/` | Pydantic settings model that loads `.env` |
| `tools/specs.py` | `tools/` | `TOOL_DEFINITIONS` dict with function-calling specs |
