# Dashboard

The Dashboard is the landing page of Morphix. It provides an overview of your system, workspace management, and quick access to workflows and agents.

## Layout

The Dashboard is organized into three main areas:

1. **System panel** (left) — Workspace selector, online/offline toggle, metrics, self-reflection setting
2. **Modules panel** (right) — Workflow cards and Agent cards in a grid layout
3. **Bottom row** — Utility buttons: Abrir Logs, lnav, Tema (theme toggle), Reiniciar

## System Panel

### Online/Offline Indicator

Shows current connectivity status:

- **☁ Online** (green) — Connected to DeepSeek (or configured cloud LLM)
- **⛔ Offline** (amber) — Using local Ollama model

The "Activar Offline" / "Desactivar Offline" button toggles between modes. When switching to offline, the application falls back to the configured Ollama model (default: `phi3:mini`).

### Workspace Selector

A dropdown listing all available PostgreSQL workspaces. Workspaces are separate database schemas with their own agents, workflows, tools, and conversations.

- Select a workspace from the dropdown to switch to it — this reloads the workspace's agents and tools
- Click `+ Nuevo` to create a new workspace. Enter a name (must start with a letter, lowercase, numbers and underscores only). The workspace is created as a new PostgreSQL schema.
- Switching workspaces updates all tabs: the Maestro tab reloads agent configurations, the Editor tab changes project scope, and the History tab shows conversations from the new workspace

### Metrics

Shows key runtime statistics:

- **Tokens**: Total tokens used across all conversations
- **Workflows**: Completed / Total workflows with completion ratio
- **Uptime**: Seconds since the application started

### Self-Reflection Toggle

A checkbox labeled "Self-Reflection (agentes se auto-revisan)". When enabled, agents review their own outputs for quality and correctness before returning results. This is controlled by the `AGENT_SELF_REFLECTION` feature flag.

## Modules Panel

### Workflow Cards

Each available workflow is displayed as a clickable card with the workflow name and a description excerpt (up to 80 characters). Clicking a workflow card:

1. Activates the workflow
2. Switches to the Maestro tab
3. Sets Maestro to Orchestrate mode
4. Displays the workflow description in chat

Available workflows depend on what's installed in the current workspace:

| Workflow | Description |
|----------|-------------|
| **development** | Full orchestration: decompose → route → execute → supervise → aggregate |
| **coordinated** | DAG-based parallel execution with shared blackboard |
| **collaborative** | Multi-agent panel debate with moderator consensus |
| **tdd** | Test-driven development loop: write tests → run → fix → repeat |

### Agent Cards

Each registered agent is displayed as a clickable card in a 2-column grid layout. Each card shows:

- Agent name (capitalized)
- Tool count (e.g., "(6 tools)")

Clicking an agent card:

1. Selects that agent
2. Switches to the Maestro tab
3. Sets Maestro to Chat mode
4. Starts a direct conversation with that agent

Available agents:

| Agent | Type | Best for |
|-------|------|----------|
| **Developer** | agent | Coding, building, testing |
| **Analista** | reasoning | Analysis, review, architecture |
| **Moderador** | reasoning | Debate moderation, consensus |
| **Conversacional** | agent | Quick chat, fallback agent |

!!! tip "Agent vs Workflow"
    Click a **workflow card** when you have a multi-step task that needs planning and execution across multiple agents. Click an **agent card** when you want to chat directly with a specific agent.

## Bottom Row Actions

| Button | Action |
|--------|--------|
| **Abrir Logs** | Opens the `logs/morphix.log` file in the system's default text editor |
| **lnav** | Opens logs with `lnav` (Log Navigator) if installed — provides a terminal-based log viewer with syntax highlighting |
| **Tema** | Toggles dark mode on/off. Requires restart to apply fully. |
| **Reiniciar** | Restarts the Morphix application |

## Getting Started

1. Pick a **workflow card** from the modules panel (e.g., "development")
2. Morphix switches to the **Maestro tab** automatically
3. Create or select a **project** using the project dropdown in the top bar
4. Type your task in the input field and press **Ctrl+Enter**
5. Watch the orchestration unfold in the execution panel and chat

For quick questions or single-agent tasks, click an agent card instead and type your question in Chat mode.
