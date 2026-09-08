"""Loads the settings database from plain JSON, bundled read-only alongside
the executable in a shipped build or read straight out of ``data/`` in a
source checkout.

The database used to ship AES-256-GCM encrypted (see git history / ARCHITECTURE.md
if you're wondering why references to that still show up in old commits). That
bought a tamper-evidence check and made the files annoying to hand-edit, at the
cost of a fresh random key baked into the binary on every single build — which
meant every build, even with zero code changes, was a byte-different,
never-before-seen file to Windows SmartScreen/Smart App Control. The data here
is public BF6 hardware/settings reference info, not a secret, so that trade
wasn't worth it. See README "Getting the executable" for what actually helps
with SmartScreen (it isn't this).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATASETS = ("gpu_db", "cpu_db", "cfg_commands", "ingame_settings", "system_tweaks")


class DatabaseError(RuntimeError):
    """Raised when the settings database is missing or malformed."""


def _resource_root() -> Path:
    """Where bundled read-only data lives, frozen or not."""
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen)
    return Path(__file__).resolve().parent.parent.parent


@dataclass
class Database:
    gpu_db: dict[str, Any] = field(default_factory=dict)
    cpu_db: dict[str, Any] = field(default_factory=dict)
    cfg_commands: dict[str, Any] = field(default_factory=dict)
    ingame_settings: dict[str, Any] = field(default_factory=dict)
    system_tweaks: dict[str, Any] = field(default_factory=dict)
    source: str = "unknown"
    version: str = "unknown"

    # -- lookups ---------------------------------------------------------
    @property
    def gpus(self) -> list[dict[str, Any]]:
        return self.gpu_db.get("gpus", [])

    @property
    def cpus(self) -> list[dict[str, Any]]:
        return self.cpu_db.get("cpus", [])

    @property
    def commands(self) -> list[dict[str, Any]]:
        return self.cfg_commands.get("commands", [])

    @property
    def settings(self) -> list[dict[str, Any]]:
        return self.ingame_settings.get("settings", [])

    @property
    def tweaks(self) -> list[dict[str, Any]]:
        return self.system_tweaks.get("tweaks", [])

    def command(self, key: str) -> dict[str, Any] | None:
        return next((c for c in self.commands if c["key"].lower() == key.lower()), None)

    def setting(self, setting_id: str) -> dict[str, Any] | None:
        return next((s for s in self.settings if s["id"] == setting_id), None)


def _load_plain(data_dir: Path) -> Database:
    payload = {}
    for name in DATASETS:
        path = data_dir / f"{name}.json"
        if not path.is_file():
            raise DatabaseError(f"Missing database file: {path}")
        payload[name] = json.loads(path.read_text(encoding="utf-8"))
    db = Database(**payload)
    db.source = f"JSON ({data_dir})"
    db.version = payload["gpu_db"].get("version", "unknown")
    return db


def load(explicit_dir: Path | None = None) -> Database:
    """Load the database from plain JSON.

    Looks under the frozen resource root first (a shipped build's bundled
    ``data/`` folder), then the repo's top-level ``data/`` (a source checkout).
    """
    if explicit_dir is not None:
        return _load_plain(explicit_dir)

    root = _resource_root()
    for candidate in (root / "data", Path(__file__).resolve().parents[2] / "data"):
        if candidate.is_dir():
            return _load_plain(candidate)

    raise DatabaseError(f"No settings database found. Looked for data/ under {root} and its source checkout.")
