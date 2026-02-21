#!/usr/bin/python3
"""App-wide configuration, loaded from and saved to disk."""
import json
import os
import sys

_CONFIG_DIR = os.path.expanduser("~/.config/cozyapp")
_CONFIG_PATH = os.path.join(_CONFIG_DIR, "config.json")
_STATE_PATH = os.path.join(_CONFIG_DIR, "state.json")

_DEFAULTS = {
    "host": "127.0.0.1",
    "port": 8188,
    "tag_blacklist": [],
    "cache_max_age_days": 1,
}

# In-memory config dict, populated by load()
_config = dict(_DEFAULTS)


def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(base_path, relative_path)


def load():
    """Load config from disk, falling back to defaults for missing keys."""
    global _config
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Merge so missing keys fall back to defaults
        _config = {**_DEFAULTS, **data}
    except FileNotFoundError:
        _config = dict(_DEFAULTS)
    except Exception as e:
        print(f"Config load error: {e}", flush=True)
        _config = dict(_DEFAULTS)


def save():
    """Persist the current config to disk."""
    try:
        os.makedirs(_CONFIG_DIR, exist_ok=True)
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(_config, f, indent=2)
    except Exception as e:
        print(f"Config save error: {e}", flush=True)


def load_state() -> dict:
    """Load state from disk; returns an empty dict if missing."""
    try:
        with open(_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"State load error: {e}", flush=True)
        return {}


def save_state(state: dict):
    """Persist state dict to disk."""
    try:
        os.makedirs(_CONFIG_DIR, exist_ok=True)
        with open(_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"State save error: {e}", flush=True)


def get(key):
    """Return the value for the given config key."""
    return _config.get(key, _DEFAULTS.get(key))


def set(key, value):
    """Update a config value in memory (call save() to persist)."""
    _config[key] = value


def server_address():
    """Return the ComfyUI server address as 'host:port'."""
    return f"{get('host')}:{get('port')}"
