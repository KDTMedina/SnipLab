"""Screen capture helpers (Qt based, plus Win32 for the active window)."""
from __future__ import annotations

import sys

from PySide6.QtCore import QRect
from PySide6.QtGui import QCursor, QGuiApplication, QPixmap, QScreen


def screen_at_cursor() -> QScreen:
    return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()


def grab_screen(screen: QScreen | None = None) -> QPixmap:
    screen = screen or screen_at_cursor()
    return screen.grabWindow(0)


def grab_active_window() -> QPixmap | None:
    """Capture the foreground window. Windows only; returns None elsewhere."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    dwmapi = ctypes.windll.dwmapi
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    rect = wintypes.RECT()
    DWMWA_EXTENDED_FRAME_BOUNDS = 9  # excludes the invisible resize border
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
                                    ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(rect))

    cx, cy = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
    target = QGuiApplication.primaryScreen()
    for s in QGuiApplication.screens():
        g, dpr = s.geometry(), s.devicePixelRatio()
        phys = QRect(int(g.x() * dpr), int(g.y() * dpr), int(g.width() * dpr), int(g.height() * dpr))
        if phys.contains(cx, cy):
            target = s
            break

    dpr = target.devicePixelRatio()
    origin_x, origin_y = int(target.geometry().x() * dpr), int(target.geometry().y() * dpr)
    full = target.grabWindow(0)
    crop = QRect(rect.left - origin_x, rect.top - origin_y,
                 rect.right - rect.left, rect.bottom - rect.top)
    crop = crop.intersected(full.rect())
    if crop.isEmpty():
        return None
    out = full.copy(crop)
    out.setDevicePixelRatio(1.0)
    return out
