# Export

Morphix can export conversations in four formats: **Markdown**, **JSON**, **PDF**, and **HTML**. Exports include the full conversation history, agent messages, tool outputs, files written, and workflow scorecards — all scoped to the current workspace.

## Export Formats

### Markdown (`.md`)

Plain Markdown with conversation structure preserved. Each message is rendered as a block with role, agent name, and content.

```markdown
# Morphix Conversation — 2026-06-24 14:30

## User
Create a REST API with FastAPI for managing a todo list...

## Assistant (Developer)
Here's the implementation...

## Agent Message (Analista — Subtask 3: Review the API design)
The API structure follows REST conventions...
```

### JSON (`.json`)

Structured export with full metadata:

```json
{
  "conversation_id": 42,
  "workspace": "main",
  "created_at": "2026-06-24T14:30:00",
  "title": "FastAPI Todo API",
  "messages": [
    {"role": "user", "content": "...", "timestamp": "..."},
    {"role": "assistant", "content": "...", "timestamp": "..."},
    {"role": "agent", "content": "[Analista — Review API]\n...", "timestamp": "..."}
  ],
  "scorecard": {
    "subtasks": 5,
    "completed": 5,
    "tokens": 15234,
    "time": "45.2s"
  },
  "files_written": ["src/main.py", "src/models.py", "tests/test_api.py"]
}
```

### PDF (`.pdf`)

Formatted PDF with headers, monospace code blocks, and page breaks between major sections. Uses `fpdf2` for generation.

### HTML (`.html`)

Standalone HTML page with:
- **Pygments syntax highlighting** for code blocks (Python, shell, JSON, YAML, and others)
- Responsive layout with conversation bubbles
- Role-based color coding (user = blue, assistant = green, agent = amber, tool = gray)
- Print-friendly CSS

!!! note "Pygments dependency"
    Pygments is lazy-imported. If not installed, code blocks render without syntax highlighting but the HTML export still works. Install with: `pip install pygments`

## What's Included

### Conversation Messages

All messages from the conversation are included:

| Role | Description |
|------|-------------|
| `user` | Your original prompts and follow-ups |
| `assistant` | Morphix's responses |
| `agent` | Internal agent messages (subtask execution, analysis) |
| `tool` | Tool execution results (files written, commands run) |

### Agent Messages

During orchestrated workflows, internal agent-to-agent messages are appended to the export history. These show what each agent did during its subtask:

```
[Developer — Create SQLAlchemy models for Todo items]
Created models.py with Todo and Tag models using SQLAlchemy 2.0 style...

[Analista — Review the code for issues]
Reviewed 3 files. No security issues found. Suggested adding type hints to query parameters.
```

### Scorecard

Each export includes a workflow scorecard with:

- Subtask count (total, completed, recovered, failed)
- Token usage
- Elapsed time
- Quality rating
- Task type and complexity

### Files Written

A list of all files created or modified during the workflow, collected from each agent's `files_written` output and verified against the actual project directory on disk.

## Watermark Stripping

Exports are automatically stripped of internal watermarks and meta-instructions. The stripping process removes:

- Internal system prompts and instructions
- Agent loop guidance text
- Watermark markers in the output

This ensures your exports contain only the conversation and results — clean, professional output suitable for sharing or documentation.

The watermark stripping is controlled by a skip flag: when present in the output, the stripping filter is bypassed to preserve intentional formatting.

## Project-Scoped Exports

Exports are **project-scoped** to prevent cross-project contamination:

- Each export includes only messages from the current conversation
- Workspace-specific conversations never appear in another workspace's exports
- Files written list is filtered to the current project's directory

When no project is selected, files are resolved relative to the workspace memory directory.

## How to Export

### From the History Tab

1. Open the **History** tab
2. Select a conversation from the list
3. Click the **Descargar** (Download) button in the top bar
4. Select your format: `md`, `json`, `pdf`, or `html`
5. Choose a save location

### From the Maestro Tab

1. During or after a workflow, click **Descargar** in the Maestro top bar (Row 2)
2. Select the format from the dropdown
3. The active conversation is exported

### Export Format Selection

In the Maestro tab, the Descargar button shows a format selector:

| Option | Format | Best for |
|--------|--------|----------|
| `md` | Markdown | Readable archives, GitHub Gists |
| `json` | JSON | Programmatic processing, data analysis |
| `pdf` | PDF | Sharing, printing, formal documentation |
| `html` | HTML | Browser viewing, syntax-highlighted review |

## Export Implementation Details

### Markdown Export

Builds a Markdown string with headers, code fences, and horizontal rules between major sections. Uses `textwrap` and `shlex` for safe formatting.

### JSON Export

Serializes the full conversation object with `json.dumps(indent=2, ensure_ascii=False)`. Includes all metadata fields.

### PDF Export

Uses `fpdf2` (`FPDF` class) with:
- A4 page size, 10mm margins
- Helvetica font with monospace fallback for code
- Auto page breaks at 20mm from bottom
- `multi_cell()` for wrapping long text

### HTML Export

Uses Pygments for syntax highlighting. The `HtmlFormatter` with `noclasses=True` embeds styles inline for standalone viewing. Supported languages:

- Python (`pygments.lexers.PythonLexer`)
- Shell/Bash (`pygments.lexers.BashLexer`)
- JSON (`pygments.lexers.JsonLexer`)
- YAML (`pygments.lexers.YamlLexer`)
- Markdown (`pygments.lexers.MarkdownLexer`)
- SQL (`pygments.lexers.SqlLexer`)
- JavaScript, TypeScript, HTML, CSS, and others

If Pygments is not installed, the export falls back to plain `<pre><code>` blocks without highlighting.
