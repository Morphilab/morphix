"""Editor Tab — explorador de directorios + editor de código multi-pestaña.

Modo proyecto (predeterminado): muestra el directorio del proyecto activo de
Maestro con contención de guardado. Modo carpeta libre ("📂 Abrir carpeta…"):
navega cualquier directorio del equipo y edita donde el usuario decidió.

Cada archivo abierto vive en su propia pestaña (CodeEditor: números de línea +
resaltado Pygments). El guardado es atómico (tmp + os.replace) y pregunta por
los cambios sin guardar al cerrar pestañas o cambiar de proyecto.
"""

import logging
import os
import shutil
from pathlib import Path

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFileSystemModel,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from core.config import settings
from core.path_resolver import paths

logger = logging.getLogger(__name__)

from desktop.code_editor import CodeEditor
from desktop.theme import COLORS, StyleFactory

MAX_FILE_SIZE = 1_000_000  # 1 MB
_TMP_SUFFIX = ".morphix-tmp"
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
    """Árbol de archivos (proyecto o carpeta libre) + editor multi-pestaña."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._workspace = settings.active_workspace
        self._project_root: str | None = None
        self._project_dir: Path | None = None
        self._mode = "project"  # "project" | "free"
        self._nav_root: Path | None = None  # directorio mostrado en el árbol
        self._open_docs: dict[Path, CodeEditor] = {}
        self._build_ui()

    # ── UI ──────────────────────────────────────────────────────────

    def _build_ui(self):
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # ── Columna izquierda: árbol ──
        left = QWidget()
        left.setMinimumWidth(200)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        self._project_label = QLabel("Proyecto: —")
        self._project_label.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 11px; padding: 2px;"
        )
        self._project_label.setWordWrap(True)
        left_layout.addWidget(self._project_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        btn_style = StyleFactory.small_button()
        self._new_file_btn = QPushButton("➕ Archivo")
        self._new_file_btn.setStyleSheet(btn_style)
        self._new_file_btn.clicked.connect(lambda: self._new_file(self._root()))
        self._new_dir_btn = QPushButton("📁 Carpeta")
        self._new_dir_btn.setStyleSheet(btn_style)
        self._new_dir_btn.clicked.connect(lambda: self._new_folder(self._root()))
        self._refresh_btn = QPushButton("⟳")
        self._refresh_btn.setStyleSheet(btn_style)
        self._refresh_btn.clicked.connect(self._refresh)
        self._open_folder_btn = QPushButton("📂")
        self._open_folder_btn.setStyleSheet(btn_style)
        self._open_folder_btn.setToolTip("Abrir carpeta… (navegar fuera del proyecto)")
        self._open_folder_btn.clicked.connect(self._pick_folder)
        btn_row.addWidget(self._new_file_btn)
        btn_row.addWidget(self._new_dir_btn)
        btn_row.addWidget(self._refresh_btn)
        btn_row.addWidget(self._open_folder_btn)
        left_layout.addLayout(btn_row)

        # ── Breadcrumb: volver al proyecto + ⬆ subir + migas clicables ──
        crumb_row = QHBoxLayout()
        crumb_row.setSpacing(2)
        self._back_btn = QPushButton("📦 Proyecto")
        self._back_btn.setStyleSheet(btn_style)
        self._back_btn.setToolTip("Volver al proyecto activo en Maestro")
        self._back_btn.clicked.connect(lambda: self._set_project(self._project_root))
        self._back_btn.setEnabled(False)
        crumb_row.addWidget(self._back_btn)
        self._ascend_btn = QPushButton("⬆")
        self._ascend_btn.setStyleSheet(btn_style)
        self._ascend_btn.setToolTip("Subir al directorio superior")
        self._ascend_btn.clicked.connect(self._ascend)
        self._ascend_btn.setEnabled(False)
        crumb_row.addWidget(self._ascend_btn)
        self._crumbs_widget = QWidget()
        self._crumbs = QHBoxLayout(self._crumbs_widget)
        self._crumbs.setContentsMargins(0, 0, 0, 0)
        self._crumbs.setSpacing(0)
        crumb_row.addWidget(self._crumbs_widget, 1)
        crumb_row.addStretch(0)
        left_layout.addLayout(crumb_row)

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
        self._tree.setExpandsOnDoubleClick(False)  # doble clic = navegar, no expandir
        self._tree.doubleClicked.connect(self._on_tree_activated)
        left_layout.addWidget(self._tree, 1)
        splitter.addWidget(left)

        # ── Columna derecha: pestañas de documentos ──
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        head = QHBoxLayout()
        self._path_label = QLabel("Selecciona un archivo del árbol")
        self._path_label.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 11px; padding: 2px;"
        )
        self._save_btn = QPushButton("💾 Guardar")
        self._save_btn.setStyleSheet(StyleFactory.primary_button())
        self._save_btn.clicked.connect(self._save)
        self._save_btn.setEnabled(False)
        head.addWidget(self._path_label, 1)
        head.addWidget(self._save_btn)
        right_layout.addLayout(head)

        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.setMovable(False)
        self._tabs.setStyleSheet(StyleFactory.editor_tabs())
        self._tabs.tabCloseRequested.connect(self._close_tab)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        right_layout.addWidget(self._tabs, 1)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(
            f"color: {COLORS['text_dim']}; font-size: 11px; padding: 2px;"
        )
        self._status_label.setWordWrap(True)
        right_layout.addWidget(self._status_label)

        right.setMinimumWidth(300)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.addWidget(splitter)
        self._set_project(None)

    # ── Raíz activa (proyecto o navegación libre) ──────────────────

    def _root(self) -> Path | None:
        return self._nav_root

    def set_project(self, project_root: str | None, workspace: str | None = None):
        """Llamado por MaestroTab/MainWindow cuando cambia el proyecto activo."""
        self._workspace = workspace or settings.active_workspace
        self._set_project(project_root or None)

    def _set_project(self, project_root: str | None):
        if not self._discard_all():
            return
        self._open_folder_btn.setText("📂")
        self._open_folder_btn.setToolTip("Abrir carpeta… (navegar fuera del proyecto)")
        self._project_root = project_root
        self._path_label.setText("Selecciona un archivo del árbol")
        self._status_label.setText("")
        self._sync_save_btn()

        if not project_root:
            # Sin proyecto: breadcrumb + árbol disponibles en navegación libre.
            self._project_dir = None
            self._mode = "free"
            self._project_label.setText(
                "Sin proyecto — navegación libre (crea/selecciona uno en Maestro)"
            )
            self._set_tree_root(Path.home())
            self._set_ops_enabled(True)
            return

        self._mode = "project"
        self._project_dir = paths.code_projects_dir(self._workspace, project_root)
        self._project_dir.mkdir(parents=True, exist_ok=True)
        name = Path(project_root).name
        self._project_label.setText(f"Proyecto: {name}")
        self._set_tree_root(self._project_dir)
        self._set_ops_enabled(True)

    def open_folder(self, folder: Path | None):
        """Modo carpeta libre: navega y edita cualquier directorio del equipo."""
        if folder is None:
            return
        if not self._discard_all():
            return
        folder = Path(folder)
        if not folder.is_dir():
            self._status_label.setText(f"❌ La carpeta no existe: {folder}")
            return
        self._mode = "free"
        self._project_label.setText(f"Carpeta: {folder}")
        self._set_tree_root(folder)
        self._set_ops_enabled(True)

    def _pick_folder(self):
        start = str(self._nav_root or self._project_dir or Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Abrir carpeta…", start)
        if chosen:
            self.open_folder(Path(chosen))

    # ── Navegación breadcrumb (subir/bajar entre directorios) ──────

    def _set_tree_root(self, root: Path | None):
        """Re-ubica la raíz del árbol y refresca el breadcrumb."""
        self._nav_root = root
        if root is None:
            self._tree.setRootIndex(QModelIndex())
        else:
            self._fs_model.setRootPath(str(root))
            src = self._fs_model.index(str(root))
            self._tree.setRootIndex(self._proxy.mapFromSource(src))
        self._rebuild_crumbs()
        self._back_btn.setEnabled(self._mode == "free" and self._project_root is not None)

    def _inside_subtree(self, root: Path, target: Path) -> bool:
        """True si target == root o target desciende de root."""
        try:
            target.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    def _navigate_to(self, target: Path):
        if (
            self._mode == "project"
            and self._project_dir is not None
            and not self._inside_subtree(self._project_dir, target)
        ):
            reply = QMessageBox.question(
                self,
                "Salir del proyecto",
                "Vas a navegar fuera del proyecto (modo libre sobre el equipo).\n"
                "El botón ⬅ te devuelve al proyecto. ¿Continuar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            self.open_folder(target)
            return
        self._set_tree_root(target)

    def _ascend(self):
        if self._nav_root is None:
            return
        parent = self._nav_root.parent
        if parent == self._nav_root:
            return  # ya en la raíz del sistema
        self._navigate_to(parent)

    def _rebuild_crumbs(self):
        while self._crumbs.count():
            item = self._crumbs.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        if self._nav_root is None:
            hint = QLabel("Usa 📂 para elegir una carpeta")
            hint.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 11px; padding: 2px;")
            self._crumbs.addWidget(hint)
            self._ascend_btn.setEnabled(False)
            return

        if self._mode == "project" and self._project_dir is not None:
            chain: list[Path] = []
            node: Path | None = self._nav_root
            while node is not None and self._inside_subtree(self._project_dir, node):
                chain.insert(0, node)
                node = node.parent if node != self._project_dir else None
            first_label = f"📦 {self._project_dir.name}"
        else:
            chain = list(self._nav_root.parents)[::-1] + [self._nav_root]
            if chain and chain[0] == self._nav_root.anchor:
                chain[0] = Path(self._nav_root.anchor)
            first_label = self._nav_root.anchor or "/"

        for i, segment in enumerate(chain):
            is_last = i == len(chain) - 1
            label = first_label if i == 0 else segment.name
            if is_last:
                current = QLabel(label)
                current.setStyleSheet(
                    f"color: {COLORS['accent']}; font-size: 11px; "
                    f"padding: 2px 6px; font-weight: bold;"
                )
                self._crumbs.addWidget(current)
            else:
                btn = QPushButton(label)
                btn.setStyleSheet(StyleFactory.breadcrumb_button())
                btn.setToolTip(str(segment))
                btn.clicked.connect(lambda checked=False, p=segment: self._navigate_to(p))
                self._crumbs.addWidget(btn)
            if not is_last:
                sep = QLabel("▸")
                sep.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 11px;")
                self._crumbs.addWidget(sep)
        self._ascend_btn.setEnabled(True)

    def _set_ops_enabled(self, enabled: bool):
        self._new_file_btn.setEnabled(enabled)
        self._new_dir_btn.setEnabled(enabled)
        self._refresh_btn.setEnabled(enabled)
        self._open_folder_btn.setEnabled(True)  # navegar siempre disponible
        self._ascend_btn.setEnabled(self._nav_root is not None)

    def _refresh(self):
        root = self._root()
        if root:
            self._fs_model.setRootPath("")
            self._fs_model.setRootPath(str(root))
            src = self._fs_model.index(str(root))
            self._tree.setRootIndex(self._proxy.mapFromSource(src))

    # ── Tree → editor ──────────────────────────────────────────────

    def _path_from_index(self, proxy_idx: QModelIndex) -> Path | None:
        if not proxy_idx.isValid():
            return None
        src = self._proxy.mapToSource(proxy_idx)
        return Path(self._fs_model.filePath(src))

    def _on_tree_activated(self, proxy_idx: QModelIndex):
        """Doble clic: archivo → abrir en pestaña; carpeta → descender."""
        path = self._path_from_index(proxy_idx)
        if path is None:
            return
        if path.is_dir():
            self._navigate_to(path)
        elif path.is_file():
            self._open_file(path)

    def open_project_file(self, rel_path: str):
        """Abre un archivo (relativo a la raíz activa o absoluto) en una pestaña.

        Usado por el doble clic en 'Archivos creados' del Maestro.
        """
        p = Path(rel_path)
        if not p.is_absolute():
            root = self._root()
            if root is None:
                return
            p = root / p
        if p.is_file():
            self._open_file(p)

    def _open_file(self, path: Path):
        path = Path(path)
        if path in self._open_docs:
            self._tabs.setCurrentWidget(self._open_docs[path])
            return
        try:
            if path.stat().st_size > MAX_FILE_SIZE:
                self._show_notice(f"[Archivo demasiado grande para editar: {path.name}]")
                return
            data = path.read_bytes()
            if b"\x00" in data[:4096]:
                self._show_notice(f"[Archivo binario — no editable: {path.name}]")
                return
            text = data.decode("utf-8", errors="replace")
        except Exception as e:
            self._status_label.setText(f"❌ No se pudo abrir: {e}")
            return

        editor = CodeEditor()
        editor.set_path(path)
        editor.set_lexer_for_path(path)
        editor.setPlainText(text)
        editor.mark_saved()
        editor.setProperty("mtime", path.stat().st_mtime)
        editor.textChanged.connect(lambda e=editor: self._on_editor_changed(e))
        self._open_docs[path] = editor
        self._tabs.addTab(editor, path.name)
        self._tabs.setCurrentWidget(editor)
        self._path_label.setText(self._rel(path))
        self._status_label.setText("")
        self._sync_save_btn()

    def _show_notice(self, msg: str):
        self._status_label.setText(msg)

    def _rel(self, path: Path) -> str:
        root = self._root()
        try:
            return str(path.relative_to(root)) if root else str(path)
        except ValueError:
            return str(path)

    # ── Estado de pestañas ─────────────────────────────────────────

    def _current_editor(self) -> CodeEditor | None:
        w = self._tabs.currentWidget()
        return w if isinstance(w, CodeEditor) else None

    def _on_editor_changed(self, editor: CodeEditor):
        idx = self._tabs.indexOf(editor)
        if idx >= 0:
            path = editor.path()
            name = path.name if path else "?"
            self._tabs.setTabText(idx, f"• {name}" if editor.is_dirty() else name)
        self._sync_save_btn()

    def _on_tab_changed(self, _idx: int):
        editor = self._current_editor()
        path = editor.path() if editor is not None else None
        if path is not None:
            self._path_label.setText(self._rel(path))
        self._sync_save_btn()

    def _sync_save_btn(self):
        editor = self._current_editor()
        self._save_btn.setEnabled(editor is not None and editor.is_dirty())

    def _close_tab(self, idx: int):
        w = self._tabs.widget(idx)
        if not isinstance(w, CodeEditor):
            return
        if w.is_dirty():
            reply = QMessageBox.question(
                self,
                "Cambios sin guardar",
                f"'{self._tab_name(w)}' tiene cambios sin guardar. ¿Guardar?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if reply == QMessageBox.StandardButton.Cancel:
                return
            if reply == QMessageBox.StandardButton.Save and not self._save_editor(w):
                return  # fallo de guardado → conservar la pestaña abierta
        path = w.path()
        self._tabs.removeTab(idx)
        if path is not None:
            self._open_docs.pop(path, None)

    def _discard_all(self) -> bool:
        """Cierra todas las pestañas preguntando por cambios. False si canceló."""
        dirty = [e for e in self._open_docs.values() if e.is_dirty()]
        if dirty:
            names = ", ".join(self._tab_name(e) for e in dirty[:3])
            extra = "…" if len(dirty) > 3 else ""
            reply = QMessageBox.question(
                self,
                "Cambios sin guardar",
                f"{len(dirty)} archivo(s) con cambios ({names}{extra}). ¿Guardar todo?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if reply == QMessageBox.StandardButton.Cancel:
                return False
            if reply == QMessageBox.StandardButton.Save:
                for e in dirty:
                    if not self._save_editor(e):
                        return False
        while self._tabs.count():
            w = self._tabs.widget(0)
            self._tabs.removeTab(0)
            if isinstance(w, CodeEditor) and w.path() is not None:
                self._open_docs.pop(w.path(), None)  # type: ignore[arg-type]
        return True

    @staticmethod
    def _tab_name(editor: CodeEditor) -> str:
        path = editor.path()
        return path.name if path else "?"

    # ── Guardar ─────────────────────────────────────────────────────

    def _inside_root(self, path: Path) -> bool:
        if self._mode == "free":
            return True  # el usuario eligió navegar/editar ahí
        if self._project_dir is None:
            return False
        try:
            path.resolve().relative_to(self._project_dir.resolve())
            return True
        except ValueError:
            return False

    def _save(self):
        self._save_editor(self._current_editor())

    def _save_editor(self, editor: CodeEditor | None) -> bool:
        if editor is None:
            return False
        path = editor.path()
        if path is None:
            return False
        if not self._inside_root(path):
            self._status_label.setText("❌ Ruta fuera del proyecto")
            return False

        if path.exists():
            try:
                stored = editor.property("mtime")
                if stored is not None and path.stat().st_mtime != float(stored):
                    reply = QMessageBox.question(
                        self,
                        "Archivo modificado en disco",
                        f"'{path.name}' cambió fuera del editor. ¿Sobrescribir?",
                        QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                        QMessageBox.StandardButton.Save,
                    )
                    if reply != QMessageBox.StandardButton.Save:
                        self._status_label.setText(
                            "⚠️ Guardado cancelado (archivo en disco más nuevo)"
                        )
                        return False
            except OSError:
                pass  # si desaparece, el guardado lo recrea

        tmp = path.with_name(path.name + _TMP_SUFFIX)
        try:
            tmp.write_text(editor.toPlainText(), encoding="utf-8")
            os.replace(tmp, path)
        except Exception as e:
            tmp.unlink(missing_ok=True)
            self._status_label.setText(f"❌ Error al guardar: {e}")
            return False
        try:
            editor.setProperty("mtime", path.stat().st_mtime)
        except OSError:
            pass
        editor.mark_saved()
        self._on_editor_changed(editor)
        self._status_label.setText(f"✅ Guardado: {path.name}")
        return True

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_S and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self._save()
            return
        super().keyPressEvent(event)

    # ── Operaciones de archivo ──────────────────────────────────────

    def _on_context_menu(self, point):
        proxy_idx = self._tree.indexAt(point)
        path = self._path_from_index(proxy_idx)
        target_dir = self._root()
        if path is not None:
            target_dir = path if path.is_dir() else path.parent

        menu = QMenu(self)
        menu.addAction("➕ Nuevo archivo", lambda: self._new_file(target_dir))
        menu.addAction("📁 Nueva carpeta", lambda: self._new_folder(target_dir))
        if path is not None:
            menu.addSeparator()
            if path.is_file():
                menu.addAction(
                    "👁️ Abrir con viewer",
                    lambda: self._open_in_viewer(path),
                )
            menu.addAction("✏️ Renombrar", lambda: self._rename(path))
            menu.addAction("🗑️ Eliminar", lambda: self._delete(path))
        menu.exec(self._tree.viewport().mapToGlobal(point))

    def _open_in_viewer(self, path: Path):
        """Click derecho → visor standalone (sin pasar por el editor)."""
        from desktop.services.file_viewer_service import open_in_viewer

        open_in_viewer(path)

    def _new_file(self, target_dir: Path | None):
        if not target_dir:
            return
        name, ok = QInputDialog.getText(self, "Nuevo archivo", "Nombre del archivo:")
        if not ok or not name.strip():
            return
        dest = target_dir / name.strip()
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
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self._status_label.setText(f"❌ Error: {e}")

    def _rename(self, path: Path):
        new_name, ok = QInputDialog.getText(self, "Renombrar", "Nuevo nombre:", text=path.name)
        if not ok or not new_name.strip() or new_name.strip() == path.name:
            return
        dest = path.parent / new_name.strip()
        if dest.exists():
            self._status_label.setText("❌ Ya existe")
            return
        try:
            path.rename(dest)
            if path in self._open_docs:
                editor = self._open_docs.pop(path)
                idx = self._tabs.indexOf(editor)
                editor.set_path(dest)
                self._open_docs[dest] = editor
                if idx >= 0 and not editor.is_dirty():
                    self._tabs.setTabText(idx, dest.name)
                if dest.is_file():
                    editor.setProperty("mtime", dest.stat().st_mtime)
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
        if not self._inside_root(path):
            self._status_label.setText("❌ Ruta fuera del proyecto")
            return

        from PySide6.QtCore import QThread
        from PySide6.QtCore import Signal as QSignal

        class _DeleteWorker(QThread):
            done = QSignal(bool, str)

            def __init__(self, p, is_dir):
                super().__init__()
                self._path = p
                self._is_dir = is_dir

            def run(self):
                try:
                    if self._is_dir:
                        shutil.rmtree(self._path)
                    else:
                        self._path.unlink()
                    self.done.emit(True, "")
                except Exception as e:
                    self.done.emit(False, str(e))

        self._delete_worker = _DeleteWorker(path, path.is_dir())
        self._delete_worker.done.connect(lambda ok, err: self._on_delete_done(path, ok, err))
        self._delete_worker.start()

    def _on_delete_done(self, path, success, error):
        if not success:
            self._status_label.setText(f"❌ Error: {error}")
            return
        editor = self._open_docs.pop(path, None)
        if editor is not None:
            idx = self._tabs.indexOf(editor)
            if idx >= 0:
                self._tabs.removeTab(idx)
        self._sync_save_btn()
        self._status_label.setText(f"🗑️ Eliminado: {path.name}")
