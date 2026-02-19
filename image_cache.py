#!/usr/bin/python3
"""Disk-based image cache for generated images."""
import os
import time
from pathlib import Path

# Cache root and image subdirectory
_CACHE_DIR = Path(os.path.expanduser("~/.cache/ComfyApp/images"))


def _ensure_dir():
    """Create the cache directory if it doesn't exist."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)


def list_images() -> list[Path]:
    """
    Return all cached image paths sorted oldest-first.

    Sorting oldest-first means callers that prepend to a list
    (e.g. the gallery) will display the newest image at the top.
    """
    if not _CACHE_DIR.exists():
        return []
    return sorted(
        (p for p in _CACHE_DIR.iterdir() if p.is_file()),
        key=lambda p: p.stat().st_mtime
    )


def save_image(data: bytes) -> Path:
    """
    Write raw image bytes to the cache directory.

    Uses a nanosecond timestamp filename to avoid collisions.
    Returns the path of the saved file.
    """
    _ensure_dir()
    filename = f"{time.time_ns()}.png"
    path = _CACHE_DIR / filename
    path.write_bytes(data)
    return path


def load_image(path: Path) -> bytes:
    """Read and return raw bytes from a cached image path."""
    return Path(path).read_bytes()


def cleanup_old(max_age_days: int = 1):
    """
    Delete cached images older than max_age_days days.

    Silently skips files that cannot be removed.
    """
    if not _CACHE_DIR.exists():
        return
    cutoff = time.time() - max_age_days * 86400
    for entry in _CACHE_DIR.iterdir():
        if not entry.is_file():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
        except Exception as e:
            print(f"Cache cleanup error ({entry.name}): {e}", flush=True)
