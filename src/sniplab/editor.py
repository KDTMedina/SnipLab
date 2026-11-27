"""Annotation editor: a QGraphicsView canvas plus the editor window."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QAction, QActionGroup, QColor, QGuiApplication, QImage, QKeySequence,
                           QPainter, QPainterPath, QPen, QPixmap, QUndoCommand, QUndoStack)
from PySide6.QtWidgets import (QColorDialog, QFileDialog, QGraphicsEllipseItem, QGraphicsLineItem,
                               QGraphicsRectItem, QGraphicsScene, QGraphicsView, QLabel,
                               QMainWindow, QMessageBox, QSpinBox, QToolBar)

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


def get_color(item) -> QColor | None:
    """The user-facing colour of an annotation, or None if it has none (blur)."""
    if isinstance(item, PixelateItem):
        return None
    if isinstance(item, TextItem):
        return QColor(item.defaultTextColor())
    if isinstance(item, StepItem):
        return QColor(item.brush().color())
    if isinstance(item, QGraphicsLineItem):          # line and arrow
        return QColor(item.pen().color())
    if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem)):
        if item.pen().style() == Qt.NoPen:           # highlighter: filled, no outline
            return QColor(item.brush().color())
        return QColor(item.pen().color())
    return None


def set_color(item, color: QColor):
    color = QColor(color)
    if isinstance(item, PixelateItem):
        return
    if isinstance(item, TextItem):
        item.setDefaultTextColor(color)
    elif isinstance(item, StepItem):
        item.setBrush(color)
    elif isinstance(item, (QGraphicsLineItem, QGraphicsRectItem, QGraphicsEllipseItem)):
        if not isinstance(item, QGraphicsLineItem) and item.pen().style() == Qt.NoPen:
            color.setAlpha(item.brush().color().alpha())   # keep highlighter see-through
            item.setBrush(color)
        else:
            pen = item.pen()
            pen.setColor(color)
            item.setPen(pen)
    item.update()


def get_state(item) -> dict:
    """Snapshot everything about an item that the user can change."""
    state = {"pos": QPointF(item.pos()), "scale": item.scale()}
    color = get_color(item)
    if color is not None:
        state["color"] = color
    if isinstance(item, QGraphicsLineItem):
        state["line"] = QLineF(item.line())
    elif isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem)):
        state["rect"] = QRectF(item.rect())
    if isinstance(item, TextItem):
        state["text"] = item.toPlainText()
    return state


def set_state(item, state: dict):
    item.setPos(state["pos"])
    item.setScale(state["scale"])
    if "color" in state:
        set_color(item, state["color"])
    if "line" in state:
        item.setLine(state["line"])
    if "rect" in state:
        item.setRect(state["rect"])
    if "text" in state and item.toPlainText() != state["text"]:
        item.setPlainText(state["text"])


class ModifyCommand(QUndoCommand):
    """Move, resize or re-text an existing annotation."""

    def __init__(self, item, old: dict, new: dict, text="Modify"):
        super().__init__(text)
        self.item, self.old, self.new = item, old, new

    def redo(self):
        set_state(self.item, self.new)

    def undo(self):
        set_state(self.item, self.old)


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
        self._snap: dict = {}          # item -> state at mouse press (for move undo)
        self._resize: dict | None = None
        self.setMouseTracking(True)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
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
        self._paint_handles(painter)

    # --- resize handles -----------------------------------------------------
    HANDLE_PX = 9
    CURSORS = {"tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
               "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
               "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
               "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor}

    def _active_item(self):
        if self.tool != "select":
            return None
        sel = self.scene().selectedItems()
        return sel[0] if len(sel) == 1 else None

    def _handles(self, item) -> dict:
        """Handle name -> position in scene coordinates."""
        if isinstance(item, StepItem):
            return {}
        if isinstance(item, TextItem):
            return {"br": item.mapRectToScene(item.boundingRect()).bottomRight()}
        if isinstance(item, QGraphicsLineItem):
            line = item.line()
            return {"p1": item.mapToScene(line.p1()), "p2": item.mapToScene(line.p2())}
        if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem)):
            r = item.mapRectToScene(item.rect())
            cx, cy = r.center().x(), r.center().y()
            return {"tl": r.topLeft(), "t": QPointF(cx, r.top()), "tr": r.topRight(),
                    "r": QPointF(r.right(), cy), "br": r.bottomRight(),
                    "b": QPointF(cx, r.bottom()), "bl": r.bottomLeft(),
                    "l": QPointF(r.left(), cy)}
        return {}

    def _paint_handles(self, painter: QPainter):
        item = self._active_item()
        if item is None:
            return
        half = self.HANDLE_PX / 2 / max(self.transform().m11(), 0.01)
        pen = QPen(QColor("#1e88e5"), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.white)
        for p in self._handles(item).values():
            painter.drawRect(QRectF(p.x() - half, p.y() - half, half * 2, half * 2))

    def _handle_at(self, view_pos):
        item = self._active_item()
        if item is None:
            return None, None
        for name, p in self._handles(item).items():
            vp = self.mapFromScene(p)
            if abs(vp.x() - view_pos.x()) <= self.HANDLE_PX and abs(vp.y() - view_pos.y()) <= self.HANDLE_PX:
                return item, name
        return None, None

    def _do_resize(self, pos: QPointF):
        rs = self._resize
        item, h = rs["item"], rs["handle"]
        if h in ("p1", "p2"):
            line, pt = item.line(), item.mapFromScene(pos)
            item.setLine(QLineF(pt, line.p2()) if h == "p1" else QLineF(line.p1(), pt))
        elif isinstance(item, TextItem):
            new_w = max(10.0, pos.x() - item.scenePos().x())
            item.setScale(max(0.2, rs["old"]["scale"] * new_w / rs["orig_width"]))
        else:
            r = rs["orig_rect"]
            left, top, right, bottom = r.left(), r.top(), r.right(), r.bottom()
            if "l" in h:
                left = pos.x()
            if "r" in h:
                right = pos.x()
            if "t" in h:
                top = pos.y()
            if "b" in h:
                bottom = pos.y()
            new = QRectF(QPointF(left, top), QPointF(right, bottom)).normalized()
            if new.width() >= 2 and new.height() >= 2:
                item.setRect(item.mapRectFromScene(new))

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
    def next_step_number(self) -> int:
        """Highest step number currently on the canvas, plus one.

        Derived from what is actually in the scene, so deleting or undoing the
        last step frees its number, while a gap in the middle is ignored.
        """
        numbers = [it.number for it in self.scene().items() if isinstance(it, StepItem)]
        return max(numbers, default=0) + 1

    def _text_edited(self, item: TextItem, before: str, after: str):
        """Turn a finished text-editing session into one undo step."""
        scene = self.scene()
        if item.is_new:
            if after.strip():
                item.is_new = False
                self.undo_stack.push(AddCommand(scene, item, "Add text"))
            elif item.scene() is scene:
                scene.removeItem(item)
        elif not after.strip():
            new = get_state(item)
            self.undo_stack.beginMacro("Delete text")
            self.undo_stack.push(ModifyCommand(item, dict(new, text=before), new, "Edit text"))
            self.undo_stack.push(DeleteCommand(scene, item))
            self.undo_stack.endMacro()
        elif before != after:
            new = get_state(item)
            self.undo_stack.push(ModifyCommand(item, dict(new, text=before), new, "Edit text"))

    def mousePressEvent(self, e):
        view_pos = e.position().toPoint()
        pos = self.mapToScene(view_pos)

        if self.tool == "select":
            self._snap = {}
            if e.button() == Qt.LeftButton:
                item, handle = self._handle_at(view_pos)
                if item is not None:
                    self._resize = {"item": item, "handle": handle, "old": get_state(item),
                                    "orig_rect": item.mapRectToScene(item.rect())
                                    if hasattr(item, "rect") else QRectF(),
                                    "orig_width": max(1.0, item.mapRectToScene(item.boundingRect()).width())}
                    return
            super().mousePressEvent(e)
            if e.button() == Qt.LeftButton:
                self._snap = {it: get_state(it) for it in self.scene().selectedItems()}
            return
        if e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)

        if self.tool == "text":
            if isinstance(self.itemAt(view_pos), TextItem):
                return super().mousePressEvent(e)
            item = TextItem(self.color)
            item.edited.connect(self._text_edited)
            item.setPos(pos)
            self.scene().addItem(item)
            item.begin_edit()
            return
        if self.tool == "step":
            item = StepItem(self.next_step_number(), self.color)
            item.setFlags(MOVABLE)
            item.setPos(pos)
            self.undo_stack.push(AddCommand(self.scene(), item, "Add step"))
            return

        self.scene().clearSelection()
        self._start = pos
        self._cur = self._make(self.tool)
        self.scene().addItem(self._cur)
        self._apply_geometry(pos)

    def mouseMoveEvent(self, e):
        if self._resize is not None:
            self._do_resize(self.mapToScene(e.position().toPoint()))
            return
        if self._cur is not None:
            self._apply_geometry(self.mapToScene(e.position().toPoint()))
            return
        if self.tool == "select" and not e.buttons():
            _, handle = self._handle_at(e.position().toPoint())
            self.viewport().setCursor(self.CURSORS.get(handle, Qt.SizeAllCursor if handle else Qt.ArrowCursor))
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._resize is not None:
            rs, self._resize = self._resize, None
            new = get_state(rs["item"])
            if new != rs["old"]:
                self.undo_stack.push(ModifyCommand(rs["item"], rs["old"], new, "Resize"))
            return
        if self.tool == "select":
            super().mouseReleaseEvent(e)
            changed = [(it, old, get_state(it)) for it, old in self._snap.items()
                       if it.scene() is not None and get_state(it) != old]
            self._snap = {}
            if changed:
                self.undo_stack.beginMacro("Move")
                for it, old, new in changed:
                    self.undo_stack.push(ModifyCommand(it, old, new, "Move"))
                self.undo_stack.endMacro()
            return
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

    def apply_color(self, color: QColor) -> int:
        """Recolour the selected annotations as one undo step. Returns how many changed."""
        changes = []
        for item in self.scene().selectedItems():
            old = get_state(item)
            if "color" not in old:
                continue
            set_color(item, color)
            new = get_state(item)
            if new != old:
                changes.append((item, old, new))
        if changes:
            self.undo_stack.beginMacro("Change colour")
            for item, old, new in changes:
                self.undo_stack.push(ModifyCommand(item, old, new, "Change colour"))
            self.undo_stack.endMacro()
        return len(changes)

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
            self.canvas.color = c            # also the colour for new annotations
            self._refresh_color_icon()
            n = self.canvas.apply_color(c)   # and recolour whatever is selected
            if n:
                self.statusBar().showMessage(f"Changed colour of {n} item(s)", 3000)

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