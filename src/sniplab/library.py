"""Capture library: where captures are stored and how they are named.

Pure Python (no Qt) so it is easy to unit test.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp"}


def default_dir() -> Path:
    return Path.home() / "Pictures" / "SnipLab"


def new_capture_path(folder: Path, prefix: str = "SnipLab", ext: str = ".png",
                     now: datetime | None = None) -> Path:
    """Return a unique, timestamped path inside *folder* (created if missing)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
    candidate = folder / f"{prefix}_{stamp}{ext}"
    n = 1
    while candidate.exists():
        candidate = folder / f"{prefix}_{stamp}_{n}{ext}"
        n += 1
    return candidate


def list_captures(folder: Path) -> list[Path]:
    """Image files in *folder*, newest first."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def delete_capture(path: Path) -> bool:
    try:
        Path(path).unlink()
        return True
    except OSError:
        return False
