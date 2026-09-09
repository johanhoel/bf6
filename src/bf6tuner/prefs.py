"""Small persisted preferences.

Three things worth surviving a restart: paths the user corrected by hand,
per-setting values they chose instead of the engine's, and the last-used
preset/target toggles. Everything else is re-derived on every launch.

That third one is the one exception worth explaining: resolution and
refresh rate are genuinely re-detected from the real display every launch
(see ``ui/app.py``'s ``_on_detected``), so those never need to be
remembered here. The preset and the Target checkboxes (VRR, HDR, "I
stream/record", frame generation, Thread.* overrides, legacy keys, the FPS
overlay) are different: there is no reliable signal to *detect* most of
them from the current config - Windows has no "is VRR enabled" query, and
the in-game FPS overlay isn't even a setting BF6 tracks in its save file -
so re-deriving them would mean guessing, which is exactly how the app used
to open on a hardcoded "Competitive + overlay on" every single time
regardless of what was actually set last. Remembering the last explicit
choice is the only honest way to make the window match reality on open.
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
TARGET_FILE = "target.json"
UPDATE_SKIP_FILE = "update_skip.json"


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


# -- last-used preset/target toggles ------------------------------------------
# See the module docstring for why this, alone among "derived" state, is
# persisted rather than re-derived. Written on every change (same as the
# per-setting overrides above) - it is a handful of scalars, not worth
# debouncing.

def load_target() -> dict[str, Any]:
    return _read(TARGET_FILE)


def save_target(values: dict[str, Any]) -> None:
    _write(TARGET_FILE, values)


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


# -- skipped update ------------------------------------------------------------
# "Skip this version" remembers the commit sha the user dismissed, in its own
# tiny file rather than folded into target.json - _save_target() there
# unconditionally overwrites its file with a fixed key set on every preset/
# checkbox change, which would silently wipe a skip recorded any other way.
# Updates here are tracked by commit sha, not a semantic version number (see
# update.py's module docstring), so "skip this version" means "don't nag
# again until main moves past this exact commit" - once a newer commit
# appears, the stored sha no longer matches and the banner returns.

def load_skipped_update_sha() -> str:
    return str(_read(UPDATE_SKIP_FILE).get("sha", ""))


def save_skipped_update_sha(sha: str) -> None:
    _write(UPDATE_SKIP_FILE, {"sha": sha})
