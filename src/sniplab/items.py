"""Custom annotation items for the editor canvas."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QGraphicsEllipseItem, QGraphicsItem, QGraphicsLineItem,
                               QGraphicsRectItem, QGraphicsTextItem)

MOVABLE = QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable


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
        self.setDefaultTextColor(color)
        font = QFont("Segoe UI", point_size)
        font.setBold(True)
        self.setFont(font)
        self.setFlags(MOVABLE)

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