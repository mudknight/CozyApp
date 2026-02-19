#!/usr/bin/python3
"""Disk-based image cache for generated images."""
import json
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
        (p for p in _CACHE_DIR.iterdir()
         if p.is_file() and p.suffix == '.png'),
        key=lambda p: p.stat().st_mtime
    )


def save_image(data: bytes, image_info: dict = None) -> Path:
    """
    Write raw image bytes to the cache directory.

    Uses a nanosecond timestamp filename to avoid collisions.
    If image_info is provided, saves it as a sidecar JSON file
    with the same stem. Returns the path of the saved image.
    """
    _ensure_dir()
    filename = f"{time.time_ns()}.png"
    path = _CACHE_DIR / filename
    path.write_bytes(data)
    if image_info:
        json_path = path.with_suffix('.json')
        json_path.write_text(
            json.dumps(image_info), encoding='utf-8'
        )
    return path


def load_image(path: Path) -> bytes:
    """Read and return raw bytes from a cached image path."""
    return Path(path).read_bytes()


def load_image_info(path: Path) -> dict | None:
    """
    Load the sidecar JSON for a cached image, if it exists.

    Returns a dict on success, or None if no sidecar is present.
    """
    json_path = Path(path).with_suffix('.json')
    try:
        return json.loads(json_path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"Cache: failed to read sidecar {json_path.name}: {e}",
              flush=True)
        return None


def cleanup_old(max_age_days: int = 1):
    """
    Delete cached images older than max_age_days days.

    Also removes any sidecar JSON files for deleted images.
    Silently skips files that cannot be removed.
    """
    if not _CACHE_DIR.exists():
        return
    cutoff = time.time() - max_age_days * 86400
    for entry in _CACHE_DIR.iterdir():
        if not entry.is_file() or entry.suffix != '.png':
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
                sidecar = entry.with_suffix('.json')
                if sidecar.exists():
                    sidecar.unlink()
        except Exception as e:
            print(f"Cache cleanup error ({entry.name}): {e}", flush=True)
