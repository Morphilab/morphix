"""Editor Tab — visualizador/editor de archivos del proyecto activo.

Detecta el proyecto activado en Maestro, muestra su directorio (estructurado)
en un árbol, permite visualizar y editar el contenido de los archivos, y crear,
renombrar o eliminar archivos/carpetas.
"""

import logging
import shutil
from pathlib import Path

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFileSystemModel,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from core.path_resolver import paths

logger = logging.getLogger(__name__)

from desktop.theme import ACCENT, StyleFactory

MAX_FILE_SIZE = 1_000_000  # 1 MB
_HIDDEN = {".git", "__pycache__", ".codebase_cache", ".undo", ".redo", ".venv", "node_modules"}


class _NoiseFilter(QSortFilterProxyModel):
    """Oculta directorios/archivos ruidosos del árbol del proyecto."""

    def filterAcceptsRow(
        self, source_row: int, source_parent: QModelIndex | QPersistentModelIndex
    ) -> bool:
        model = self.sourceModel()
        if not isinstance(model, QFileSystemModel):
            return True
        idx = model.index(source_row, 0, source_parent)
        name = model.fileName(idx)
        if name in _HIDDEN or name.endswith(".pyc"):
            return False
        return True


class EditorTab(QWidget):
    """Árbol de archivos del proyecto + editor de texto."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._workspace = "main"
        self._project_root: str | None = None
        self._project_dir: Path | None = None
        self._current_file: Path | None = None
        self._dirty = False
        self._build_ui()

    # ── UI ──────────────────────────────────────────────────────────

    def _build_ui(self):
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # ── Left column: tree ──
        left = QWidget()
        left.setMinimumWidth(200)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        self._project_label = QLabel("Proyecto: —")
        self._project_label.setStyleSheet("color: #A0A0A0; font-size: 11px; padding: 2px;")
        self._project_label.setWordWrap(True)
        left_layout.addWidget(self._project_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        btn_style = StyleFactory.small_button()
        self._new_file_btn = QPushButton("➕ Archivo")
        self._new_file_btn.setStyleSheet(btn_style)
        self._new_file_btn.clicked.connect(lambda: self._new_file(self._project_dir))
        self._new_dir_btn = QPushButton("📁 Carpeta")
        self._new_dir_btn.setStyleSheet(btn_style)
        self._new_dir_btn.clicked.connect(lambda: self._new_folder(self._project_dir))
        self._refresh_btn = QPushButton("⟳")
        self._refresh_btn.setStyleSheet(btn_style)
        self._refresh_btn.clicked.connect(self._refresh)
        btn_row.addWidget(self._new_file_btn)
        btn_row.addWidget(self._new_dir_btn)
        btn_row.addWidget(self._refresh_btn)
        left_layout.addLayout(btn_row)

        self._fs_model = QFileSystemModel()
        self._proxy = _NoiseFilter(self)
        self._proxy.setSourceModel(self._fs_model)

        self._tree = QTreeView()
        self._tree.setModel(self._proxy)
        for col in (1, 2, 3):  # hide size/type/date → show only Name
            self._tree.hideColumn(col)
        self._tree.setHeaderHidden(True)
        self._tree.setStyleSheet(StyleFactory.tree_view())
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        self._tree.clicked.connect(self._on_tree_clicked)
        left_layout.addWidget(self._tree, 1)
        splitter.addWidget(left)

        # ── Columna derecha: editor (flexible) ──
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        head = QHBoxLayout()
        self._path_label = QLabel("Selecciona un archivo del árbol")
        self._path_label.setStyleSheet("color: #A0A0A0; font-size: 11px; padding: 2px;")
        self._save_btn = QPushButton("💾 Guardar")
        self._save_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT}; color: #FFFFFF; border-radius: 6px; "
            "padding: 4px 12px; font-size: 11px; font-weight: bold; }}"
            "QPushButton:disabled { background: #2A2A2A; color: #666; }"
        )
        self._save_btn.clicked.connect(self._save)
        self._save_btn.setEnabled(False)
        head.addWidget(self._path_label, 1)
        head.addWidget(self._save_btn)
        right_layout.addLayout(head)

        self._editor = QPlainTextEdit()
        self._editor.setReadOnly(True)
        self._editor.setFont(QFont("monospace", 11))
        self._editor.setStyleSheet(StyleFactory.text_editor())
        self._editor.textChanged.connect(self._on_text_changed)
        right_layout.addWidget(self._editor, 1)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #888; font-size: 11px; padding: 2px;")
        right_layout.addWidget(self._status_label)

        right.setMinimumWidth(300)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.addWidget(splitter)
        self._set_project(None)

    # ── Proyecto activo ─────────────────────────────────────────────

    def set_project(self, project_root: str | None, workspace: str = "main"):
        """Llamado por MaestroTab/MainWindow cuando cambia el proyecto activo."""
        self._workspace = workspace or "main"
        self._set_project(project_root or None)

    def _set_project(self, project_root: str | None):
        if not self._maybe_discard_changes():
            return
        self._project_root = project_root
        self._current_file = None
        self._editor.blockSignals(True)
        self._editor.clear()
        self._editor.blockSignals(False)
        self._editor.setReadOnly(True)
        self._save_btn.setEnabled(False)
        self._dirty = False
        self._path_label.setText("Selecciona un archivo del árbol")
        self._status_label.setText("")

        if not project_root:
            self._project_dir = None
            self._project_label.setText("Proyecto: — (crea/selecciona uno en Maestro)")
            self._tree.setRootIndex(QModelIndex())
            self._set_ops_enabled(False)
            return

        self._project_dir = paths.code_projects_dir(self._workspace, project_root)
        self._project_dir.mkdir(parents=True, exist_ok=True)
        name = Path(project_root).name
        self._project_label.setText(f"Proyecto: {name}")
        self._fs_model.setRootPath(str(self._project_dir))
        src = self._fs_model.index(str(self._project_dir))
        self._tree.setRootIndex(self._proxy.mapFromSource(src))
        self._set_ops_enabled(True)

    def _set_ops_enabled(self, enabled: bool):
        self._new_file_btn.setEnabled(enabled)
        self._new_dir_btn.setEnabled(enabled)
        self._refresh_btn.setEnabled(enabled)

    def _refresh(self):
        if self._project_dir:
            self._fs_model.setRootPath("")
            self._fs_model.setRootPath(str(self._project_dir))
            src = self._fs_model.index(str(self._project_dir))
            self._tree.setRootIndex(self._proxy.mapFromSource(src))

    # ── Tree → editor ──────────────────────────────────────────────

    def _path_from_index(self, proxy_idx: QModelIndex) -> Path | None:
        if not proxy_idx.isValid():
            return None
        src = self._proxy.mapToSource(proxy_idx)
        return Path(self._fs_model.filePath(src))

    def _on_tree_clicked(self, proxy_idx: QModelIndex):
        path = self._path_from_index(proxy_idx)
        if path and path.is_file():
            self._open_file(path)

    def _open_file(self, path: Path):
        if not self._maybe_discard_changes():
            return
        try:
            if path.stat().st_size > MAX_FILE_SIZE:
                self._show_readonly(f"[Archivo demasiado grande para editar: {path.name}]")
                return
            data = path.read_bytes()
            if b"\x00" in data[:4096]:
                self._show_readonly(f"[Archivo binario — no editable: {path.name}]")
                return
            text = data.decode("utf-8", errors="replace")
        except Exception as e:
            self._status_label.setText(f"❌ No se pudo abrir: {e}")
            return

        self._current_file = path
        self._editor.blockSignals(True)
        self._editor.setPlainText(text)
        self._editor.blockSignals(False)
        self._editor.setReadOnly(False)
        self._dirty = False
        self._save_btn.setEnabled(False)
        self._path_label.setText(self._rel(path))
        self._status_label.setText("")

    def _show_readonly(self, msg: str):
        self._current_file = None
        self._editor.blockSignals(True)
        self._editor.setPlainText(msg)
        self._editor.blockSignals(False)
        self._editor.setReadOnly(True)
        self._save_btn.setEnabled(False)
        self._dirty = False

    def _rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self._project_dir)) if self._project_dir else str(path)
        except ValueError:
            return str(path)

    def _on_text_changed(self):
        if self._current_file is not None and not self._editor.isReadOnly():
            self._dirty = True
            self._save_btn.setEnabled(True)

    # ── Guardar ─────────────────────────────────────────────────────

    def _inside_project(self, path: Path) -> bool:
        if self._project_dir is None:
            return False
        try:
            path.resolve().relative_to(self._project_dir.resolve())
            return True
        except ValueError:
            return False

    def _save(self):
        if self._current_file is None:
            return
        if not self._inside_project(self._current_file):
            self._status_label.setText("❌ Ruta fuera del proyecto")
            return
        try:
            self._current_file.write_text(self._editor.toPlainText(), encoding="utf-8")
            self._dirty = False
            self._save_btn.setEnabled(False)
            self._status_label.setText(f"✅ Guardado: {self._current_file.name}")
        except Exception as e:
            self._status_label.setText(f"❌ Error al guardar: {e}")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_S and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self._save()
            return
        super().keyPressEvent(event)

    def _maybe_discard_changes(self) -> bool:
        """Devuelve True si se puede continuar (guardó/descartó), False si canceló."""
        if not self._dirty or self._current_file is None:
            return True
        reply = QMessageBox.question(
            self,
            "Cambios sin guardar",
            f"'{self._current_file.name}' tiene cambios sin guardar. ¿Guardar?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return False
        if reply == QMessageBox.StandardButton.Save:
            self._save()
        self._dirty = False
        return True

    # ── Operaciones de archivo ──────────────────────────────────────

    def _on_context_menu(self, point):
        proxy_idx = self._tree.indexAt(point)
        path = self._path_from_index(proxy_idx)
        target_dir = self._project_dir
        if path is not None:
            target_dir = path if path.is_dir() else path.parent

        menu = QMenu(self)
        menu.addAction("➕ Nuevo archivo", lambda: self._new_file(target_dir))
        menu.addAction("📁 Nueva carpeta", lambda: self._new_folder(target_dir))
        if path is not None:
            menu.addSeparator()
            menu.addAction("✏️ Renombrar", lambda: self._rename(path))
            menu.addAction("🗑️ Eliminar", lambda: self._delete(path))
        menu.exec(self._tree.viewport().mapToGlobal(point))

    def _new_file(self, target_dir: Path | None):
        if not target_dir:
            return
        name, ok = QInputDialog.getText(self, "Nuevo archivo", "Nombre del archivo:")
        if not ok or not name.strip():
            return
        dest = target_dir / name.strip()
        if not self._inside_project(dest):
            self._status_label.setText("❌ Ruta fuera del proyecto")
            return
        if dest.exists():
            self._status_label.setText("❌ Ya existe")
            return
        try:
            dest.write_text("", encoding="utf-8")
            self._open_file(dest)
        except Exception as e:
            self._status_label.setText(f"❌ Error: {e}")

    def _new_folder(self, target_dir: Path | None):
        if not target_dir:
            return
        name, ok = QInputDialog.getText(self, "Nueva carpeta", "Nombre de la carpeta:")
        if not ok or not name.strip():
            return
        dest = target_dir / name.strip()
        if not self._inside_project(dest):
            self._status_label.setText("❌ Ruta fuera del proyecto")
            return
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self._status_label.setText(f"❌ Error: {e}")

    def _rename(self, path: Path):
        new_name, ok = QInputDialog.getText(self, "Renombrar", "Nuevo nombre:", text=path.name)
        if not ok or not new_name.strip() or new_name.strip() == path.name:
            return
        dest = path.parent / new_name.strip()
        if not self._inside_project(dest):
            self._status_label.setText("❌ Ruta fuera del proyecto")
            return
        if dest.exists():
            self._status_label.setText("❌ Ya existe")
            return
        try:
            path.rename(dest)
            if self._current_file == path:
                self._open_file(dest) if dest.is_file() else self._show_readonly("")
        except Exception as e:
            self._status_label.setText(f"❌ Error: {e}")

    def _delete(self, path: Path):
        reply = QMessageBox.question(
            self,
            "Eliminar",
            f"¿Eliminar '{path.name}'?" + (" (y su contenido)" if path.is_dir() else ""),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if not self._inside_project(path):
            self._status_label.setText("❌ Ruta fuera del proyecto")
            return
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            if self._current_file == path:
                self._current_file = None
                self._editor.blockSignals(True)
                self._editor.clear()
                self._editor.blockSignals(False)
                self._editor.setReadOnly(True)
                self._save_btn.setEnabled(False)
                self._dirty = False
                self._path_label.setText("Selecciona un archivo del árbol")
            self._status_label.setText(f"🗑️ Eliminado: {path.name}")
        except Exception as e:
            self._status_label.setText(f"❌ Error: {e}")
