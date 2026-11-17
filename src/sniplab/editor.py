"""Annotation editor: a QGraphicsView canvas plus the editor window."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QAction, QActionGroup, QColor, QGuiApplication, QImage, QKeySequence,
                           QPainter, QPainterPath, QPen, QPixmap, QUndoCommand, QUndoStack)
from PySide6.QtWidgets import (QColorDialog, QFileDialog, QGraphicsEllipseItem, QGraphicsRectItem,
                               QGraphicsScene, QGraphicsView, QLabel, QMainWindow, QMessageBox,
                               QSpinBox, QToolBar)

from .items import MOVABLE, ArrowItem, PixelateItem, StepItem, TextItem

TOOLS = [
    ("select", "Select", "Select and move annotations (Delete removes)"),
    ("rect", "Rectangle", "Draw a rectangle"),
    ("ellipse", "Ellipse", "Draw an ellipse"),
    ("line", "Line", "Draw a line"),
    ("arrow", "Arrow", "Draw an arrow"),
    ("text", "Text", "Click to add text (double-click text to edit)"),
    ("highlight", "Highlight", "Highlighter using the current colour"),
    ("blur", "Blur", "Pixelate sensitive information"),
    ("step", "Step", "Place numbered step markers"),
    ("crop", "Crop", "Drag the area to keep"),
]


# --- undo commands ---------------------------------------------------------
class AddCommand(QUndoCommand):
    def __init__(self, scene: QGraphicsScene, item, text="Add annotation"):
        super().__init__(text)
        self.scene, self.item = scene, item

    def redo(self):
        if self.item.scene() is None:
            self.scene.addItem(self.item)

    def undo(self):
        if self.item.scene() is self.scene:
            self.scene.removeItem(self.item)


class DeleteCommand(QUndoCommand):
    def __init__(self, scene: QGraphicsScene, item):
        super().__init__("Delete annotation")
        self.scene, self.item = scene, item

    def redo(self):
        if self.item.scene() is self.scene:
            self.scene.removeItem(self.item)

    def undo(self):
        if self.item.scene() is None:
            self.scene.addItem(self.item)


class CropCommand(QUndoCommand):
    def __init__(self, canvas: "Canvas", old: QRectF, new: QRectF):
        super().__init__("Crop")
        self.canvas, self.old, self.new = canvas, old, new

    def redo(self):
        self.canvas.set_canvas_rect(self.new)

    def undo(self):
        self.canvas.set_canvas_rect(self.old)


# --- canvas ----------------------------------------------------------------
class Canvas(QGraphicsView):
    def __init__(self, image: QImage):
        super().__init__()
        self.base = image
        scene = QGraphicsScene(self)
        self.setScene(scene)
        bg = scene.addPixmap(QPixmap.fromImage(image))
        bg.setZValue(-1000)
        scene.setSceneRect(QRectF(image.rect()))

        self.setRenderHint(QPainter.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor(30, 30, 30))

        self.undo_stack = QUndoStack(self)
        self.color = QColor("#e53935")
        self.pen_width = 4
        self.tool = "select"
        self._start: QPointF | None = None
        self._cur = None
        self._step = 1
        self.set_tool("select")

    # --- tool state ---------------------------------------------------
    def set_tool(self, name: str):
        self.tool = name
        self.setDragMode(QGraphicsView.RubberBandDrag if name == "select" else QGraphicsView.NoDrag)
        self.viewport().setCursor(Qt.ArrowCursor if name == "select" else Qt.CrossCursor)
        if name != "select":
            self.scene().clearSelection()

    def set_canvas_rect(self, rect: QRectF):
        self.scene().setSceneRect(rect)
        self.reset_view()

    # --- view helpers -------------------------------------------------
    def reset_view(self):
        """Fit the image in the window, but never enlarge past 100%."""
        self.resetTransform()
        vp = self.viewport().size()
        sr = self.sceneRect()
        if sr.width() > vp.width() or sr.height() > vp.height():
            self.fitInView(sr, Qt.KeepAspectRatio)

    def actual_size(self):
        self.resetTransform()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            factor = 1.15 ** (e.angleDelta().y() / 120)
            self.scale(factor, factor)
        else:
            super().wheelEvent(e)

    def drawForeground(self, painter: QPainter, rect: QRectF):
        # Hide anything lying outside the (possibly cropped) canvas.
        outer = QPainterPath()
        outer.addRect(rect)
        inner = QPainterPath()
        inner.addRect(self.sceneRect())
        painter.fillPath(outer.subtracted(inner), QColor(30, 30, 30))

    # --- item creation --------------------------------------------------
    def _make(self, tool: str):
        pen = QPen(self.color, self.pen_width)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        if tool == "rect":
            item = QGraphicsRectItem()
            item.setPen(pen)
        elif tool == "ellipse":
            item = QGraphicsEllipseItem()
            item.setPen(pen)
        elif tool == "arrow":
            item = ArrowItem()
            item.setPen(pen)
        elif tool == "line":
            from PySide6.QtWidgets import QGraphicsLineItem
            item = QGraphicsLineItem()
            item.setPen(pen)
        elif tool == "highlight":
            item = QGraphicsRectItem()
            fill = QColor(self.color)
            fill.setAlpha(110)
            item.setBrush(fill)
            item.setPen(Qt.NoPen)
        elif tool == "blur":
            item = PixelateItem(self.base)
        else:  # crop preview
            item = QGraphicsRectItem()
            item.setPen(QPen(Qt.white, 1, Qt.DashLine))
            item.setBrush(QColor(0, 0, 0, 60))
        if tool != "crop":
            item.setFlags(MOVABLE)
        return item

    def _apply_geometry(self, pos: QPointF):
        a, item = self._start, self._cur
        if self.tool in ("line", "arrow"):
            item.setLine(QLineF(a, pos))
        else:
            rect = QRectF(a, pos).normalized()
            if self.tool == "crop":
                rect = rect.intersected(self.sceneRect())
            item.setRect(rect)

    # --- mouse ---------------------------------------------------------
    def mousePressEvent(self, e):
        if self.tool == "select" or e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)
        view_pos = e.position().toPoint()
        pos = self.mapToScene(view_pos)

        if self.tool == "text":
            if isinstance(self.itemAt(view_pos), TextItem):
                return super().mousePressEvent(e)
            item = TextItem(self.color)
            item.setPos(pos)
            self.scene().addItem(item)
            item.begin_edit()
            self.undo_stack.push(AddCommand(self.scene(), item, "Add text"))
            return
        if self.tool == "step":
            item = StepItem(self._step, self.color)
            item.setFlags(MOVABLE)
            item.setPos(pos)
            self._step += 1
            self.undo_stack.push(AddCommand(self.scene(), item, "Add step"))
            return

        self.scene().clearSelection()
        self._start = pos
        self._cur = self._make(self.tool)
        self.scene().addItem(self._cur)
        self._apply_geometry(pos)

    def mouseMoveEvent(self, e):
        if self._cur is not None:
            self._apply_geometry(self.mapToScene(e.position().toPoint()))
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._cur is None:
            return super().mouseReleaseEvent(e)
        item, self._cur = self._cur, None
        self._apply_geometry(self.mapToScene(e.position().toPoint()))

        if self.tool in ("line", "arrow"):
            too_small = item.line().length() < 4
        else:
            r = item.rect()
            too_small = r.width() < 4 or r.height() < 4

        if self.tool == "crop":
            rect = item.rect()
            self.scene().removeItem(item)
            if not too_small:
                self.undo_stack.push(CropCommand(self, self.sceneRect(), rect))
            return
        if too_small:
            self.scene().removeItem(item)
            return
        self.undo_stack.push(AddCommand(self.scene(), item))

    # --- keys / actions ---------------------------------------------------
    def keyPressEvent(self, e):
        editing = isinstance(self.scene().focusItem(), TextItem) and \
            self.scene().focusItem().textInteractionFlags() & Qt.TextEditorInteraction
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and not editing:
            self.delete_selected()
            return
        super().keyPressEvent(e)

    def delete_selected(self):
        items = self.scene().selectedItems()
        if not items:
            return
        self.undo_stack.beginMacro("Delete")
        for it in items:
            self.undo_stack.push(DeleteCommand(self.scene(), it))
        self.undo_stack.endMacro()

    def render_image(self) -> QImage:
        self.scene().clearSelection()
        focus = self.scene().focusItem()
        if focus is not None:
            focus.clearFocus()
        rect = self.sceneRect()
        img = QImage(rect.size().toSize(), QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        self.scene().render(p, QRectF(img.rect()), rect)
        p.end()
        return img


# --- window ----------------------------------------------------------------
class EditorWindow(QMainWindow):
    saved = Signal(str)

    def __init__(self, path: Path):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.path = Path(path)
        image = QImage(str(self.path)).convertToFormat(QImage.Format_ARGB32)
        self.canvas = Canvas(image)
        self.setCentralWidget(self.canvas)
        self._build_toolbar()
        self._update_title()

        avail = QGuiApplication.primaryScreen().availableGeometry()
        w = min(image.width() + 40, int(avail.width() * 0.9))
        h = min(image.height() + 120, int(avail.height() * 0.9))
        self.resize(max(w, 900), max(h, 500))

    def showEvent(self, e):
        super().showEvent(e)
        self.canvas.reset_view()

    # --- UI -------------------------------------------------------------
    def _build_toolbar(self):
        tb = QToolBar("Tools")
        tb.setMovable(False)
        self.addToolBar(tb)

        group = QActionGroup(self)
        group.setExclusive(True)
        for name, label, tip in TOOLS:
            act = QAction(label, self, checkable=True)
            act.setToolTip(tip)
            act.setData(name)
            act.triggered.connect(lambda _=False, n=name: self.canvas.set_tool(n))
            group.addAction(act)
            tb.addAction(act)
            if name == "select":
                act.setChecked(True)

        tb.addSeparator()
        self.color_action = QAction("Colour", self)
        self.color_action.triggered.connect(self._pick_color)
        tb.addAction(self.color_action)
        self._refresh_color_icon()

        tb.addWidget(QLabel(" Width "))
        spin = QSpinBox()
        spin.setRange(1, 40)
        spin.setValue(self.canvas.pen_width)
        spin.valueChanged.connect(lambda v: setattr(self.canvas, "pen_width", v))
        tb.addWidget(spin)

        tb.addSeparator()
        undo = self.canvas.undo_stack.createUndoAction(self, "Undo")
        undo.setShortcut(QKeySequence.Undo)
        redo = self.canvas.undo_stack.createRedoAction(self, "Redo")
        redo.setShortcut(QKeySequence.Redo)
        tb.addAction(undo)
        tb.addAction(redo)

        tb.addSeparator()
        for text, handler, shortcut in [
            ("Copy", self.copy_image, QKeySequence.Copy),
            ("Save", self.save, QKeySequence.Save),
            ("Save As...", self.save_as, QKeySequence("Ctrl+Shift+S")),
            ("Fit", self.canvas.reset_view, None),
            ("100%", self.canvas.actual_size, None),
        ]:
            act = QAction(text, self)
            if shortcut is not None:
                act.setShortcut(shortcut)
            act.triggered.connect(lambda _=False, h=handler: h())
            tb.addAction(act)

    def _refresh_color_icon(self):
        pix = QPixmap(16, 16)
        pix.fill(self.canvas.color)
        from PySide6.QtGui import QIcon
        self.color_action.setIcon(QIcon(pix))

    def _pick_color(self):
        c = QColorDialog.getColor(self.canvas.color, self, "Annotation colour")
        if c.isValid():
            self.canvas.color = c
            self._refresh_color_icon()

    def _update_title(self):
        self.setWindowTitle(f"SnipLab - {self.path.name}")

    # --- actions ----------------------------------------------------------
    def copy_image(self):
        focus = self.canvas.scene().focusItem()
        if isinstance(focus, TextItem):
            selected = focus.textCursor().selectedText()
            if selected:
                QGuiApplication.clipboard().setText(selected)
                return
        QGuiApplication.clipboard().setImage(self.canvas.render_image())
        self.statusBar().showMessage("Copied image to clipboard", 3000)

    def save(self):
        if self.canvas.render_image().save(str(self.path)):
            self.canvas.undo_stack.setClean()
            self.statusBar().showMessage(f"Saved {self.path.name}", 3000)
            self.saved.emit(str(self.path))
            return True
        QMessageBox.warning(self, "SnipLab", f"Could not save {self.path}")
        return False

    def save_as(self):
        name, _ = QFileDialog.getSaveFileName(self, "Save As", str(self.path),
                                              "PNG (*.png);;JPEG (*.jpg *.jpeg);;Bitmap (*.bmp)")
        if not name:
            return False
        self.path = Path(name)
        self._update_title()
        return self.save()

    def closeEvent(self, e):
        if self.canvas.undo_stack.isClean():
            e.accept()
            return
        answer = QMessageBox.question(
            self, "SnipLab", "Save changes before closing?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if answer == QMessageBox.Cancel or (answer == QMessageBox.Save and not self.save()):
            e.ignore()
        else:
            e.accept()
