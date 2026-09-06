"""Small persisted preferences.

Only paths the user has corrected by hand. Detection is re-run every launch, but
a path someone picked themselves should never have to be picked twice.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

FILENAME = "paths.json"


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.join(str(Path.home()), ".config")
    return Path(base) / "BF6Tuner"


def _path() -> Path:
    return config_dir() / FILENAME


def load() -> dict[str, str]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}


def save(values: dict[str, str]) -> None:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(values, indent=2), encoding="utf-8")


def set_override(role: str, value: str | None) -> dict[str, str]:
    values = load()
    if value is None:
        values.pop(role, None)
    else:
        values[role] = value
    save(values)
    return values
