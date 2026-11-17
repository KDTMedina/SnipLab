"""Full-screen overlay for drag-selecting a capture region."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget


class RegionOverlay(QWidget):
    selected = Signal(QPixmap)
    cancelled = Signal()

    def __init__(self, screen, pixmap: QPixmap):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self._pix = pixmap
        self._origin: QPoint | None = None
        self._sel = QRect()
        self._done = False

    # --- painting -----------------------------------------------------
    def paintEvent(self, _event):
        p = QPainter(self)
        whole = self.rect()
        p.drawPixmap(whole, self._pix)
        p.fillRect(whole, QColor(0, 0, 0, 110))
        if not self._sel.isNull():
            p.save()
            p.setClipRect(self._sel)
            p.drawPixmap(whole, self._pix)
            p.restore()
            p.setPen(QPen(QColor("#4fc3f7"), 2))
            p.drawRect(self._sel)
            dpr = self._pix.devicePixelRatio()
            label = f"{int(self._sel.width() * dpr)} x {int(self._sel.height() * dpr)}"
            p.setPen(Qt.white)
            p.fillRect(QRect(self._sel.x(), max(0, self._sel.y() - 22), 90, 20), QColor(0, 0, 0, 170))
            p.drawText(self._sel.x() + 6, max(14, self._sel.y() - 7), label)
        else:
            p.setPen(Qt.white)
            p.drawText(20, 30, "Drag to select a region  -  Esc to cancel")

    # --- mouse / keys -------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._origin = e.position().toPoint()
            self._sel = QRect(self._origin, self._origin)
        elif e.button() == Qt.RightButton:
            self._cancel()

    def mouseMoveEvent(self, e):
        if self._origin is not None:
            self._sel = QRect(self._origin, e.position().toPoint()).normalized()
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self._origin is None:
            return
        sel = QRect(self._origin, e.position().toPoint()).normalized()
        self._origin = None
        if sel.width() < 4 or sel.height() < 4:
            self._sel = QRect()
            self.update()
            return
        dpr = self._pix.devicePixelRatio()
        device_rect = QRect(round(sel.x() * dpr), round(sel.y() * dpr),
                            round(sel.width() * dpr), round(sel.height() * dpr))
        crop = self._pix.copy(device_rect)
        crop.setDevicePixelRatio(1.0)
        self._done = True
        self.selected.emit(crop)
        self.close()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self._cancel()

    def _cancel(self):
        self._done = True
        self.cancelled.emit()
        self.close()
