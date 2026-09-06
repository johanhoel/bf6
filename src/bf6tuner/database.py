"""Loads the settings database, from the encrypted bundle in a build or from
the plain JSON sources during development."""

from __future__ import annotations

import base64
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import crypto

DATASETS = ("gpu_db", "cpu_db", "cfg_commands", "ingame_settings", "system_tweaks")
BUNDLE_NAME = "bf6tuner.db"


def _resource_root() -> Path:
    """Where bundled read-only data lives, frozen or not."""
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen)
    return Path(__file__).resolve().parent.parent.parent


def _bundle_key() -> bytes | None:
    """The build-time key, injected as a generated module. Absent in a source checkout."""
    try:
        from ._keyring import BUNDLE_KEY  # type: ignore[attr-defined]
    except ImportError:
        return None
    return base64.b64decode(BUNDLE_KEY)


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
            raise crypto.BundleError(f"Missing database file: {path}")
        payload[name] = json.loads(path.read_text(encoding="utf-8"))
    db = Database(**payload)
    db.source = f"plain JSON ({data_dir})"
    db.version = payload["gpu_db"].get("version", "unknown")
    return db


def load(explicit_path: Path | None = None) -> Database:
    """Load the database.

    Prefers the encrypted bundle so that a shipped build never falls back to
    editable JSON that happens to be lying around. Only a source checkout,
    which has no key, reads the plain files.
    """
    root = _resource_root()
    bundle_path = explicit_path or (root / BUNDLE_NAME)
    key = _bundle_key()

    if bundle_path.is_file() and key is not None:
        payload, header = crypto.unpack(bundle_path.read_bytes(), key)
        missing = [name for name in DATASETS if name not in payload]
        if missing:
            raise crypto.BundleError(f"Database bundle is incomplete: missing {', '.join(missing)}")
        db = Database(**{name: payload[name] for name in DATASETS})
        db.source = "encrypted bundle"
        db.version = header.get("version", "unknown")
        return db

    if bundle_path.is_file() and key is None:
        raise crypto.BundleError(
            "An encrypted database is present but this build has no key. "
            "Rebuild with packaging/build.py rather than mixing artefacts."
        )

    for candidate in (root / "data", Path(__file__).resolve().parents[2] / "data"):
        if candidate.is_dir():
            return _load_plain(candidate)

    raise crypto.BundleError(f"No settings database found. Looked for {bundle_path} and ./data.")
