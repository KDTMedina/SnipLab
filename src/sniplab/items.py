"""Custom annotation items for the editor canvas."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QGraphicsEllipseItem, QGraphicsItem, QGraphicsLineItem,
                               QGraphicsPathItem, QGraphicsRectItem, QGraphicsTextItem)

MOVABLE = QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable
SELECTABLE = QGraphicsItem.ItemIsSelectable

ALIGNMENTS = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter, "right": Qt.AlignRight}


class ArrowItem(QGraphicsLineItem):
    def boundingRect(self) -> QRectF:
        pad = max(20.0, self.pen().widthF() * 5)
        return super().boundingRect().adjusted(-pad, -pad, pad, pad)

    def paint(self, painter: QPainter, option, widget=None):
        line = self.line()
        pen = self.pen()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(pen)
        painter.drawLine(line)
        if line.length() < 1:
            return
        size = max(14.0, pen.widthF() * 4)
        a = math.atan2(line.dy(), line.dx())
        tip = line.p2()
        left = QPointF(tip.x() - size * math.cos(a - math.pi / 6), tip.y() - size * math.sin(a - math.pi / 6))
        right = QPointF(tip.x() - size * math.cos(a + math.pi / 6), tip.y() - size * math.sin(a + math.pi / 6))
        painter.setBrush(pen.color())
        painter.drawPolygon(QPolygonF([tip, left, right]))


class PixelateItem(QGraphicsRectItem):
    """Hides whatever is under it by pixelating the base screenshot."""

    def __init__(self, source: QImage, block: int = 10):
        super().__init__()
        self.source = source
        self.block = block
        self.setPen(Qt.NoPen)

    def paint(self, painter: QPainter, option, widget=None):
        scene_rect = self.mapRectToScene(self.rect()).toAlignedRect().intersected(self.source.rect())
        if scene_rect.isEmpty():
            return
        sub = self.source.copy(scene_rect)
        small = sub.scaled(max(1, sub.width() // self.block), max(1, sub.height() // self.block),
                           Qt.IgnoreAspectRatio, Qt.FastTransformation)
        big = small.scaled(sub.size(), Qt.IgnoreAspectRatio, Qt.FastTransformation)
        painter.drawImage(self.mapFromScene(QPointF(scene_rect.topLeft())), big)
        if self.isSelected():
            pen = QPen(QColor("#4fc3f7"), 1, Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.rect())


class StepItem(QGraphicsEllipseItem):
    """A numbered circle, handy for tutorials."""
    RADIUS = 15

    def __init__(self, number: int, color: QColor):
        r = self.RADIUS
        super().__init__(-r, -r, 2 * r, 2 * r)
        self.number = number
        self.setBrush(color)
        self.setPen(QPen(Qt.white, 2))

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing)
        super().paint(painter, option, widget)
        painter.setPen(Qt.white)
        font = QFont("Segoe UI", 12)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignCenter, str(self.number))


class TextItem(QGraphicsTextItem):
    # Emitted when an editing session ends: (item, text_before, text_after).
    # The canvas turns this into an undo command.
    edited = Signal(object, str, str)

    def __init__(self, color: QColor, point_size: int = 20):
        super().__init__("")
        self.is_new = True          # True until the canvas has recorded it for undo
        self._text_before = ""
        self.align = "left"
        self.setDefaultTextColor(color)
        font = QFont("Segoe UI", point_size)
        font.setBold(True)
        self.setFont(font)
        self.setFlags(MOVABLE)

    def set_align(self, name: str):
        self.align = name
        option = self.document().defaultTextOption()
        option.setAlignment(ALIGNMENTS[name])
        self.document().setDefaultTextOption(option)

    def begin_edit(self):
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.setFocus()

    def mouseDoubleClickEvent(self, event):
        self.begin_edit()
        super().mouseDoubleClickEvent(event)

    def focusInEvent(self, event):
        self._text_before = self.toPlainText()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        cursor = self.textCursor()
        cursor.clearSelection()
        self.setTextCursor(cursor)
        after = self.toPlainText()
        if self.is_new or after != self._text_before:
            self.edited.emit(self, self._text_before, after)


class FocusItem(QGraphicsRectItem):
    """Highlight Area / Grayscale: everything OUTSIDE the rectangle is shown
    in monochrome (and optionally blurred); the rectangle stays untouched."""

    def __init__(self, source: QImage, blur: int = 0):
        super().__init__()
        self.source = source
        self.blur = blur
        self._cache: QImage | None = None
        self.setPen(Qt.NoPen)
        self.setZValue(-500)            # above the screenshot, below other annotations

    def set_blur(self, level: int):
        if level != self.blur:
            self.blur = level
            self._cache = None
            self.update()

    def _processed(self) -> QImage:
        if self._cache is None:
            img = self.source.convertToFormat(QImage.Format_Grayscale8).convertToFormat(QImage.Format_ARGB32)
            if self.blur > 0:
                k = 1 + self.blur // 2
                w, h = img.width(), img.height()
                small = img.scaled(max(1, w // k), max(1, h // k), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                img = small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            self._cache = img
        return self._cache

    def _full(self) -> QRectF:
        return self.mapRectFromScene(QRectF(self.source.rect()))

    def boundingRect(self) -> QRectF:
        return self._full().adjusted(-2, -2, 2, 2)

    def shape(self) -> QPainterPath:       # only the highlighted area is clickable
        path = QPainterPath()
        path.addRect(self.rect())
        return path

    def paint(self, painter: QPainter, option, widget=None):
        full = self._full()
        outside = QPainterPath()
        outside.addRect(full)
        hole = QPainterPath()
        hole.addRect(self.rect())
        painter.save()
        painter.setClipPath(outside.subtracted(hole))
        painter.drawImage(full.topLeft(), self._processed())
        painter.restore()
        painter.setPen(QPen(QColor(255, 255, 255, 200), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.rect())
        if self.isSelected():
            painter.setPen(QPen(QColor("#4fc3f7"), 1, Qt.DashLine))
            painter.drawRect(self.rect())


class FreehandItem(QGraphicsPathItem):
    def __init__(self, pen: QPen):
        super().__init__()
        self.setPen(pen)
        self.setBrush(Qt.NoBrush)
        self._last = QPointF()

    def begin(self, p: QPointF):
        self._last = QPointF(p)
        self.setPath(QPainterPath(p))

    def add_point(self, p: QPointF):
        path = self.path()
        path.quadTo(self._last, (self._last + p) / 2)    # smooth curve through the points
        self._last = QPointF(p)
        self.setPath(path)

    def finish(self):
        path = self.path()
        path.lineTo(self._last)
        self.setPath(path)


class BubbleItem(QGraphicsRectItem):
    """Speech bubble: a resizable rounded box whose tail points at `tail`.
    The tail tip stays where it is when the box is resized."""

    def __init__(self, color: QColor):
        super().__init__()
        self.text = ""
        self.tail = QPointF(0, 0)
        pen = QPen(color, 3)
        pen.setJoinStyle(Qt.RoundJoin)
        self.setPen(pen)
        self.setBrush(QColor(255, 255, 255))

    def set_tail(self, p: QPointF):
        self.prepareGeometryChange()
        self.tail = QPointF(p)
        self.update()

    def default_tail(self):
        r = self.rect()
        self.set_tail(QPointF(r.left() + r.width() * 0.25, r.bottom() + max(24.0, r.height() * 0.5)))

    def _outline(self) -> QPainterPath:
        r, t = self.rect(), self.tail
        path = QPainterPath()
        path.addRoundedRect(r, 10, 10)
        gx = max(r.left() - t.x(), t.x() - r.right(), 0.0)
        gy = max(r.top() - t.y(), t.y() - r.bottom(), 0.0)
        if gx == 0 and gy == 0:                       # tip inside the box: no tail
            return path

        def clamp(v, lo, hi):
            return (lo + hi) / 2 if lo > hi else max(lo, min(v, hi))

        if gy >= gx:                                  # tail leaves through top/bottom edge
            half = min(14.0, r.width() / 4)
            cx = clamp(t.x(), r.left() + half + 8, r.right() - half - 8)
            y = r.bottom() if t.y() > r.bottom() else r.top()
            base = (QPointF(cx - half, y), QPointF(cx + half, y))
        else:                                         # ... through left/right edge
            half = min(14.0, r.height() / 4)
            cy = clamp(t.y(), r.top() + half + 8, r.bottom() - half - 8)
            x = r.right() if t.x() > r.right() else r.left()
            base = (QPointF(x, cy - half), QPointF(x, cy + half))
        tail = QPainterPath()
        tail.addPolygon(QPolygonF([base[0], t, base[1]]))
        tail.closeSubpath()
        return path.united(tail)

    def boundingRect(self) -> QRectF:
        pad = self.pen().widthF() + 2
        return self._outline().boundingRect().adjusted(-pad, -pad, pad, pad)

    def shape(self) -> QPainterPath:
        return self._outline()

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(self.pen())
        painter.setBrush(self.brush())
        painter.drawPath(self._outline())
        if self.text:
            font = QFont("Segoe UI", 12)
            painter.setFont(font)
            painter.setPen(QColor("#222222"))
            painter.drawText(self.rect().adjusted(10, 6, -10, -6),
                             Qt.AlignCenter.value | Qt.TextWordWrap.value, self.text)
        if self.isSelected():
            painter.setPen(QPen(QColor("#4fc3f7"), 1, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.rect())