# GUI Overview

Morphix's desktop interface is a PySide6 application with a fixed sidebar/tab layout. This page describes the main window, all 8 tabs, and basic navigation patterns.

## Main Window

The main window has a minimum size of 1200x750 pixels and uses a dark theme by default. It consists of:

- **Menu bar** — "Archivo" (File) with Ctrl+Q to quit, and "Ayuda" (Help) with About and Keyboard Shortcuts dialogs
- **Sidebar** — 8 fixed entries (Dashboard, Maestro, Historial, Editor, Config, Analytics, Memoria, Bots); at the bottom, the **workspace selector** with an online/offline status dot
- **Status bar** — Shows status messages (workspace switches, export paths, errors)

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+Q | Quit application |
| Ctrl+Enter | Send message in Maestro |
| Shift+Enter | New line in Maestro input |

### Authentication

On launch, Morphix shows a login dialog requiring the master password (configured via `PASSWORD_HASH` in `.env`). This uses bcrypt for verification. After successful login, the backend initialises and loads the real tabs.

## The 8 Tabs

### 1. Dashboard
The landing page. Shows workspace selector, system status (Online/Offline), workflow cards, and agent cards. Use this to pick a workflow, select an agent, or switch workspaces.

[Read the Dashboard guide →](dashboard.md)

### 2. Maestro
The orchestration cockpit. This is where you type tasks, send messages, watch the orchestration unfold, and inspect results. It has a 2-column layout: chat on the left, and a unified activity panel on the right (collapsible sections: Ejecución / Subtareas / Archivos, plus Diagrama / Log / Bash tabs).

[Read the Maestro guide →](maestro-cockpit.md)

### 3. Editor
A built-in file browser and text editor. Shows the active project's directory tree on the left and opens files for editing on the right. Files are filtered to hide noise (`.git`, `__pycache__`, `node_modules`, etc.).

[Read the Editor guide →](editor-tab.md)

### 4. History
Lists all saved conversations across workspaces. Select a conversation to see its full message history. Export to Markdown, JSON, or PDF. Click "Continuar" to resume a conversation in the Maestro tab.

[Read the History guide →](history-tab.md)

### 5. Config
Shows current configuration: model roles (DeepSeek / Ollama), the 24 registered tools, and an on-demand CPU + memory monitor (starts stopped; ▶ Actualizar / ⏹ Detener).

[Read the Config guide →](config-tab.md)

### 6. Analytics
Metrics dashboard: token usage, workflow completion stats, LLM call count, tool call count, and rate limiter quotas (per-minute and per-hour). Refresh is **on-demand**: press ▶ Actualizar to go live, ⏹ Detener to stop; it auto-stops when you leave the tab.

[Read the Analytics guide →](analytics-tab.md)

### 7. Memoria
Browse the workspace's persistent memory: list stored entries, read their content, and delete entries (with confirmation). Refresh with ⟳; it follows workspace switches.

### 8. Bots
Bot Mode: manage the roster of bots (create/edit/clone via YAML), their eternal chats, routines (scheduled prompts), and group rooms. See the [Bot Mode](../bot-mode.md) page.

## Navigation Between Tabs

Tabs are always visible in the left sidebar. Click any entry to switch. Some interactions navigate automatically:

- **Dashboard → Maestro**: Clicking a workflow card or the launcher switches to the Maestro tab and activates the selected workflow or agent
- **History → Maestro**: Clicking "Continuar" on a conversation loads it into Maestro and switches there
- **Project changes**: When you create or select a project, the Editor tab automatically refreshes to show that project's file tree

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

Dark mode is enabled by default (`DARK_MODE=true` in `.env`). The theme is fixed at startup — there is no runtime theme switcher.
