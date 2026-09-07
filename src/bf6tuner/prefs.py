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
CFG_OVERRIDES_FILE = "cfg_overrides.json"
PROFILES_FILE = "profiles.json"


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


# -- per-line User.cfg overrides ----------------------------------------------

def load_cfg_overrides() -> dict[str, Any]:
    return _read(CFG_OVERRIDES_FILE)


def save_cfg_overrides(values: dict[str, Any]) -> None:
    _write(CFG_OVERRIDES_FILE, values)


def set_cfg_override(key: str, value: Any | None) -> dict[str, Any]:
    values = load_cfg_overrides()
    if value is None:
        values.pop(key, None)
    else:
        values[key] = value
    save_cfg_overrides(values)
    return values


def clear_cfg_overrides() -> None:
    save_cfg_overrides({})


# -- named profiles -----------------------------------------------------------
# A profile is a saved snapshot of everything the sidebar + tables represent
# at once (preset, resolution/refresh/toggles, and every override) under a
# name, so switching between e.g. "Tournament" and "Chill" is one click
# instead of re-entering a dozen values. The live state (setting/cfg
# overrides above) is the single source of truth for what's actually
# applied; a profile is just a bookmark you can save from it or load into it.

def load_profiles() -> dict[str, Any]:
    return _read(PROFILES_FILE)


def save_profiles(values: dict[str, Any]) -> None:
    _write(PROFILES_FILE, values)


def save_profile(name: str, data: dict[str, Any]) -> dict[str, Any]:
    values = load_profiles()
    values[name] = data
    save_profiles(values)
    return values


def delete_profile(name: str) -> dict[str, Any]:
    values = load_profiles()
    values.pop(name, None)
    save_profiles(values)
    return values


def rename_profile(old: str, new: str) -> dict[str, Any]:
    values = load_profiles()
    if old in values and old != new and new:
        values[new] = values.pop(old)
        save_profiles(values)
    return values
