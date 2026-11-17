"""Global hotkeys using pynput (optional dependency)."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

DEFAULT_KEYS = {
    "region": "<ctrl>+<shift>+s",
    "fullscreen": "<ctrl>+<shift>+f",
    "window": "<ctrl>+<shift>+w",
}


class HotkeyManager(QObject):
    region = Signal()
    fullscreen = Signal()
    window = Signal()

    def __init__(self, keys: dict[str, str] | None = None):
        super().__init__()
        self._keys = keys or DEFAULT_KEYS
        self._listener = None

    def start(self) -> bool:
        try:
            from pynput import keyboard
        except Exception:
            return False
        mapping = {
            self._keys["region"]: self.region.emit,
            self._keys["fullscreen"]: self.fullscreen.emit,
            self._keys["window"]: self.window.emit,
        }
        try:
            self._listener = keyboard.GlobalHotKeys(mapping)
            self._listener.daemon = True
            self._listener.start()
        except Exception:
            self._listener = None
            return False
        return True

    def stop(self):
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
