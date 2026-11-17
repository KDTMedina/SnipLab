"""SnipLab main window, tray icon and application entry point."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton,
                               QSystemTrayIcon, QVBoxLayout, QWidget)

from . import __version__, library
from .capture import grab_active_window, grab_screen, screen_at_cursor
from .editor import EditorWindow
from .hotkeys import DEFAULT_KEYS, HotkeyManager
from .overlay import RegionOverlay


def make_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#1e88e5"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    p.setPen(Qt.white)
    f = QFont("Segoe UI", 30)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pix.rect(), Qt.AlignCenter, "S")
    p.end()
    return QIcon(pix)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"SnipLab {__version__}")
        self.setWindowIcon(make_icon())
        self.resize(820, 560)
        self.settings = QSettings("SnipLab", "SnipLab")
        self._editors: set[EditorWindow] = set()
        self._overlay: RegionOverlay | None = None

        self._build_ui()
        self._build_tray()
        self._start_hotkeys()
        self.refresh_library()

    # --- folder ---------------------------------------------------------
    @property
    def folder(self) -> Path:
        return Path(self.settings.value("save_dir", str(library.default_dir())))

    def choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Capture folder", str(self.folder))
        if d:
            self.settings.setValue("save_dir", d)
            self.refresh_library()

    # --- UI -----------------------------------------------------------------
    def _build_ui(self):
        root = QWidget()
        layout = QVBoxLayout(root)

        row = QHBoxLayout()
        for text, mode, delay in [
            ("Region  (Ctrl+Shift+S)", "region", 0),
            ("Full screen  (Ctrl+Shift+F)", "fullscreen", 0),
            ("Window  (Ctrl+Shift+W)", "window", 0),
            ("Region in 3 s", "region", 3000),
        ]:
            b = QPushButton(text)
            b.setMinimumHeight(40)
            b.clicked.connect(lambda _=False, m=mode, d=delay: self.start_capture(m, d))
            row.addWidget(b)
        layout.addLayout(row)

        header = QHBoxLayout()
        self.folder_label = QLabel()
        header.addWidget(self.folder_label, 1)
        change = QPushButton("Change folder...")
        change.clicked.connect(self.choose_folder)
        header.addWidget(change)
        layout.addLayout(header)

        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(180, 120))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setSpacing(8)
        self.list.setMovement(QListWidget.Static)
        self.list.itemDoubleClicked.connect(lambda it: self.open_editor(Path(it.data(Qt.UserRole))))
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._library_menu)
        layout.addWidget(self.list, 1)

        self.setCentralWidget(root)
        self.statusBar().showMessage("Double-click a capture to edit it")

    def _build_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(make_icon(), self)
        menu = QMenu()
        for text, mode in [("Capture region", "region"), ("Capture full screen", "fullscreen"),
                           ("Capture window", "window")]:
            act = QAction(text, self)
            act.triggered.connect(lambda _=False, m=mode: self.start_capture(m))
            menu.addAction(act)
        menu.addSeparator()
        show = QAction("Open SnipLab", self)
        show.triggered.connect(self.show_window)
        menu.addAction(show)
        quit_act = QAction("Quit", self)
        quit_act.triggered.connect(self.quit_app)
        menu.addAction(quit_act)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.show_window() if reason == QSystemTrayIcon.Trigger else None)
        self.tray.setToolTip("SnipLab")
        self.tray.show()

    def _start_hotkeys(self):
        self.hotkeys = HotkeyManager(DEFAULT_KEYS)
        self.hotkeys.region.connect(lambda: self.start_capture("region"))
        self.hotkeys.fullscreen.connect(lambda: self.start_capture("fullscreen"))
        self.hotkeys.window.connect(lambda: self.start_capture("window"))
        if not self.hotkeys.start():
            self.statusBar().showMessage("Global hotkeys unavailable (pynput not installed?)")

    def show_window(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        self.hotkeys.stop()
        if self.tray:
            self.tray.hide()
        QApplication.quit()

    def closeEvent(self, e):
        if self.tray and self.tray.isVisible():
            self.hide()  # keep running in the tray so hotkeys keep working
            e.ignore()
        else:
            self.hotkeys.stop()
            e.accept()
            QApplication.quit()

    # --- library ------------------------------------------------------------
    def refresh_library(self):
        self.folder_label.setText(f"Captures: {self.folder}")
        self.list.clear()
        for path in library.list_captures(self.folder):
            pix = QPixmap(str(path))
            if pix.isNull():
                continue
            item = QListWidgetItem(QIcon(pix.scaled(self.list.iconSize(), Qt.KeepAspectRatio,
                                                    Qt.SmoothTransformation)), path.name)
            item.setData(Qt.UserRole, str(path))
            self.list.addItem(item)

    def _library_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        path = Path(item.data(Qt.UserRole))
        menu = QMenu(self)
        menu.addAction("Edit", lambda: self.open_editor(path))
        menu.addAction("Delete", lambda: self._delete(path))
        menu.exec(self.list.mapToGlobal(pos))

    def _delete(self, path: Path):
        if QMessageBox.question(self, "SnipLab", f"Delete {path.name}?") == QMessageBox.Yes:
            library.delete_capture(path)
            self.refresh_library()

    # --- capture flow -----------------------------------------------------------
    def start_capture(self, mode: str, delay_ms: int = 0):
        was_visible = self.isVisible()
        self.hide()
        # Give the window time to disappear so it is not in the screenshot.
        QTimer.singleShot(max(delay_ms, 250), lambda: self._do_capture(mode, was_visible))

    def _do_capture(self, mode: str, restore: bool):
        screen = screen_at_cursor()
        if mode == "region":
            pix = grab_screen(screen)
            self._overlay = RegionOverlay(screen, pix)
            self._overlay.selected.connect(lambda p: self._finish(p, restore))
            self._overlay.cancelled.connect(lambda: self._restore(restore))
            self._overlay.show()
            self._overlay.activateWindow()
            self._overlay.raise_()
            return
        pix = grab_active_window() if mode == "window" else None
        self._finish(pix or grab_screen(screen), restore)

    def _restore(self, restore: bool):
        if restore:
            self.show_window()

    def _finish(self, pix: QPixmap, restore: bool):
        path = library.new_capture_path(self.folder)
        if not pix.save(str(path)):
            QMessageBox.warning(self, "SnipLab", f"Could not save capture to {path}")
            self._restore(restore)
            return
        self.refresh_library()
        self.open_editor(path)

    def open_editor(self, path: Path):
        editor = EditorWindow(path)
        self._editors.add(editor)
        editor.saved.connect(lambda _p: self.refresh_library())
        editor.destroyed.connect(lambda _=None, e=editor: self._editors.discard(e))
        editor.show()
        editor.raise_()
        editor.activateWindow()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("SnipLab")
    app.setQuitOnLastWindowClosed(False)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
