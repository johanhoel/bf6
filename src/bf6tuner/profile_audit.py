"""Spotting settings a game update added, from the game's own save file.

Battlefield seasons add, rename and retire settings, and patch notes are an
unreliable record of that. Season 5's notes (game update 2.1.1.0, released
2026-10-13) were checked when this module was written and listed no new
graphics or audio options at all - only controller-side changes, in input
settings this app has never been shown a real key for. Guessing a key from a
news article would break this project's standing rule against inventing
PROFSAVE keys (see ARCHITECTURE.md section 7).

The profile itself is the authoritative record. When an update adds a
setting, its key appears in PROFSAVE_profile the next time the game saves.
So rather than chase each season's notes, this reads the file and reports
two things:

``new_since_last_scan``
    Keys present now that were not present the last time this app looked at
    the same machine's profile. This is the precise signal - the file
    compared with itself - and the one that catches a season landing between
    two runs. It needs a previous scan, so it is empty on the first one.

``not_in_baseline``
    First scan only: keys absent from a real profile captured before Season 5
    (``data/profile_key_baseline.json``), limited to the key *families* that
    capture actually contains. That limit matters - the capture has no
    ``GstInput.*`` keys at all, so without it every mouse and controller key
    on a machine would be reported as "new", which would be false.

Neither list is a recommendation. A key showing up here means "the game
stores something this app does not model yet", which is exactly what should
be confirmed and added to ``ingame_settings.json`` - with a real value seen
in a real file, not one inferred from a name.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .database import Database

_DATA_FILE = "profile_key_baseline.json"

_LINE_RE = re.compile(r"^(?P<key>[A-Za-z][\w.\-]*)(?:\s+(?P<value>.*))?$")


def _resource_root() -> Path:
    """Mirrors database.py's frozen/source split - see that module."""
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen)
    return Path(__file__).resolve().parent.parent.parent


def load_baseline() -> dict[str, Any]:
    for candidate_dir in (_resource_root() / "data", Path(__file__).resolve().parents[2] / "data"):
        path = candidate_dir / _DATA_FILE
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"Could not find {_DATA_FILE} under {_resource_root()} or its source checkout")


@dataclass
class ProfileKey:
    key: str
    value: str
    family: str
    first_seen: str


@dataclass
class ProfileAudit:
    total_keys: int = 0
    modelled: int = 0
    ignored: int = 0
    unmodelled_by_family: dict[str, int] = field(default_factory=dict)
    new_since_last_scan: list[ProfileKey] = field(default_factory=list)
    not_in_baseline: list[ProfileKey] = field(default_factory=list)
    last_scan: str = ""
    baseline_captured: str = ""
    baseline_captured_before: str = ""
    first_scan: bool = True

    @property
    def new_keys(self) -> list[ProfileKey]:
        """Whichever signal applies to this scan - see the module docstring."""
        return self.new_since_last_scan if not self.first_scan else self.not_in_baseline

    @property
    def headline(self) -> str:
        count = len(self.new_keys)
        if not count:
            if self.first_scan:
                return ("No settings keys beyond a real profile captured "
                        f"{self.baseline_captured}. Later scans will flag any a game update adds.")
            return f"No new settings keys since the last scan on {self.last_scan}."
        noun = "key" if count == 1 else "keys"
        if self.first_scan:
            return (f"{count} settings {noun} this app has never seen - not in a real profile "
                    f"captured before {self.baseline_captured_before}, so likely added by an update.")
        return (f"{count} settings {noun} appeared since the last scan on {self.last_scan} - "
                "likely added by a game update.")


def parse_keys(profsave_text: str) -> dict[str, str]:
    keys: dict[str, str] = {}
    for line in profsave_text.splitlines():
        match = _LINE_RE.match(line.strip())
        if match:
            keys[match.group("key")] = (match.group("value") or "").strip()
    return keys


def _modelled_keys(db: Database) -> set[str]:
    return {s["profsave_key"] for s in db.settings if s.get("profsave_key")}


def audit(
    db: Database,
    profsave_text: str,
    history: dict[str, Any] | None = None,
    today: _dt.date | None = None,
    baseline: dict[str, Any] | None = None,
) -> tuple[ProfileAudit, dict[str, Any]]:
    """Audit a profile's keys. Returns ``(result, updated_history)``.

    Pure: nothing is read from or written to disk here, so the caller decides
    when the history is persisted. That matters in the GUI, which recomputes
    on every change - saving after each recompute would make a new key "seen"
    a moment after it was first reported, and the report would vanish.

    ``history`` is ``{"last_scan": iso_date, "keys": {key: first_seen_iso}}``.
    """
    today_iso = (today or _dt.date.today()).isoformat()
    baseline = baseline if baseline is not None else load_baseline()
    patterns = [re.compile(p) for p in baseline.get("ignore_patterns", {})]
    baseline_keys = set(baseline.get("keys", []))
    baseline_families = set(baseline.get("families", []))
    modelled = _modelled_keys(db)

    history = history or {}
    seen: dict[str, str] = dict(history.get("keys") or {})
    first_scan = not seen

    current = parse_keys(profsave_text)
    result = ProfileAudit(
        total_keys=len(current),
        last_scan=str(history.get("last_scan", "")),
        baseline_captured=str(baseline.get("captured", "")),
        baseline_captured_before=str(baseline.get("captured_before", "")),
        first_scan=first_scan,
    )

    for key, value in sorted(current.items()):
        if any(p.match(key) for p in patterns):
            result.ignored += 1
            continue
        family = key.split(".", 1)[0]
        if key in modelled:
            result.modelled += 1
        else:
            result.unmodelled_by_family[family] = result.unmodelled_by_family.get(family, 0) + 1

        if key not in seen:
            entry = ProfileKey(key=key, value=value, family=family, first_seen=today_iso)
            if not first_scan:
                result.new_since_last_scan.append(entry)
            elif family in baseline_families and key not in baseline_keys:
                result.not_in_baseline.append(entry)
            seen[key] = today_iso
        # A key seen before keeps its original first-seen date.

    updated = {"last_scan": today_iso, "keys": seen}
    return result, updated


def render(result: ProfileAudit) -> str:
    """Plain-text section for the report and the CLI."""
    lines = ["NEW SETTINGS KEYS (from your own profile)", "-" * 78, f"  {result.headline}"]
    for entry in result.new_keys:
        value = entry.value if entry.value else "(empty)"
        lines.append(f"    {entry.key} = {value}")
    if result.new_keys:
        lines.append("  These are what the game stores, not recommendations. The app does not")
        lines.append("  write them until each one is confirmed and modelled.")
    if result.unmodelled_by_family:
        parts = ", ".join(f"{fam} {n}" for fam, n in sorted(result.unmodelled_by_family.items()))
        lines.append(f"  Keys present but not modelled by this app: {parts}.")
    lines.append(f"  {result.total_keys} keys read, {result.modelled} modelled, "
                 f"{result.ignored} ignored (shader cache stamps, key bindings).")
    return "\n".join(lines) + "\n"
