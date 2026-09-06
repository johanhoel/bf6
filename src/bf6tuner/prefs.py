"""Small persisted preferences.

Two things worth surviving a restart: paths the user corrected by hand, and
per-setting values they chose instead of the engine's. Everything else is
re-derived on every launch.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

PATHS_FILE = "paths.json"
OVERRIDES_FILE = "setting_overrides.json"


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.join(str(Path.home()), ".config")
    return Path(base) / "BF6Tuner"


def _read(filename: str) -> dict[str, Any]:
    try:
        data = json.loads((config_dir() / filename).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(filename: str, values: dict[str, Any]) -> None:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(json.dumps(values, indent=2, sort_keys=True), encoding="utf-8")


# -- paths -------------------------------------------------------------------

def load() -> dict[str, str]:
    return {str(k): str(v) for k, v in _read(PATHS_FILE).items() if isinstance(v, str)}


def save(values: dict[str, str]) -> None:
    _write(PATHS_FILE, values)


def set_override(role: str, value: str | None) -> dict[str, str]:
    values = load()
    if value is None:
        values.pop(role, None)
    else:
        values[role] = value
    save(values)
    return values


# -- per-setting overrides ---------------------------------------------------

def load_setting_overrides() -> dict[str, Any]:
    return _read(OVERRIDES_FILE)


def save_setting_overrides(values: dict[str, Any]) -> None:
    _write(OVERRIDES_FILE, values)


def set_setting_override(setting_id: str, value: Any | None) -> dict[str, Any]:
    values = load_setting_overrides()
    if value is None:
        values.pop(setting_id, None)
    else:
        values[setting_id] = value
    save_setting_overrides(values)
    return values


def clear_setting_overrides() -> None:
    save_setting_overrides({})
