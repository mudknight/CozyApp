#!/usr/bin/python3
"""App-wide configuration, loaded from and saved to disk."""
import json
import os

_CONFIG_PATH = os.path.expanduser("~/.config/comfyapp/config.json")

_DEFAULTS = {
    "host": "127.0.0.1",
    "port": 8188,
    "tag_blacklist": [],
}

# In-memory config dict, populated by load()
_config = dict(_DEFAULTS)


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
        os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(_config, f, indent=2)
    except Exception as e:
        print(f"Config save error: {e}", flush=True)


def get(key):
    """Return the value for the given config key."""
    return _config.get(key, _DEFAULTS.get(key))


def set(key, value):
    """Update a config value in memory (call save() to persist)."""
    _config[key] = value


def server_address():
    """Return the ComfyUI server address as 'host:port'."""
    return f"{get('host')}:{get('port')}"
