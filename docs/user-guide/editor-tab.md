# Editor Tab

The Editor tab provides a built-in file browser and text editor for the active project. It lets you browse, view, edit, create, rename, and delete files without leaving Morphix.

## Layout: 2-Column

| Column | Width | Content |
|--------|-------|---------|
| Left | 280px fixed | Project name label, action buttons, file tree (QTreeView) |
| Right | Flexible | File path label, Save button, text editor (QPlainTextEdit), status bar |

## Left Column: File Tree

### Project Display

The top label shows the active project name (e.g., "Proyecto: my_project"). It changes automatically when you select a project in the Maestro tab. If no project is selected, it shows "Proyecto: — (crea/selecciona uno en Maestro)".

### Action Buttons

| Button | Action |
|--------|--------|
| ➕ Archivo | Create a new file in the selected directory |
| 📁 Carpeta | Create a new folder in the selected directory |
| ⟳ | Refresh the file tree |

### Noise Filtering

The file tree automatically hides noise directories and files to keep the view clean:

| Hidden | Reason |
|--------|--------|
| `.git` | Version control metadata |
| `__pycache__` | Python bytecode cache |
| `.codebase_cache` | Morphix indexing cache |
| `.undo` / `.redo` | Morphix change tracker data |
| `.venv` | Virtual environment |
| `node_modules` | Node.js dependencies |
| `*.pyc` | Compiled Python files |

This filtering is implemented via a `QSortFilterProxyModel` subclass called `_NoiseFilter`.

### Tree Interaction

- **Click a file** — opens it in the right panel for viewing/editing
- **Right-click** — opens context menu with file operations (see below)
- The tree shows only filenames (size, type, and date columns are hidden)

## Right Column: Text Editor

### File Path Display

Shows the relative path of the currently open file (relative to the project root). When no file is open: "Selecciona un archivo del árbol".

### Save Button

A "💾 Guardar" button saves the current file. It is disabled when:
- No file is open
- The file hasn't been modified since opening/last save
- The file is in read-only mode (binary or too large)

Keyboard shortcut: **Ctrl+S**

### Text Editor

A `QPlainTextEdit` with monospace font (11pt). Features:
- Read-only by default — becomes editable when you open a file
- Max file size: 1 MB (larger files are displayed as read-only)
- Binary file detection: checks first 4096 bytes for null bytes — if found, shows "[Archivo binario — no editable]"
- UTF-8 encoding with error replacement for non-UTF-8 content
- Dirty state tracking — the Save button enables when content changes

### Status Bar

Shows operation results:
- ✅ Guardado: `<filename>` — file saved successfully
- ❌ Error al guardar: `<message>` — save failed
- ❌ Ruta fuera del proyecto — path safety violation
- ❌ Ya existe — file/folder already exists

## Context Menu Operations

Right-clicking in the file tree opens a context menu:

### Create File (`➕ Nuevo archivo`)

Prompts for a filename. Creates the file in the directory where you right-clicked (or the file's parent directory if you right-clicked a file). If the file already exists, shows an error.

### Create Folder (`📁 Nueva carpeta`)

Prompts for a folder name. Creates the directory. Parent directories are created if they don't exist.

### Rename (`✏️ Renombrar`)

Prompts for a new name. If the new name already exists, shows an error. If you're currently editing the renamed file, the editor automatically opens the renamed path.

### Delete (`🗑️ Eliminar`)

Shows a confirmation dialog ("¿Eliminar 'filename'?"). For directories, it warns "(y su contenido)". Deletes files with `unlink()` and directories with `shutil.rmtree()`. If you're currently editing the deleted file, the editor clears and becomes read-only.

!!! warning "Delete is permanent"
    Deletions are immediate and go through the filesystem. There is no trash/recycle bin integration in v1. Use the Change Tracker's undo feature via `core.change_tracker` if you need rollback.

## Path Safety

The Editor enforces that all file operations stay within the project directory:

- Every create, rename, and delete operation calls `_inside_project()` which verifies the target path resolves within the project root
- If a path escapes the project directory (e.g., via `../` traversal), the operation is rejected with "❌ Ruta fuera del proyecto"
- The project directory is set to `code_projects/<name>/` within the workspace's memory directory

## Unsaved Changes Protection

When you try to open a different file or switch projects with unsaved changes:

1. A dialog appears: "'filename' tiene cambios sin guardar. ¿Guardar?"
2. Options: **Save**, **Discard**, **Cancel**
3. **Save** — writes the file and proceeds
4. **Discard** — discards changes and proceeds
5. **Cancel** — aborts the operation, stays on the current file

## Auto-Refresh

When an agent creates or modifies files during a workflow, the Editor tab does **not** auto-refresh automatically. Click the `⟳` (Refresh) button to sync the tree with the filesystem. The Editor receives the `project_changed` signal when the Maestro tab changes the active project.

## Limitations in v1

- **No syntax highlighting** — the editor uses `QPlainTextEdit` without syntax highlighting. Code is displayed as plain monospace text.
- **No find/replace** — use the text editor's built-in selection but there's no search dialog.
- **No tabbed editing** — only one file can be open at a time.
- **No auto-save** — save explicitly with Ctrl+S or the Save button.
