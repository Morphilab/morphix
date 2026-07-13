# Projects & Workspaces

## Workspaces

A **workspace** in Morphix is an isolated environment with its own:

- **PostgreSQL schema** — Separate database tables for conversations, messages, workflows, users, and blackboard entries
- **Agents** — Custom agent profiles (loaded from `workspaces/<name>/agents/`)
- **Workflows** — Custom workflow templates (loaded from `workspaces/<name>/workflows/`)
- **Tools** — Custom Python tools (loaded from `workspaces/<name>/tools/`)
- **Hooks** — Lifecycle hooks (loaded from `workspaces/<name>/hooks/`)
- **MCP servers** — Configured in `workspaces/<name>/mcp_servers.json`

Workspaces are PostgreSQL schemas named with lowercase letters, numbers, and underscores (`[a-z][a-z0-9_]*`). Schema creation and table setup happen automatically when you switch to a workspace.

### How Workspace Switching Works

When you switch workspaces:

1. The database `search_path` is set to the target schema
2. If the schema doesn't exist, it's created with all required tables
3. Workspace agents are unloaded from the global registry
4. New workspace's agents are loaded from `workspaces/<name>/agents/`
5. Workspace tools are loaded from `workspaces/<name>/tools/`
6. All tabs refresh to reflect the new workspace

### Workspace Directory Structure

```
workspaces/<name>/
├── agents/
│   ├── developer.yaml
│   ├── analista.yaml
│   └── custom_agent.yaml
├── workflows/
│   ├── development.yaml
│   └── coordinated.yaml
├── tools/
│   └── custom_tool.py
├── hooks/
│   └── on_startup.py
└── mcp_servers.json
```

On first workspace switch, default templates are copied from `templates/agents/` and `templates/workflows/` to the workspace directory.

## Creating a New Project

Projects in Morphix are directories within the workspace's `code_projects/` folder that contain your source code, tests, and configuration files.

### From the Maestro Tab

1. In the Maestro top bar, find the **Proyecto** (Project) dropdown
2. Click **➕ Nuevo** (New)
3. Enter a project name (lowercase, letters/numbers/underscores/hyphens)
4. The project directory is created at `code_projects/<name>/`
5. The project is selected and ready for use

### From the Dashboard

1. Create or select a workspace
2. Switch to the Maestro tab (Dashboard → click any workflow/agent card)
3. Use the project dropdown to create a project

## Importing an Existing Project

1. In the Maestro top bar, click **📂 Importar** (Import)
2. Select an existing directory from your filesystem
3. The directory is symlinked or copied into `code_projects/<name>/`
4. The project is selected and ready for use

Imported projects retain their existing `.git` history, dependencies, and file structure.

## Switching Between Projects

1. Click the **Proyecto** dropdown in the Maestro top bar
2. Select a different project
3. The Editor tab updates to show the new project's file tree
4. Subsequent workflow executions use the new project's root directory

You can switch projects at any time — active conversations remain associated with the original project.

## Pre-Loaded Projects

The default `main` workspace comes with no pre-loaded code projects. Create your first project via the Dashboard workflow:

1. Click **Development** workflow card
2. Create a project via the Maestro dropdown
3. Type your first task to start building

## How Projects Relate to Workflows

| Workflow | Project behavior |
|----------|-----------------|
| **Development** | Optional. Agents can create files directly in the project root. Without a project, files are written to the workspace memory directory. |
| **Coordinated** | Optional. Phase-aware execution uses project context for file operations. Without a project, blackboard context is the only coordination mechanism. |
| **Collaborative** | Optional. If set, agents receive the project's structure and dependencies as debate context. Without a project, the debate is purely based on the question. |
| **TDD** | Recommended. Test discovery uses the project root. Without a project, green-field mode always triggers (no test files found). |

!!! tip "Always set a project for code work"
    For Development, Coordinated, and TDD workflows, having a project selected ensures:
    - Files are created in an organized location
    - Tests can be discovered and run
    - Git operations target the right repository
    - LSP diagnostics have the correct project context

## Database Isolation

Each workspace has its own PostgreSQL schema with these tables:

| Table | Purpose |
|-------|---------|
| `conversations` | Conversation metadata (ID, title, timestamps) |
| `messages` | Individual messages with role, content, token count |
| `workflows` | Workflow execution records |
| `users` | Workspace-specific user data |
| `paused_sessions` | Saved state for clarification pauses |
| `blackboard_entries` | Persisted blackboard context for coordinated workflows |

This means:
- Conversations in `workspace_a` never leak into `workspace_b`
- Each workspace can have different agents and tools
- You can have a `work` workspace and a `personal` workspace with different models

## Workspace FAQ

**Can I share agents between workspaces?**
No — each workspace loads its own agent YAML files. Copy agent templates between workspace directories if needed.

**Can I move a conversation to another workspace?**
Not directly. Export the conversation from the History tab, then re-import context into the new workspace.

**What happens if I delete a workspace?**
Drop the PostgreSQL schema. All conversations, workflows, and agents in that workspace are permanently deleted.

**Can I have different API keys per workspace?**
API keys are global (in `.env`), not per-workspace. Model configuration applies across all workspaces.
