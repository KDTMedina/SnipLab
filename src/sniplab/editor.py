"""Annotation editor: a QGraphicsView canvas plus the editor window."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QLineF, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QAction, QActionGroup, QColor, QGuiApplication, QImage, QKeySequence,
                           QPainter, QPainterPath, QPen, QPixmap, QTransform, QUndoCommand,
                           QUndoStack)
from PySide6.QtWidgets import (QColorDialog, QComboBox, QInputDialog, QFileDialog, QGraphicsEllipseItem, QGraphicsItem,
                               QGraphicsLineItem,
                               QGraphicsRectItem, QGraphicsScene, QGraphicsView, QLabel,
                               QMainWindow, QMessageBox, QSpinBox, QToolBar)

from . import __version__
from .icons import align_icon, glyph_icon, has_glyph_font, swatch_icon, tool_icon
from .items import (MOVABLE, SELECTABLE, ArrowItem, BubbleItem, FocusItem, FreehandItem,
                    PixelateItem, StepItem, TextItem)

TOOLS = [
    ("select", "Select", "Select and move annotations (Delete removes)"),
    ("rect", "Rectangle", "Draw a rectangle"),
    ("ellipse", "Ellipse", "Draw an ellipse"),
    ("line", "Line", "Draw a line"),
    ("arrow", "Arrow", "Draw an arrow"),
    ("freehand", "Freehand", "Draw freehand"),
    ("text", "Text", "Click to add text (double-click text to edit)"),
    ("bubble", "Speech Bubble", "Drag to draw; drag the tip handle to aim it; double-click to edit text"),
    ("highlight", "Highlight", "Highlight, Highlight Area or Grayscale (see the options bar)"),
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
    if isinstance(item, (PixelateItem, FocusItem)):
        return None
    if isinstance(item, FreehandItem):
        return QColor(item.pen().color())
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
    if isinstance(item, (PixelateItem, FocusItem)):
        return
    if isinstance(item, FreehandItem):
        pen = item.pen()
        pen.setColor(color)
        item.setPen(pen)
    elif isinstance(item, TextItem):
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
    state = {"pos": QPointF(item.pos()), "scale": item.scale(), "z": item.zValue()}
    color = get_color(item)
    if color is not None:
        state["color"] = color
    if isinstance(item, QGraphicsLineItem):
        state["line"] = QLineF(item.line())
    elif isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem)):
        state["rect"] = QRectF(item.rect())
    if isinstance(item, FreehandItem):
        state["path"] = QPainterPath(item.path())
    if isinstance(item, BubbleItem):
        state["tail"] = QPointF(item.tail)
        state["btext"] = item.text
    if isinstance(item, FocusItem):
        state["blur"] = item.blur
    if isinstance(item, TextItem):
        state["text"] = item.toPlainText()
        state["align"] = item.align
    return state


def set_state(item, state: dict):
    item.setPos(state["pos"])
    item.setScale(state["scale"])
    item.setZValue(state["z"])
    if "color" in state:
        set_color(item, state["color"])
    if "line" in state:
        item.setLine(state["line"])
    if "rect" in state:
        item.setRect(state["rect"])
    if "path" in state:
        item.setPath(state["path"])
    if "tail" in state:
        item.set_tail(state["tail"])
    if "btext" in state:
        item.text = state["btext"]
        item.update()
    if "blur" in state:
        item.set_blur(state["blur"])
    if "text" in state and item.toPlainText() != state["text"]:
        item.setPlainText(state["text"])
    if "align" in state:
        item.set_align(state["align"])


class ModifyCommand(QUndoCommand):
    """Move, resize, recolour or re-text an existing annotation.

    Commands pushed with the same merge_key on the same item (e.g. dragging the
    blur spin box) collapse into a single undo step.
    """

    def __init__(self, item, old: dict, new: dict, text="Modify", merge_key=None):
        super().__init__(text)
        self.item, self.old, self.new, self.merge_key = item, old, new, merge_key

    def id(self):
        return 1001 if self.merge_key else -1

    def mergeWith(self, other):
        if (self.merge_key and isinstance(other, ModifyCommand)
                and other.item is self.item and other.merge_key == self.merge_key):
            self.new = other.new
            return True
        return False

    def redo(self):
        set_state(self.item, self.new)

    def undo(self):
        set_state(self.item, self.old)


class ReplaceImageCommand(QUndoCommand):
    """Swap the whole base image (used by Rotate). Annotations are merged into
    the new image; undo brings the old image and the editable annotations back."""

    def __init__(self, canvas: "Canvas", new_image: QImage, text: str):
        super().__init__(text)
        self.canvas, self.new_image = canvas, new_image
        self.old_image, self.old_rect = canvas.base, QRectF(canvas.sceneRect())
        self.old_items = canvas.annotation_items()

    def redo(self):
        self.canvas.replace_base(self.new_image, QRectF(self.new_image.rect()), [])

    def undo(self):
        self.canvas.replace_base(self.old_image, self.old_rect, self.old_items)


# --- canvas ----------------------------------------------------------------
class Canvas(QGraphicsView):
    cursor_moved = Signal(int, int)     # pointer position in image pixels
    zoom_changed = Signal(float)        # 1.0 == 100%
    tool_changed = Signal(str)

    def __init__(self, image: QImage):
        super().__init__()
        self.base = image
        scene = QGraphicsScene(self)
        self.setScene(scene)
        self.bg = scene.addPixmap(QPixmap.fromImage(image))
        self.bg.setZValue(-1000)
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
        self._hover = None             # annotation under the pointer (shows its handles)
        self._applying = False         # True while the undo stack is running a command
        self.text_align = "left"
        self.highlight_mode = "highlight"   # highlight | area | gray
        self.blur_level = 8
        self.setMouseTracking(True)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.set_tool("select")

    # --- tool state ---------------------------------------------------
    def set_tool(self, name: str):
        self.tool = name
        self.setDragMode(QGraphicsView.RubberBandDrag if name == "select" else QGraphicsView.NoDrag)
        self.viewport().setCursor(Qt.ArrowCursor if name == "select" else Qt.CrossCursor)
        self.tool_changed.emit(name)

    def set_canvas_rect(self, rect: QRectF):
        self.scene().setSceneRect(rect)
        self.reset_view()

    # --- undo / redo / image replacement ---------------------------------------
    def editing_text_item(self):
        f = self.scene().focusItem()
        if isinstance(f, TextItem) and f.textInteractionFlags() & Qt.TextEditorInteraction:
            return f
        return None

    def undo(self):
        text = self.editing_text_item()
        if text is not None:            # while typing, Ctrl+Z undoes typing only
            text.document().undo()
            return
        self._run_stack(self.undo_stack.undo)

    def redo(self):
        text = self.editing_text_item()
        if text is not None:
            text.document().redo()
            return
        self._run_stack(self.undo_stack.redo)

    def _run_stack(self, fn):
        self._applying = True           # ignore focus events caused by the undo itself
        try:
            fn()
        finally:
            self._applying = False
        self._hover = None
        self.viewport().update()

    def annotation_items(self) -> list:
        return [it for it in self.scene().items() if it is not self.bg]

    def replace_base(self, image: QImage, rect: QRectF, items: list):
        scene = self.scene()
        for it in self.annotation_items():
            scene.removeItem(it)
        self.base = image
        self.bg.setPixmap(QPixmap.fromImage(image))
        scene.setSceneRect(rect)
        for it in items:
            scene.addItem(it)
        self._hover = None
        self.reset_view()

    def rotate_image(self, degrees: int):
        """Rotate the picture. Annotations are merged into it (undo restores them)."""
        flat = self.render_image()
        rotated = flat.transformed(QTransform().rotate(degrees), Qt.SmoothTransformation)
        self.undo_stack.push(ReplaceImageCommand(self, rotated, f"Rotate {degrees} degrees"))

    # --- view helpers -------------------------------------------------
    def reset_view(self):
        """Fit the image in the window, but never enlarge past 100%."""
        self.resetTransform()
        vp = self.viewport().size()
        sr = self.sceneRect()
        if sr.width() > vp.width() or sr.height() > vp.height():
            self.fitInView(sr, Qt.KeepAspectRatio)
        self.zoom_changed.emit(self.transform().m11())

    def actual_size(self):
        self.resetTransform()
        self.zoom_changed.emit(1.0)

    def zoom_by(self, factor: float):
        self.scale(factor, factor)
        self.zoom_changed.emit(self.transform().m11())

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.zoom_by(1.15 ** (e.angleDelta().y() / 120))
        else:
            super().wheelEvent(e)

    # --- selection / stacking order -------------------------------------------
    def select_all(self):
        for it in self.scene().items():
            if it.flags() & QGraphicsItem.ItemIsSelectable:
                it.setSelected(True)

    def reorder(self, to_front: bool) -> int:
        """Move the selected annotations above or below all the others (undoable)."""
        selected = self.scene().selectedItems()
        others = [it.zValue() for it in self.scene().items()
                  if it.zValue() > -1000 and it not in selected]
        if not selected or not others:
            return 0
        z = max(others) + 1 if to_front else min(others) - 1
        changes = []
        for it in selected:
            old = get_state(it)
            it.setZValue(z)
            changes.append((it, old, get_state(it)))
        self.undo_stack.beginMacro("Bring to front" if to_front else "Send to back")
        for it, old, new in changes:
            self.undo_stack.push(ModifyCommand(it, old, new, "Reorder"))
        self.undo_stack.endMacro()
        return len(changes)

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

    def _candidates(self) -> list:
        """Items whose handles are live: the selected one and the one under the pointer."""
        items = []
        sel = self.scene().selectedItems()
        if len(sel) == 1:
            items.append(sel[0])
        if self._hover is not None and self._hover not in items:
            items.append(self._hover)
        return [it for it in items if it.scene() is self.scene()]

    @staticmethod
    def _box_handles(r: QRectF) -> dict:
        cx, cy = r.center().x(), r.center().y()
        return {"tl": r.topLeft(), "t": QPointF(cx, r.top()), "tr": r.topRight(),
                "r": QPointF(r.right(), cy), "br": r.bottomRight(),
                "b": QPointF(cx, r.bottom()), "bl": r.bottomLeft(),
                "l": QPointF(r.left(), cy)}

    def _handles(self, item) -> dict:
        """Handle name -> position in scene coordinates."""
        if isinstance(item, StepItem):
            return {}
        if isinstance(item, TextItem):
            return {"br": item.mapRectToScene(item.boundingRect()).bottomRight()}
        if isinstance(item, QGraphicsLineItem):
            line = item.line()
            return {"p1": item.mapToScene(line.p1()), "p2": item.mapToScene(line.p2())}
        if isinstance(item, FreehandItem):
            return self._box_handles(item.mapRectToScene(item.path().boundingRect()))
        if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem)):
            handles = self._box_handles(item.mapRectToScene(item.rect()))
            if isinstance(item, BubbleItem):
                handles["tail"] = item.mapToScene(item.tail)
            return handles
        return {}

    def _paint_handles(self, painter: QPainter):
        half = self.HANDLE_PX / 2 / max(self.transform().m11(), 0.01)
        pen = QPen(QColor("#1e88e5"), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        for item in self._candidates():
            for name, p in self._handles(item).items():
                painter.setBrush(QColor("#ffb300") if name == "tail" else Qt.white)
                box = QRectF(p.x() - half, p.y() - half, half * 2, half * 2)
                if name == "tail":
                    painter.drawEllipse(box)
                else:
                    painter.drawRect(box)

    def _handle_at(self, view_pos):
        for item in self._candidates():
            for name, p in self._handles(item).items():
                vp = self.mapFromScene(p)
                if abs(vp.x() - view_pos.x()) <= self.HANDLE_PX and abs(vp.y() - view_pos.y()) <= self.HANDLE_PX:
                    return item, name
        return None, None

    def _update_hover(self, view_pos):
        it = self.itemAt(view_pos)
        new = it if (it is not None and it is not self.bg and self._handles(it)) else None
        if new is not self._hover:
            self._hover = new
            self.viewport().update()

    def leaveEvent(self, e):
        self._hover = None
        self.viewport().update()
        super().leaveEvent(e)

    def _start_resize(self, item, handle: str) -> dict:
        rs = {"item": item, "handle": handle, "old": get_state(item),
              "orig_width": max(1.0, item.mapRectToScene(item.boundingRect()).width())}
        if isinstance(item, FreehandItem):
            local = item.path().boundingRect()
            rs["orig_path"], rs["orig_local"] = QPainterPath(item.path()), local
            rs["orig_rect"] = item.mapRectToScene(local)
        elif hasattr(item, "rect"):
            rs["orig_rect"] = item.mapRectToScene(item.rect())
        return rs

    def _do_resize(self, pos: QPointF):
        rs = self._resize
        item, h = rs["item"], rs["handle"]
        if h in ("p1", "p2"):
            line, pt = item.line(), item.mapFromScene(pos)
            item.setLine(QLineF(pt, line.p2()) if h == "p1" else QLineF(line.p1(), pt))
        elif h == "tail":
            item.set_tail(item.mapFromScene(pos))
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
            if new.width() < 2 or new.height() < 2:
                return
            local = item.mapRectFromScene(new)
            if isinstance(item, FreehandItem):
                old = rs["orig_local"]
                sx = local.width() / old.width() if old.width() > 0 else 1.0
                sy = local.height() / old.height() if old.height() > 0 else 1.0
                tr = QTransform()
                tr.translate(local.left(), local.top())
                tr.scale(sx, sy)
                tr.translate(-old.left(), -old.top())
                item.setPath(tr.map(rs["orig_path"]))
            else:
                item.setRect(local)

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
        elif tool == "freehand":
            item = FreehandItem(pen)
        elif tool == "bubble":
            item = BubbleItem(self.color)
        elif tool == "highlight":
            if self.highlight_mode == "highlight":
                item = QGraphicsRectItem()
                fill = QColor(self.color)
                fill.setAlpha(110)
                item.setBrush(fill)
                item.setPen(Qt.NoPen)
            else:   # "area" (monochrome + blur outside) or "gray" (monochrome outside)
                item = FocusItem(self.base, self.blur_level if self.highlight_mode == "area" else 0)
        elif tool == "blur":
            item = PixelateItem(self.base)
        else:  # crop preview
            item = QGraphicsRectItem()
            item.setPen(QPen(Qt.white, 1, Qt.DashLine))
            item.setBrush(QColor(0, 0, 0, 60))
        if tool != "crop":
            item.setFlags(SELECTABLE if isinstance(item, FocusItem) else MOVABLE)
        return item

    def _apply_geometry(self, pos: QPointF):
        a, item = self._start, self._cur
        if self.tool in ("line", "arrow"):
            item.setLine(QLineF(a, pos))
        elif self.tool == "freehand":
            item.add_point(pos)
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
        if self._applying or item.scene() is not scene:
            return          # focus change caused by undo/redo/removal: not a user edit
        if item.is_new:
            if after.strip():
                item.is_new = False
                self.undo_stack.push(AddCommand(scene, item, "Add text"))
            else:
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

    def _edit_bubble_text(self, item: BubbleItem):
        text, ok = QInputDialog.getMultiLineText(self, "Speech bubble", "Text:", item.text)
        if ok and text != item.text:
            old = get_state(item)
            item.text = text
            item.update()
            self.undo_stack.push(ModifyCommand(item, old, get_state(item), "Edit text"))

    def mouseDoubleClickEvent(self, e):
        it = self.itemAt(e.position().toPoint())
        if isinstance(it, BubbleItem):
            self._edit_bubble_text(it)
            return
        super().mouseDoubleClickEvent(e)

    def mousePressEvent(self, e):
        view_pos = e.position().toPoint()
        pos = self.mapToScene(view_pos)

        # Handles are live with EVERY tool: grab one and resize, no need to switch to Select.
        if e.button() == Qt.LeftButton and self._cur is None:
            item, handle = self._handle_at(view_pos)
            if item is not None:
                self.scene().clearSelection()
                item.setSelected(True)
                self._resize = self._start_resize(item, handle)
                return

        if self.tool == "select":
            self._snap = {}
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
            item.set_align(self.text_align)
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
        if self.tool == "freehand":
            self._cur.begin(pos)
        self.scene().addItem(self._cur)
        self._apply_geometry(pos)

    def mouseMoveEvent(self, e):
        view_pos = e.position().toPoint()
        sp = self.mapToScene(view_pos)
        self.cursor_moved.emit(int(sp.x()), int(sp.y()))
        if self._resize is not None:
            self._do_resize(sp)
            return
        if self._cur is not None:
            self._apply_geometry(sp)
            return
        if not e.buttons():
            _, handle = self._handle_at(view_pos)
            if handle is None:
                self._update_hover(view_pos)
                _, handle = self._handle_at(view_pos)
            if handle is not None:
                self.viewport().setCursor(self.CURSORS.get(handle, Qt.SizeAllCursor))
            else:
                self.viewport().setCursor(Qt.ArrowCursor if self.tool == "select" else Qt.CrossCursor)
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
        elif self.tool == "freehand":
            item.finish()
            r = item.path().boundingRect()
            too_small = r.width() < 2 and r.height() < 2
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
        if self.tool == "bubble":
            item.default_tail()
        self.undo_stack.push(AddCommand(self.scene(), item))
        item.setSelected(True)            # its handles are ready straight away
        if self.tool == "bubble":
            self._edit_bubble_text(item)

    # --- keys / actions ---------------------------------------------------
    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.editing_text_item() is None:
            self.delete_selected()
            return
        super().keyPressEvent(e)

    def _modify(self, items, label: str, fn, merge: bool = False) -> int:
        """Apply fn to each item and record it as ONE undo step. Returns how many changed."""
        changes = []
        for item in items:
            old = get_state(item)
            fn(item)
            new = get_state(item)
            if new != old:
                changes.append((item, old, new))
        if len(changes) == 1:
            item, old, new = changes[0]
            self.undo_stack.push(ModifyCommand(item, old, new, label, label if merge else None))
        elif changes:
            self.undo_stack.beginMacro(label)
            for item, old, new in changes:
                self.undo_stack.push(ModifyCommand(item, old, new, label))
            self.undo_stack.endMacro()
        return len(changes)

    def apply_color(self, color: QColor) -> int:
        items = [it for it in self.scene().selectedItems() if get_color(it) is not None]
        return self._modify(items, "Change colour", lambda it: set_color(it, color))

    def apply_align(self, name: str) -> int:
        targets = [it for it in self.scene().selectedItems() if isinstance(it, TextItem)]
        editing = self.editing_text_item()
        if editing is not None and editing not in targets:
            targets.append(editing)
        fresh = [t for t in targets if t.is_new]          # not on the undo stack yet
        for t in fresh:
            t.set_align(name)
        rest = [t for t in targets if not t.is_new]
        return len(fresh) + self._modify(rest, "Text alignment", lambda t: t.set_align(name))

    def apply_blur(self, level: int) -> int:
        items = [it for it in self.scene().selectedItems() if isinstance(it, FocusItem) and it.blur > 0]
        return self._modify(items, "Blur amount", lambda it: it.set_blur(level), merge=True)

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
    """Layout: menu bar on top, horizontal toolbar, vertical tool palette on the
    left (Photoshop style), canvas in the middle, status bar at the bottom."""
    saved = Signal(str)

    def __init__(self, path: Path):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.path = Path(path)
        image = QImage(str(self.path)).convertToFormat(QImage.Format_ARGB32)
        self.canvas = Canvas(image)
        self.setCentralWidget(self.canvas)

        self.act: dict[str, QAction] = {}
        self.tool_actions: dict[str, QAction] = {}
        self._build_actions()
        self._build_menus()
        self._build_top_toolbar()
        self._build_options_toolbar()
        self._build_tool_palette()
        self._build_status_bar()
        self._wire_signals()
        self._update_title()

        avail = QGuiApplication.primaryScreen().availableGeometry()
        w = min(image.width() + 120, int(avail.width() * 0.9))
        h = min(image.height() + 200, int(avail.height() * 0.9))
        self.resize(max(w, 960), max(h, 560))

    def showEvent(self, e):
        super().showEvent(e)
        self.canvas.reset_view()

    # --- actions -----------------------------------------------------------
    def _action(self, key: str, text: str, handler, shortcut=None, icon=None, tip: str = ""):
        act = QAction(text, self)
        if icon is not None:
            act.setIcon(icon)
        if shortcut is not None:
            act.setShortcut(shortcut if isinstance(shortcut, QKeySequence) else QKeySequence(shortcut))
        if tip:
            act.setToolTip(tip)
        act.triggered.connect(lambda _=False, h=handler: h())
        self.act[key] = act
        return act

    def _build_actions(self):
        c = self.canvas
        # File
        self._action("save", "&Save", self.save, QKeySequence.Save, glyph_icon("save"), "Save (Ctrl+S)")
        self._action("save_as", "Save &As...", self.save_as, "F12", glyph_icon("save_as"),
                     "Save As (F12)")   # not Ctrl+Shift+S: that is the global capture hotkey
        self._action("copy", "&Copy Image to Clipboard", self.copy_image, QKeySequence.Copy,
                     glyph_icon("copy"), "Copy to clipboard (Ctrl+C)")
        self._action("close", "&Close", self.close, "Ctrl+W", glyph_icon("close"))
        # Edit
        self._action("undo", "&Undo", c.undo, QKeySequence.Undo, glyph_icon("undo"), "Undo (Ctrl+Z)")
        self._action("redo", "&Redo", c.redo, QKeySequence.Redo, glyph_icon("redo"), "Redo (Ctrl+Y)")
        self._refresh_undo_actions()
        self._action("delete", "&Delete\tDel", c.delete_selected, icon=glyph_icon("delete"),
                     tip="Delete selected objects (Del)")
        self._action("select_all", "Select &All Objects", self._select_all, "Ctrl+Shift+A",
                     glyph_icon("select_all"))
        # Object
        self._action("colour", "Change &Colour...", self._pick_color,
                     icon=swatch_icon(c.color), tip="Colour of the selected objects and new objects")
        self._action("front", "Bring to &Front", lambda: self._reorder(True))
        self._action("back", "Send to &Back", lambda: self._reorder(False))
        # Image
        self._action("rot_cw", "Rotate &Right 90\u00b0", lambda: self._rotate(90))
        self._action("rot_ccw", "Rotate &Left 90\u00b0", lambda: self._rotate(-90))
        self._action("rot_180", "Rotate &180\u00b0", lambda: self._rotate(180))
        # View (toolbar only)
        self._action("zoom_out", "Zoom Out", lambda: c.zoom_by(1 / 1.25), icon=glyph_icon("zoom_out"),
                     tip="Zoom out")
        self._action("zoom_in", "Zoom In", lambda: c.zoom_by(1.25), icon=glyph_icon("zoom_in"),
                     tip="Zoom in (Ctrl + mouse wheel)")
        self._action("fit", "Fit", c.reset_view, tip="Fit image to window")
        self._action("actual", "100%", c.actual_size, tip="Actual size")
        # Help
        self._action("shortcuts", "&Keyboard Shortcuts", self._show_shortcuts)
        self._action("about", "&About SnipLab", self._show_about)

        # Tool palette actions (shared by the palette and the Object menu)
        group = QActionGroup(self)
        group.setExclusive(True)
        for name, label, tip in TOOLS:
            act = QAction(tool_icon(name), label, self, checkable=True)
            act.setToolTip(f"{label} - {tip}")
            act.setStatusTip(tip)
            act.triggered.connect(lambda _=False, n=name: self._set_tool(n))
            group.addAction(act)
            self.tool_actions[name] = act
        self.tool_actions["select"].setChecked(True)

    def _build_menus(self):
        mb = self.menuBar()
        a = self.act

        file_menu = mb.addMenu("&File")
        file_menu.addActions([a["save"], a["save_as"]])
        file_menu.addSeparator()
        file_menu.addAction(a["copy"])
        file_menu.addSeparator()
        file_menu.addAction(a["close"])

        edit_menu = mb.addMenu("&Edit")
        edit_menu.addActions([a["undo"], a["redo"]])
        edit_menu.addSeparator()
        edit_menu.addActions([a["select_all"], a["delete"]])

        obj_menu = mb.addMenu("&Object")
        for name, _label, _tip in TOOLS:
            obj_menu.addAction(self.tool_actions[name])
        obj_menu.addSeparator()
        obj_menu.addAction(a["colour"])
        obj_menu.addActions([a["front"], a["back"]])
        obj_menu.addSeparator()
        obj_menu.addAction(a["delete"])

        img_menu = mb.addMenu("&Image")
        img_menu.addActions([a["rot_cw"], a["rot_ccw"], a["rot_180"]])

        help_menu = mb.addMenu("&Help")
        help_menu.addActions([a["shortcuts"], a["about"]])

    def _build_top_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonIconOnly if has_glyph_font() else Qt.ToolButtonTextOnly)
        self.addToolBar(Qt.TopToolBarArea, tb)
        a = self.act

        tb.addActions([a["save"], a["save_as"], a["copy"]])
        tb.addSeparator()
        tb.addActions([a["undo"], a["redo"]])
        tb.addSeparator()
        tb.addAction(a["colour"])
        tb.addWidget(QLabel("  Width "))
        spin = QSpinBox()
        spin.setRange(1, 40)
        spin.setValue(self.canvas.pen_width)
        spin.setToolTip("Line width for new objects")
        spin.valueChanged.connect(lambda v: setattr(self.canvas, "pen_width", v))
        tb.addWidget(spin)
        tb.addSeparator()
        tb.addActions([a["zoom_out"], a["zoom_in"], a["fit"], a["actual"]])
        # "Fit" and "100%" have no icon, so always show them as text
        for key in ("fit", "actual", "colour"):
            btn = tb.widgetForAction(a[key])
            if btn is not None:
                btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon if key == "colour"
                                       else Qt.ToolButtonTextOnly)

    def _build_options_toolbar(self):
        """Second row: options for the active tool (text alignment, highlight mode, blur)."""
        self.addToolBarBreak(Qt.TopToolBarArea)
        tb = QToolBar("Options")
        tb.setMovable(False)
        tb.setIconSize(QSize(18, 18))
        tb.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.addToolBar(Qt.TopToolBarArea, tb)
        self._opt = {"text": [], "mode": [], "blur": []}

        self._opt_hint = tb.addWidget(QLabel("  No extra options for this tool"))

        self._opt["text"].append(tb.addWidget(QLabel("  Text alignment ")))
        group = QActionGroup(self)
        group.setExclusive(True)
        self.align_actions = {}
        for key, label in (("left", "Align Left"), ("center", "Align Centre"), ("right", "Align Right")):
            act = QAction(align_icon(key), label, self, checkable=True)
            act.setToolTip(label + " (shows on multi-line text)")
            act.triggered.connect(lambda _=False, k=key: self._set_align(k))
            group.addAction(act)
            tb.addAction(act)
            self._opt["text"].append(act)
            self.align_actions[key] = act
        self.align_actions["left"].setChecked(True)

        self._opt["mode"].append(tb.addWidget(QLabel("  Highlight mode ")))
        self.mode_combo = QComboBox()
        for label, key in (("Highlight", "highlight"), ("Highlight Area", "area"), ("Grayscale", "gray")):
            self.mode_combo.addItem(label, key)
        self.mode_combo.setToolTip("Highlight: marker.  Highlight Area: rest of the picture becomes "
                                   "monochrome and blurred.  Grayscale: rest becomes monochrome.")
        self.mode_combo.currentIndexChanged.connect(
            lambda i: self._set_hl_mode(self.mode_combo.itemData(i)))
        self._opt["mode"].append(tb.addWidget(self.mode_combo))

        self._opt["blur"].append(tb.addWidget(QLabel("  Blur amount ")))
        self.blur_spin = QSpinBox()
        self.blur_spin.setRange(1, 30)
        self.blur_spin.setValue(self.canvas.blur_level)
        self.blur_spin.setToolTip("How strongly the area outside the highlight is blurred")
        self.blur_spin.valueChanged.connect(self._set_blur)
        self._opt["blur"].append(tb.addWidget(self.blur_spin))

    def _wire_signals(self):
        c = self.canvas
        c.tool_changed.connect(self._refresh_options)
        c.scene().selectionChanged.connect(self._refresh_options)
        c.scene().focusItemChanged.connect(self._refresh_options)
        c.scene().focusItemChanged.connect(self._refresh_undo_actions)
        for sig in (c.undo_stack.canUndoChanged, c.undo_stack.canRedoChanged,
                    c.undo_stack.undoTextChanged, c.undo_stack.redoTextChanged):
            sig.connect(self._refresh_undo_actions)
        self._refresh_options()
        self._refresh_undo_actions()

    def _refresh_undo_actions(self, *_):
        if "undo" not in self.act:
            return
        stack = self.canvas.undo_stack
        if self.canvas.editing_text_item() is not None:      # typing: Ctrl+Z acts on the text
            self.act["undo"].setText("&Undo Typing")
            self.act["redo"].setText("&Redo Typing")
            self.act["undo"].setEnabled(True)
            self.act["redo"].setEnabled(True)
            return
        self.act["undo"].setText(("&Undo " + stack.undoText()).strip())
        self.act["redo"].setText(("&Redo " + stack.redoText()).strip())
        self.act["undo"].setEnabled(stack.canUndo())
        self.act["redo"].setEnabled(stack.canRedo())

    def _refresh_options(self, *_):
        c = self.canvas
        sel = c.scene().selectedItems()
        one = sel[0] if len(sel) == 1 else None
        text_item = c.editing_text_item() or (one if isinstance(one, TextItem) else None)
        focus_item = one if isinstance(one, FocusItem) and one.blur > 0 else None
        show_text = c.tool == "text" or text_item is not None
        show_mode = c.tool == "highlight"
        show_blur = (show_mode and c.highlight_mode == "area") or focus_item is not None
        for key, visible in (("text", show_text), ("mode", show_mode), ("blur", show_blur)):
            for act in self._opt[key]:
                act.setVisible(visible)
        self._opt_hint.setVisible(not (show_text or show_mode or show_blur))
        if show_text:
            self.align_actions[text_item.align if text_item else c.text_align].setChecked(True)
        if show_blur:
            self.blur_spin.blockSignals(True)
            self.blur_spin.setValue(focus_item.blur if focus_item else c.blur_level)
            self.blur_spin.blockSignals(False)

    def _set_align(self, key: str):
        self.canvas.text_align = key
        self.canvas.apply_align(key)

    def _set_hl_mode(self, mode: str):
        self.canvas.highlight_mode = mode
        self._refresh_options()

    def _set_blur(self, value: int):
        self.canvas.blur_level = value
        self.canvas.apply_blur(value)

    def _rotate(self, degrees: int):
        self.canvas.rotate_image(degrees)
        self.statusBar().showMessage(
            "Rotated. Annotations were merged into the image (Undo brings them back).", 5000)

    def _build_tool_palette(self):
        tb = QToolBar("Tools", self)
        tb.setOrientation(Qt.Vertical)
        tb.setMovable(False)
        tb.setIconSize(QSize(28, 28))
        tb.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.addToolBar(Qt.LeftToolBarArea, tb)
        groups = [["select"], ["rect", "ellipse", "line", "arrow", "freehand"],
                  ["text", "bubble", "highlight", "blur", "step"], ["crop"]]
        for i, names in enumerate(groups):
            if i:
                tb.addSeparator()
            for n in names:
                tb.addAction(self.tool_actions[n])

    def _build_status_bar(self):
        sb = self.statusBar()
        self.lbl_tool = QLabel()
        self.lbl_size = QLabel()
        self.lbl_cursor = QLabel("")
        self.lbl_zoom = QLabel("100%")
        for lbl, width in ((self.lbl_tool, 110), (self.lbl_cursor, 110),
                           (self.lbl_size, 110), (self.lbl_zoom, 60)):
            lbl.setMinimumWidth(width)
            sb.addPermanentWidget(lbl)
        sb.showMessage("Ready")
        self._set_tool_label("select")
        self._update_size_label(self.canvas.sceneRect())
        self.canvas.cursor_moved.connect(lambda x, y: self.lbl_cursor.setText(f"{x}, {y} px"))
        self.canvas.zoom_changed.connect(lambda z: self.lbl_zoom.setText(f"{z * 100:.0f}%"))
        self.canvas.scene().sceneRectChanged.connect(self._update_size_label)

    def _update_size_label(self, rect: QRectF):
        self.lbl_size.setText(f"{int(rect.width())} x {int(rect.height())} px")

    def _set_tool_label(self, name: str):
        label = next(l for n, l, _ in TOOLS if n == name)
        self.lbl_tool.setText(f"Tool: {label}")

    # --- handlers ------------------------------------------------------------
    def _set_tool(self, name: str):
        self.canvas.set_tool(name)
        self._set_tool_label(name)

    def _select_all(self):
        self.tool_actions["select"].trigger()
        self.canvas.select_all()

    def _reorder(self, to_front: bool):
        n = self.canvas.reorder(to_front)
        if not n:
            self.statusBar().showMessage("Select an object first", 3000)

    def _pick_color(self):
        c = QColorDialog.getColor(self.canvas.color, self, "Annotation colour")
        if c.isValid():
            self.canvas.color = c            # also the colour for new annotations
            self.act["colour"].setIcon(swatch_icon(c))
            n = self.canvas.apply_color(c)   # and recolour whatever is selected
            if n:
                self.statusBar().showMessage(f"Changed colour of {n} object(s)", 3000)

    def _update_title(self):
        self.setWindowTitle(f"SnipLab - {self.path.name}")

    def _show_shortcuts(self):
        QMessageBox.information(self, "Keyboard shortcuts", (
            "Ctrl+S\tSave\n"
            "F12\tSave As\n"
            "Ctrl+C\tCopy image to clipboard\n"
            "Ctrl+Z / Ctrl+Y\tUndo / Redo (while typing text: undoes typing)\n"
            "Hover an object\tits handles appear - drag them with any tool\n"
            "Double-click a bubble\tEdit its text\n"
            "Del\tDelete selected objects\n"
            "Ctrl+Shift+A\tSelect all objects\n"
            "Ctrl+W\tClose editor\n"
            "Ctrl + mouse wheel\tZoom\n\n"
            "Global capture hotkeys:\n"
            "Ctrl+Shift+S\tRegion\n"
            "Ctrl+Shift+F\tFull screen\n"
            "Ctrl+Shift+W\tActive window"))

    def _show_about(self):
        QMessageBox.about(self, "About SnipLab",
                          f"<b>SnipLab {__version__}</b><br>Screen capture and annotation.")

    # --- file actions ----------------------------------------------------------
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