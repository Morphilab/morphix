# Dashboard

The Dashboard is the landing page of Morphix. It gives you a quick launcher, the workflow and agent catalogs, project management, and bot chips.

## Layout

The Dashboard is organized into:

1. **Header** — "¿Qué quieres hacer?" launcher: type a task and press Enter to jump to the Maestro tab with it pre-filled
2. **Left panel** — **WORKFLOWS** grid (scrollable cards) and **AGENTES** chips
3. **Right panel** — **PROYECTOS** selector with creation buttons, and **BOTS** chips
4. **Footer** — mode dot, offline toggle, self-reflection checkbox, and utility buttons

The Dashboard refreshes each time you open it, so cards created in Bots or Maestro appear immediately.

## Footer

### Online/Offline Indicator

The **● mode dot** in the footer shows current connectivity:

- **● green** — Connected to DeepSeek (or configured cloud LLM)
- **● amber** — Using the local Ollama model

The "Activar Offline" / "Desactivar Offline" button toggles between modes. When switching to offline, the application falls back to the configured Ollama model (default: `phi3:mini`).

### Self-Reflection Toggle

A checkbox labeled "Self-Reflection". When enabled, agents review their own outputs for quality and correctness before returning results. This is controlled by the `AGENT_SELF_REFLECTION` feature flag.

### Utility Buttons

| Button | Action |
|--------|--------|
| **Logs** | Opens the `logs/morphix.log` file in the system's default text editor |
| **lnav** | Opens logs with `lnav` (Log Navigator) if installed — terminal-based log viewer with syntax highlighting |

## Workflows Panel

Each available workflow is displayed as a clickable card with the workflow name and a description excerpt. Clicking a card:

1. Activates that workflow
2. Switches to the Maestro tab in Orchestrate mode

The cards come from the workflow presets installed in the current workspace (`templates/workflows/`, copied to `workspaces/<name>/workflows/`). The nine bundled presets:

| Preset | Description |
|--------|-------------|
| **development** | General coding tasks: decompose → execute → aggregate |
| **coordinated** | Multi-agent execution with subtasks in parallel (up to 5) |
| **collaborative** | Panel debate with moderator consensus (no project needed) |
| **tdd** | Test-driven loop — iterates until all tests pass |
| **bdd** | Gherkin stories → failing test per story → minimal implementation |
| **sdd** | Spec-first with review gates and traced implementation |
| **edd** | Eval-driven loop — numeric metrics decide when to stop |
| **domain_tdd** | Domain model → critical scenarios → TDD cycle per scenario |
| **reflexion** | Generator–critic refinement loop |

All presets except **collaborative** require a selected project. See [Workflows Overview](workflows.md) for details.

## Agentes Panel

Each registered agent appears as a chip showing its name. Clicking an agent chip:

1. Selects that agent
2. Switches to the Maestro tab in Chat mode
3. Starts a direct conversation with that agent

The five built-in agents: **developer** (coding/testing), **analista** (read-only analysis), **architect** (design and planning), **conversacional** (general chat), and **moderador** (debate synthesis). See [Agents](agents.md).

## Projects Panel

The **PROYECTOS** section lists the projects of the current workspace. Click a project to select it (jumps to Maestro). Buttons:

| Button | Action |
|--------|--------|
| **＋ Nuevo** | Create a new project directory under `code_projects/<name>/` |
| **⌂ Importar** | Copy an existing directory into the workspace as a project |
| **⤓ Clonar** | Clone a Git repository into the workspace as a project |

See [Projects & Workspaces](projects.md).

## Bots Panel

The **BOTS** section shows the workspace's bot roster as chips. Clicking a bot opens its eternal chat in Maestro. Manage the roster (create, edit, routines, group rooms) in the **Bots** tab.

!!! tip "Agent vs Workflow"
    Click a **workflow card** when you have a multi-step task that needs planning and execution across agents. Click an **agent chip** when you want to chat directly with a specific agent.
