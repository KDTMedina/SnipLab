"""Icons.

* Tool icons are drawn with QPainter, so they look the same on every machine.
* Command icons (Save, Undo, ...) use Microsoft's own icon font -
  "Segoe Fluent Icons" (Windows 11) or "Segoe MDL2 Assets" (Windows 10).
  If neither font exists (e.g. on Linux) those actions simply show text.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QPainter,
                           QPainterPath, QPen, QPixmap, QPolygonF)

# Segoe MDL2 Assets / Segoe Fluent Icons code points
GLYPHS = {
    "save": 0xE74E, "save_as": 0xE792, "copy": 0xE8C8, "undo": 0xE7A7, "redo": 0xE7A6,
    "delete": 0xE74D, "zoom_in": 0xE8A3, "zoom_out": 0xE71F, "close": 0xE711,
    "select_all": 0xE8B3,
}

_family_cache: list = []


def icon_font_family() -> str | None:
    if not _family_cache:
        available = set(QFontDatabase.families())
        _family_cache.append(next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets")
                                   if f in available), None))
    return _family_cache[0]


def has_glyph_font() -> bool:
    return icon_font_family() is not None


def _text_color() -> QColor:
    from PySide6.QtGui import QPalette
    return QGuiApplication.palette().color(QPalette.WindowText)


def _canvas(size: int):
    pix = QPixmap(size * 2, size * 2)
    pix.setDevicePixelRatio(2)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    return pix, p


def glyph_icon(name: str, size: int = 20) -> QIcon:
    family = icon_font_family()
    if family is None:
        return QIcon()
    pix, p = _canvas(size)
    font = QFont(family)
    font.setPixelSize(int(size * 0.8))
    p.setFont(font)
    p.setPen(_text_color())
    p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, chr(GLYPHS[name]))
    p.end()
    return QIcon(pix)


def tool_icon(name: str, size: int = 28) -> QIcon:
    fg = _text_color()
    pix, p = _canvas(size)
    s = size / 28.0
    p.scale(s, s)                      # draw on a 28 x 28 grid
    pen = QPen(fg, 2.2)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    accent = QColor("#1e88e5")

    if name == "select":
        p.setBrush(fg)
        p.drawPolygon(QPolygonF([QPointF(7, 4), QPointF(7, 22), QPointF(12, 17.5),
                                 QPointF(15.5, 25), QPointF(18.5, 23.5),
                                 QPointF(15, 16.5), QPointF(21.5, 16.5)]))
    elif name == "rect":
        p.drawRect(QRectF(5, 7, 18, 14))
    elif name == "ellipse":
        p.drawEllipse(QRectF(4, 6, 20, 16))
    elif name == "line":
        p.drawLine(QPointF(5, 23), QPointF(23, 5))
    elif name == "arrow":
        p.drawLine(QPointF(5, 23), QPointF(22, 6))
        p.setBrush(fg)
        p.drawPolygon(QPolygonF([QPointF(24, 4), QPointF(14, 7), QPointF(21, 14)]))
    elif name == "text":
        f = QFont("Segoe UI", 17)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, 0, 28, 28), Qt.AlignCenter, "T")
    elif name == "highlight":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 214, 0, 200))
        p.drawRoundedRect(QRectF(3, 9, 22, 11), 2, 2)
        p.setPen(QPen(fg, 1.6))
        p.drawLine(QPointF(7, 14.5), QPointF(21, 14.5))
    elif name == "blur":
        p.setPen(Qt.NoPen)
        for row in range(3):
            for col in range(3):
                shade = 70 if (row + col) % 2 == 0 else 190
                p.setBrush(QColor(shade, shade, shade) if fg.lightness() < 128
                           else QColor(255 - shade, 255 - shade, 255 - shade))
                p.drawRect(QRectF(5 + col * 6, 5 + row * 6, 6, 6))
    elif name == "step":
        p.setPen(Qt.NoPen)
        p.setBrush(accent)
        p.drawEllipse(QRectF(3, 3, 22, 22))
        f = QFont("Segoe UI", 12)
        f.setBold(True)
        p.setFont(f)
        p.setPen(Qt.white)
        p.drawText(QRectF(3, 3, 22, 22), Qt.AlignCenter, "1")
    elif name == "freehand":
        path = QPainterPath(QPointF(4, 20))
        path.cubicTo(QPointF(7, 3), QPointF(14, 3), QPointF(14, 14))
        path.cubicTo(QPointF(14, 25), QPointF(21, 24), QPointF(24, 8))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
    elif name == "bubble":
        p.setBrush(Qt.NoBrush)
        body = QPainterPath()
        body.addRoundedRect(QRectF(3, 4, 22, 15), 5, 5)
        tail = QPainterPath()
        tail.addPolygon(QPolygonF([QPointF(8, 18), QPointF(6, 25), QPointF(14, 18)]))
        p.drawPath(body.united(tail))
    elif name == "crop":
        p.drawPolyline(QPolygonF([QPointF(9, 3), QPointF(9, 19), QPointF(25, 19)]))
        p.drawPolyline(QPolygonF([QPointF(3, 9), QPointF(19, 9), QPointF(19, 25)]))
    p.end()
    return QIcon(pix)


def swatch_icon(color: QColor, size: int = 20) -> QIcon:
    pix, p = _canvas(size)
    p.setPen(QPen(_text_color(), 1))
    p.setBrush(color)
    p.drawRoundedRect(QRectF(2, 2, size - 4, size - 4), 3, 3)
    p.end()
    return QIcon(pix)


def align_icon(kind: str, size: int = 18) -> QIcon:
    """Text alignment icon: 'left', 'center' or 'right'."""
    pix, p = _canvas(size)
    p.setPen(QPen(_text_color(), 1.6, Qt.SolidLine, Qt.RoundCap))
    s = size / 18.0
    p.scale(s, s)
    for y, w in ((3, 14), (7, 8), (11, 14), (15, 6)):
        if kind == "left":
            x = 2
        elif kind == "center":
            x = (18 - w) / 2
        else:
            x = 16 - w
        p.drawLine(QPointF(x, y), QPointF(x + w, y))
    p.end()
    return QIcon(pix)
