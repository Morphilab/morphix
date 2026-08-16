# Maestro Cockpit

The Maestro tab is the orchestration cockpit — where you interact with Morphix, send tasks, and watch multi-agent workflows execute in real time.

## Layout: 2-Column with Resizable Splitter

The Maestro tab has a 2-column layout with a `QSplitter` for resizable columns (double-click a handle to collapse a column):

| Column | Width | Content |
|--------|-------|---------|
| Left (Chat) | Flexible (~3/5) | Chat blocks with streaming text, agent debate blocks, input field, send button |
| Right (Actividad) | ~1/5 (resizable) | Collapsible sections: Ejecución (progress + stat chips), Subtareas, Archivos creados — plus a QTabWidget below: Diagrama, Log, Bash |

## Top Bar

The top bar is a single compact row that spans both columns and contains:

### Controls

- **Estado**: Shows "Online" (green) or "Offline" (amber)
- **Workspace**: Shows the active workspace name (e.g., `ws: main`)
- **Modo**: Two toggle buttons — `💬 Chat` (active) and `⚙️ Orquestar` (inactive). These switch between conversation mode and workflow orchestration mode.
- **Proyecto**: Dropdown to select or create projects. Projects are stored in `code_projects/<name>/`. Buttons: `➕ Nuevo` (create new project) and `📂 Importar` (import existing directory).
- **Agente**: Dropdown to select which agent to use. Options include "🤖 Auto" (let the system choose) and each registered agent (Developer, Analista, Moderador, Conversacional). In Orchestrate mode, the list is filtered to agents allowed by the active workflow.
- **Workflow label**: Shows the active workflow name (e.g., `workflow: development`)

### Actions

- **⚡ Pre-cargar proyecto**: Indexes the current project into FAISS for semantic code search. Shows a progress bar during indexing. Requires a selected project.
- **Limpiar**: Clears the entire chat and resets the cockpit
- **Descargar**: Exports the current conversation. Format selector: `md`, `json`, `pdf`, `html`
- **✚ Nueva conversación**: Resets the conversation ID and clears chat
- **Activar/Desactivar Offline**: Toggles offline mode

## Right Column: Actividad (Activity Panel)

### Ejecución Section

The stats chips show real-time execution metrics from the normalized stats contract (all workflows emit the same fields):

| Stat | Description |
|------|-------------|
| ⏱ Elapsed time | Time since workflow started |
| ⚡ Tokens used | Total tokens consumed (prompt + completion) |
| 🧠 Current agent | Agent currently executing |
| 🚦 Status | Workflow status — "completado" turns green |
| 📂 Phase | Current phase (design/implement/verify, Descomponiendo/Ejecutando/Verificando/Sintetizando, Ronda N/M, Iteración N/M) |

The progress bar fills as subtasks complete (`completed / total`). The Ejecución section is always visible.

### Subtask List Section

Collapsible (auto-expands when the workflow emits real steps). Lists each subtask with a status icon:

| Icon | Status | Meaning |
|------|--------|---------|
| ✅ | completed | Subtask finished successfully |
| 🔵 | running | Subtask currently being executed |
| ❌ | failed | Subtask encountered an error |
| ⏳ | pending | Subtask not yet started |

### Archivos creados Section

Collapsible (auto-expands when files are written). Shows files created or modified during the current workflow. Double-click a file to open it in the Editor tab.

## Left Column: Chat

### Chat Blocks

Messages appear as full-width dense blocks with role headers:

- **User messages**: "You" header in blue accent, full-width markdown content
- **Assistant messages**: "Morphix" header in green, full-width markdown content
- **System messages**: Dimmed gray text, centered
- **Agent messages**: Agent name as header, full-width with agent-specific color

### Streaming

Assistant responses stream in real time. The text updates progressively with ~70ms debounce to keep rendering smooth. A "Generando..." animation with animated dots appears while the assistant is thinking.

### Input Area

- Multi-line text input (QTextEdit) with placeholder: "¿Qué quieres que coordine el Maestro?"
- **Ctrl+Enter**: Send message
- **Shift+Enter**: Insert newline
- Optional PDF path field with "Cargar" button — loads a PDF and includes its text in the next message
- Blue "Enviar" button

## Right Column: Detalle (Detail Tabs)

A QTabWidget below the collapsible sections, with 3 tabs:

### 1. Diagrama Tab

Phase cards derived locally from each `stats_update` (`subtask_list` grouped by phase). Shows the current workflow structure with status cards (✅🔵❌⏳). Agent debate during collaborative and coordinated workflows streams live in the chat instead (universal `agent_stream` blocks).

### 2. Log Tab

Detailed execution log with timestamps. Each entry follows the format:

```
HH:MM:SS  message text
```

Log entries include system messages, tool execution notifications, agent transitions, and error reports. The log is capped at 400 blocks to prevent memory issues. The `[bash_manager]` prefix in log messages also updates the Bash tab.

### 3. Bash Tab

Shows shell command output from the `bash_manager` tool. Uses a monospace font on a near-black background (`#0A0A0A`) with green text (`#22C55E`). Content is truncated to the last 5000 characters. Shows "(sin comandos ejecutados aún)" when empty. The tab is hidden when the active workflow's allowlist does not include `bash_manager` (e.g., collaborative).

## Chat Mode vs Orchestrate Mode

### Chat Mode (`💬 Chat`)

- Direct conversation with a single agent
- The agent combo dropdown selects which agent to chat with
- "🤖 Auto" uses the default agent (`conversacional`)
- No workflow orchestration — the agent responds directly to your messages
- Use this for quick questions, code explanations, or brainstorming

### Orchestrate Mode (`⚙️ Orquestar`)

- Full multi-agent workflow orchestration
- The system chooses the best agent for each subtask
- Agent combo is filtered to agents allowed by the active workflow
- Requires a project to be selected (except for collaborative workflows)
- Dispatches to one of 4 workflow routes depending on the active workflow:
    1. **Direct tool command** — if message matches `tool_name: action, key=val` format
    2. **TDD loop** — if the active workflow is "tdd"
    3. **Full orchestration** — development/coordinated workflows decompose tasks, route to agents, supervise, and aggregate
    4. **Simple conversation** — if `TaskAnalyzer` determines orchestration isn't needed

!!! tip "Which mode should I use?"
    Use **Chat** for quick questions, code review, or single-agent tasks. Use **Orchestrate** for multi-step development tasks (build a feature, refactor code, run tests). Orchestrate mode decomposes your task into subtasks and assigns each to the best agent.

## Clarification Requests (Sprint 21)

When an agent needs more information during a workflow, it can pause and ask you a question:

1. A system message appears in chat asking the clarification
2. The workflow pauses — state is saved to a `PausedSession` in the database
3. Type your answer in the input field and press Send
4. The workflow resumes from the pause point, injecting your answer as context

Clarification requests survive application restarts. If you close Morphix during a pause, the session is restored on next launch.

!!! note "How clarification works"
    The `ask_clarification` tool is intercepted directly in the agent loop (`orchestration/loop.py`) rather than via function-calling. It bypasses the normal tool execution path and emits a pause signal to the orchestrator.

## Project Management

### Creating a Project

Click `➕ Nuevo` in the top bar. Enter a name (lowercase, numbers, underscores). A directory is created under `code_projects/<name>/` and becomes the active project. The mode automatically switches to Orchestrate.

### Importing a Project

Click `📂 Importar`. Select an existing directory. Its contents are copied to `code_projects/<name>/`. The project becomes active and available in the dropdown.

### Pre-loading a Project

Click `⚡ Pre-cargar proyecto` to index the project into FAISS. This enables semantic code search during workflows. A progress bar shows indexing progress (files scanned and percentage complete). After completion, the status shows the number of indexed chunks.

## Downloading Conversations

Click `Descargar` to export the current conversation. Select the format from the dropdown:

| Format | Description |
|--------|-------------|
| `md` | Markdown with role-labeled sections (👤 Usuario, 🤖 Maestro, 🧠 Agente, 🔧 Herramienta) |
| `json` | Structured JSON array with role, content, and agent metadata |
| `pdf` | PDF document generated with ReportLab |
| `html` | Styled HTML page with Pygments syntax highlighting for code blocks |

Exports strip internal system messages (anti-frustration rules, identity prompts, bash_manager prefixes). If a conversation is saved in the database (has a conversation ID), the export delegates to `ConversationRepository.export()` which reads from disk and strips watermarks.
