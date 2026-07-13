# GUI Overview

Morphix's desktop interface is a PySide6 application with a fixed tab layout. This page describes the main window, all 6 tabs, and basic navigation patterns.

## Main Window

The main window has a minimum size of 1200x750 pixels and uses a dark theme by default. It consists of:

- **Menu bar** — "Archivo" (File) with Ctrl+Q to quit, and "Ayuda" (Help) with About and Keyboard Shortcuts dialogs
- **Tab bar** — 6 fixed tabs across the top (not user-draggable or reorderable)
- **Status bar** — Shows current workspace name ("Workspace: main") and status messages

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+Q | Quit application |
| Ctrl+Enter | Send message in Maestro |
| Shift+Enter | New line in Maestro input |

### Authentication

On launch, Morphix shows a login dialog requiring the master password (configured via `PASSWORD_HASH` in `.env`). This uses bcrypt for verification. After successful login, the backend initialises and loads the real tabs.

## The 6 Tabs

### 1. Dashboard
The landing page. Shows workspace selector, system status (Online/Offline), workflow cards, and agent cards. Use this to pick a workflow, select an agent, or switch workspaces.

[Read the Dashboard guide →](dashboard.md)

### 2. Maestro
The orchestration cockpit. This is where you type tasks, send messages, watch the orchestration unfold, and inspect results. It has a 3-column layout: execution panel (left), chat (center), and detail panel (right).

[Read the Maestro guide →](maestro-cockpit.md)

### 3. Editor
A built-in file browser and text editor. Shows the active project's directory tree on the left and opens files for editing on the right. Files are filtered to hide noise (`.git`, `__pycache__`, `node_modules`, etc.).

[Read the Editor guide →](editor-tab.md)

### 4. History
Lists all saved conversations across workspaces. Select a conversation to see its full message history. Export to Markdown, JSON, or PDF. Click "Continuar" to resume a conversation in the Maestro tab.

[Read the History guide →](history-tab.md)

### 5. Config
Shows current configuration: model roles (DeepSeek / Ollama), registered tools, and a live CPU + memory monitor that updates every 3 seconds.

[Read the Config guide →](config-tab.md)

### 6. Analytics
Real-time metrics dashboard. Token usage, workflow completion stats, LLM call count, tool call count, and rate limiter quotas (per-minute and per-hour). Refreshes every 5 seconds.

[Read the Analytics guide →](analytics-tab.md)

## Navigation Between Tabs

Tabs are always visible at the top of the window. Click any tab to switch. Some interactions navigate automatically:

- **Dashboard → Maestro**: Clicking a workflow or agent card switches to the Maestro tab and activates the selected workflow or agent
- **History → Maestro**: Clicking "Continuar" on a conversation loads it into Maestro and switches there
- **Project changes**: When you create or select a project in Maestro, the Editor tab automatically refreshes to show that project's file tree

## Online vs Offline Mode

The mode indicator appears in both the Dashboard and Maestro tabs:

| Mode | Icon | Color | LLM Used |
|------|------|-------|----------|
| Online | ☁ | Green | DeepSeek (or configured OpenAI-compatible provider) |
| Offline | ⛔ | Amber | Ollama (local LLM, defaults to `phi3:mini`) |

Toggle offline mode using the button in either tab. Offline mode disables external API calls and forces local model usage.

!!! tip "When to use offline mode"
    Use offline mode when you don't have an internet connection, when you want privacy (no data leaves your machine), or when you want to save API costs during experimentation.

## Dark Mode

Dark mode is enabled by default (`DARK_MODE=true` in `.env`). You can toggle it from the Dashboard's "Tema" button. The change takes effect after restarting the application.
