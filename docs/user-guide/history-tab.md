# History Tab

The History tab shows all saved conversations across workspaces. You can browse, read, export, delete, and resume past conversations.

## Layout: 2-Column

| Column | Width | Content |
|--------|-------|---------|
| Left | 260px minimum | Conversation list, refresh button |
| Right | Flexible | Conversation detail view, export/delete/resume actions |

## Conversation List

The left panel shows all saved conversations with:

- **Conversation ID** in brackets (e.g., `[42]`)
- **Title** — the first user message or a generated summary

Click a conversation to load its full message history in the detail panel. Click "Refrescar" to reload the list from the database.

## Detail Panel

When a conversation is selected, the detail panel shows its contents:

### Message Display

All messages are rendered in Markdown with role labels:

- **USER:** — User messages
- **ASSISTANT:** — Assistant/agent responses
- **SYSTEM:** — System notifications
- **TOOL:** — Tool execution results

Messages are separated by horizontal rules (`---`).

### Action Buttons

| Button | Action |
|--------|--------|
| **Exportar** | Export the conversation in the selected format |
| **Eliminar** | Delete the conversation permanently |
| **Continuar** | Load the conversation into the Maestro tab and resume it |

### Format Selector

Before exporting, choose the output format from the dropdown:

| Format | Description |
|--------|-------------|
| `md` | Markdown document |
| `json` | Structured JSON array |
| `pdf` | PDF document |

!!! note "HTML export"
    HTML export is available from the Maestro tab's Descargar button, which includes Pygments syntax highlighting. The History tab's export uses the repository's `export()` method which supports Markdown, JSON, and PDF.

### Resuming a Conversation

Click **Continuar** to:

1. Load all messages from the conversation into the Maestro tab
2. Switch to the Maestro tab automatically
3. The conversation ID is set, so the next message continues the same database conversation
4. Full context is preserved — the decomposer and TaskAnalyzer receive `is_follow_up: true`, adapting subtasks for modification rather than creation

## Exporting Conversations

Exports are project-scoped — they read files from the project's directory on disk, ensuring real content (not fabricated data). The `ConversationRepository.export()` method:

1. Reads the conversation from the database
2. If a project is associated, reads actual file contents from disk
3. Strips internal watermarks (anti-distillation patterns)
4. Writes the export file to the `exports/` directory

!!! tip "Export location"
    Exported files are saved to the `exports/` directory within the Morphix data directory. The status label shows the full path after export.

## Deleting Conversations

Click **Eliminar** to permanently delete a conversation. This removes it from the database. The action is immediate — there is no confirmation dialog. After deletion, the list automatically refreshes.

## Filters (Coming in Future Version)

The v1 History tab shows all conversations. Future versions will add filters for:

- By date range
- By workspace
- By workflow type
