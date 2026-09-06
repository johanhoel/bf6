"""Rendering and writing the output files.

Every write in here is backed up first, into a per-application folder rather
than next to the original, so a restore is always possible even if the game
rewrites its own file afterwards.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from . import APP_NAME, __version__
from .engine import Recommendation

if TYPE_CHECKING:  # compare imports this module, so keep the runtime edge one-way
    from .compare import Comparison

MARKER_BEGIN = "# ---- BF6 Tuner: generated block, safe to delete ----"
MARKER_END = "# ---- end BF6 Tuner block ----"


def backup_root() -> Path:
    base = os.environ.get("APPDATA") or os.path.join(str(Path.home()), ".config")
    return Path(base) / "BF6Tuner" / "backups"


@dataclass
class WriteResult:
    path: Path
    backup: Path | None
    created: bool
    message: str


@dataclass
class RestorePoint:
    """A snapshot of every config file the app touches, taken together.

    Both files are captured in one directory so undoing is a single action
    rather than two separate file restores that can be half-applied. Files that
    did not exist at the time are recorded too, so restoring can delete a file
    the app created rather than leaving it behind.
    """

    directory: Path
    created: _dt.datetime
    label: str
    files: list[dict[str, object]] = field(default_factory=list)
    app_version: str = ""

    @property
    def stamp(self) -> str:
        return self.created.strftime("%Y-%m-%d %H:%M:%S")

    @property
    def roles(self) -> list[str]:
        return [str(entry["role"]) for entry in self.files]

    def entry(self, role: str) -> dict[str, object] | None:
        return next((e for e in self.files if e["role"] == role), None)

    def describe(self) -> str:
        parts = []
        for entry in self.files:
            name = Path(str(entry["original"])).name
            if entry.get("existed"):
                parts.append(f"{name} ({int(entry.get('size', 0)):,} bytes)")
            else:
                parts.append(f"{name} (did not exist)")
        return ", ".join(parts) if parts else "empty"


ROLE_LABELS = {"user_cfg": "User.cfg", "profsave": "In-game settings (PROFSAVE_profile)"}


def _timestamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _safe_label(label: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 _-]+", "", label).strip().replace(" ", "-")
    return cleaned[:48] or "snapshot"


def create_restore_point(targets: dict[str, Path | None], label: str) -> RestorePoint | None:
    """Snapshot every known config file before anything is written.

    Returns None only if there is nothing at all to record.
    """
    targets = {role: path for role, path in targets.items() if path is not None}
    if not targets:
        return None

    created = _dt.datetime.now()
    directory = backup_root() / f"{_timestamp()}_{_safe_label(label)}"
    directory.mkdir(parents=True, exist_ok=True)

    files: list[dict[str, object]] = []
    for role, path in targets.items():
        entry: dict[str, object] = {
            "role": role, "original": str(path), "existed": path.is_file(),
        }
        if path.is_file():
            stored = directory / path.name
            shutil.copy2(path, stored)
            entry["stored"] = stored.name
            entry["size"] = stored.stat().st_size
        files.append(entry)

    point = RestorePoint(
        directory=directory, created=created, label=label,
        files=files, app_version=__version__,
    )
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "created": created.isoformat(timespec="seconds"),
                "label": label, "app_version": __version__, "files": files,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return point


def list_restore_points() -> list[RestorePoint]:
    """Every restore point, newest first. Unreadable ones are skipped, not fatal."""
    root = backup_root()
    if not root.is_dir():
        return []
    points: list[RestorePoint] = []
    for directory in root.iterdir():
        manifest = directory / "manifest.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            points.append(RestorePoint(
                directory=directory,
                created=_dt.datetime.fromisoformat(str(data["created"])),
                label=str(data.get("label", "")),
                files=list(data.get("files", [])),
                app_version=str(data.get("app_version", "")),
            ))
        except (OSError, ValueError, KeyError):
            continue
    return sorted(points, key=lambda p: p.created, reverse=True)


def restore_from(point: RestorePoint, roles: list[str] | None = None) -> list[WriteResult]:
    """Put the files in ``point`` back where they came from.

    A file that did not exist when the snapshot was taken is deleted, so this is
    a true undo rather than a partial one.
    """
    results: list[WriteResult] = []
    for entry in point.files:
        role = str(entry["role"])
        if roles is not None and role not in roles:
            continue
        original = Path(str(entry["original"]))
        label = ROLE_LABELS.get(role, role)

        if not entry.get("existed"):
            if original.is_file():
                original.unlink()
                results.append(WriteResult(original, None, False,
                                           f"Removed {label} - it did not exist at this restore point."))
            else:
                results.append(WriteResult(original, None, False,
                                           f"{label} already absent, nothing to undo."))
            continue

        stored = point.directory / str(entry.get("stored", original.name))
        if not stored.is_file():
            results.append(WriteResult(original, None, False,
                                       f"{label} is missing from the backup folder; skipped."))
            continue
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(stored, original)
        results.append(WriteResult(original, stored, False, f"Restored {label} to {original}"))
    return results


def delete_restore_point(point: RestorePoint) -> None:
    shutil.rmtree(point.directory, ignore_errors=True)


def prune_restore_points(keep: int = 40) -> int:
    points = list_restore_points()
    removed = 0
    for point in points[keep:]:
        delete_restore_point(point)
        removed += 1
    return removed


def backup_file(path: Path, tag: str) -> Path | None:
    """Single-file backup, used when a write happens outside a restore point."""
    if not path.is_file():
        return None
    root = backup_root() / "loose" / tag
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"{path.name}.{_timestamp()}.bak"
    shutil.copy2(path, destination)
    return destination


# --------------------------------------------------------------------------
# User.cfg
# --------------------------------------------------------------------------

def render_user_cfg(rec: Recommendation, include_comments: bool = True) -> str:
    out: list[str] = []
    if include_comments:
        out += [
            MARKER_BEGIN,
            f"# {APP_NAME} {__version__} - generated {_dt.datetime.now():%Y-%m-%d %H:%M}",
            f"# Machine: {rec.profile.cpu_name} / {rec.profile.gpu_name}",
            f"# Target:  {rec.target.preset} @ {rec.target.width}x{rec.target.height} {rec.target.refresh_hz}Hz",
            f"# Estimate: {rec.predicted_fps} FPS ({rec.bottleneck}-limited: GPU {rec.gpu_fps} / CPU {rec.cpu_fps})",
            "#",
            "# Lines are 'Key Value'. Delete a line to return that setting to the game's default.",
            "# This file must sit next to the game executable, not in Documents.",
            "",
        ]

    for line in rec.cfg:
        if line.key is None:
            if include_comments:
                out += ["", f"# --- {line.comment} ---"] if line.header else [f"# {line.comment}"]
            continue
        if include_comments and line.comment:
            tag = ""
            if line.confidence == "legacy":
                tag = " [legacy Frostbite key - may be a no-op in BF6]"
            elif line.risk == "high":
                tag = " [measure this one]"
            out.append(f"# {line.comment}{tag}")
        out.append(f"{line.key} {line.value}")

    if include_comments:
        out += ["", MARKER_END]
    return "\r\n".join(out) + "\r\n"


def write_user_cfg(
    rec: Recommendation, path: Path, include_comments: bool = True,
    restore_point: RestorePoint | None = None,
) -> WriteResult:
    path = Path(path)
    if path.suffix.lower() != ".cfg":
        raise ValueError(
            f"Refusing to write {path.name}: the file must be named User.cfg. "
            "Windows hides known extensions, so a file called 'User.cfg.txt' looks correct "
            "in Explorer and does nothing in game."
        )
    existed = path.is_file()
    backup = restore_point.directory if restore_point else backup_file(path, "user_cfg")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_user_cfg(rec, include_comments), encoding="utf-8", newline="")
    return WriteResult(
        path=path, backup=backup, created=not existed,
        message=("Created " if not existed else "Updated ") + str(path),
    )


# --------------------------------------------------------------------------
# PROFSAVE_profile (the in-game settings the menu writes)
# --------------------------------------------------------------------------

_PROFSAVE_LINE = re.compile(r"^(?P<key>[A-Za-z][\w.]*)\s+(?P<value>.*)$")


def parse_profsave(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        match = _PROFSAVE_LINE.match(raw.strip())
        if match:
            values[match.group("key")] = match.group("value").strip()
    return values


def profsave_plan(rec: Recommendation, existing: dict[str, str]) -> list[tuple[str, str, str]]:
    """Return (key, old, new) for keys this app would change.

    Only keys already present in the user's file are considered. Inventing keys
    in a save file whose exact schema is not documented is how you get a profile
    the game refuses to load.
    """
    plan: list[tuple[str, str, str]] = []
    for choice in rec.settings:
        key = choice.profsave_key
        if not key or choice.personal or choice.value == "keep":
            continue
        if key not in existing:
            continue
        new = choice.value
        if isinstance(new, bool):
            new = int(new)
        if not isinstance(new, (int, float, str)):
            continue
        if isinstance(new, (int, float)) and choice.profsave_scale != 1.0:
            scaled = new * choice.profsave_scale
            new_text = f"{scaled:.6f}"
        else:
            new_text = str(new)
        if not _same_value(existing[key], new_text):
            plan.append((key, existing[key], new_text))
    return plan


def _same_value(old: str, new: str) -> bool:
    """Compare numerically where possible, so 0.500000 and 0.5 are not a change."""
    old, new = old.strip(), new.strip()
    if old == new:
        return True
    try:
        return abs(float(old) - float(new)) < 1e-6
    except ValueError:
        return False


def write_profsave(
    rec: Recommendation, path: Path, restore_point: RestorePoint | None = None
) -> WriteResult:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} does not exist. Launch Battlefield 6 once and save its video settings first."
        )
    original = path.read_text(encoding="utf-8", errors="ignore")
    existing = parse_profsave(original)
    plan = profsave_plan(rec, existing)
    if not plan:
        return WriteResult(path=path, backup=None, created=False,
                           message="No in-game settings needed changing.")

    changes = {key: new for key, _, new in plan}
    backup = restore_point.directory if restore_point else backup_file(path, "profsave")

    out: list[str] = []
    for raw in original.splitlines():
        match = _PROFSAVE_LINE.match(raw.strip())
        if match and match.group("key") in changes:
            out.append(f"{match.group('key')} {changes[match.group('key')]}")
        else:
            out.append(raw)
    path.write_text("\r\n".join(out) + "\r\n", encoding="utf-8", newline="")
    return WriteResult(
        path=path, backup=backup, created=False,
        message=f"Updated {len(plan)} setting(s) in {path.name}.",
    )


# --------------------------------------------------------------------------
# Current vs recommended
# --------------------------------------------------------------------------

def render_comparison(comparison: "Comparison") -> str:
    lines: list[str] = []
    add = lines.append

    add("CURRENT vs RECOMMENDED")
    add("=" * 78)
    add("")

    if not comparison.available:
        for wrapped in _wrap(comparison.reason_unavailable, 76):
            add(f"  {wrapped}")
        add("")
    else:
        add(f"  Now       ~{comparison.current_predicted} FPS "
            f"({comparison.current_bottleneck}-limited: GPU {comparison.current_gpu_fps} / "
            f"CPU {comparison.current_cpu_fps})")
        add(f"  After     ~{comparison.new_predicted} FPS   "
            f"({comparison.fps_delta:+d})")
        if abs(comparison.vram_delta_gb) >= 0.15:
            add(f"  VRAM      {comparison.vram_delta_gb:+.1f} GB")
        add("")
        add("  Per-change FPS figures below are marginal: each is what that one change is")
        add("  worth on its own. They will not sum exactly to the total, and a change that")
        add("  shows 0 FPS is one the other side of the bottleneck is absorbing.")
        add("")

        if comparison.changes:
            add(f"  {len(comparison.changes)} SETTING(S) WOULD CHANGE")
            add("  " + "-" * 76)
            for change in comparison.changes:
                arrow = "raise" if change.direction == "raise" else "lower"
                add(f"  {change.label}")
                add(f"      {change.current_display}  ->  {change.new_display}   ({arrow})")
                add(f"      Impact: {change.impact_summary}")
                for pro in change.pros:
                    for index, wrapped in enumerate(_wrap(pro, 68)):
                        add(f"      {'  + ' if index == 0 else '    '}{wrapped}")
                for con in change.cons:
                    for index, wrapped in enumerate(_wrap(con, 68)):
                        add(f"      {'  - ' if index == 0 else '    '}{wrapped}")
                for wrapped in _wrap("Why: " + change.reason, 70):
                    add(f"        {wrapped}")
                add("")
        else:
            add("  No in-game setting would change.")
            add("")

        if comparison.unchanged:
            add(f"  ALREADY CORRECT ({len(comparison.unchanged)})")
            add("  " + "-" * 76)
            add("  " + ", ".join(c.label for c in comparison.unchanged))
            add("")
        if comparison.personal:
            add(f"  LEFT ALONE - YOURS TO SET ({len(comparison.personal)})")
            add("  " + "-" * 76)
            add("  " + ", ".join(f"{c.label} (now {c.current_display})" for c in comparison.personal))
            add("")
        if comparison.unknown:
            add(f"  NOT STORED IN THE PROFILE, SO NOT COMPARABLE ({len(comparison.unknown)})")
            add("  " + "-" * 76)
            add("  " + ", ".join(f"{c.label} -> set to {c.new_display}" for c in comparison.unknown))
            add("")

    active = [c for c in comparison.cfg_changes if c.action != "same"]
    add(f"  USER.CFG - {len(active)} LINE(S) WOULD CHANGE")
    add("  " + "-" * 76)
    if not active:
        add("  Your User.cfg already matches.")
    for change in active:
        if change.action == "add":
            add(f"  + {change.key} {change.new}")
        elif change.action == "change":
            add(f"  ~ {change.key}: {change.current}  ->  {change.new}")
        else:
            add(f"  - {change.key} {change.current}   (removed - saving rewrites the whole file)")
        if change.summary:
            for wrapped in _wrap(change.summary, 70):
                add(f"        {wrapped}")
        for pro in change.pros:
            for index, wrapped in enumerate(_wrap(pro, 68)):
                add(f"        {'+ ' if index == 0 else '  '}{wrapped}")
        for con in change.cons:
            for index, wrapped in enumerate(_wrap(con, 68)):
                add(f"        {'- ' if index == 0 else '  '}{wrapped}")
        if change.risk in ("high", "unsafe") or change.confidence == "legacy":
            add(f"        [{change.confidence or 'unknown'} / risk: {change.risk or 'unknown'}]")
        add("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Human-readable report
# --------------------------------------------------------------------------

def render_report(rec: Recommendation, comparison: "Comparison | None" = None) -> str:
    p = rec.profile
    lines: list[str] = []
    add = lines.append

    add(f"{APP_NAME} {__version__} - Battlefield 6 configuration report")
    add(f"Generated {_dt.datetime.now():%Y-%m-%d %H:%M}")
    add("=" * 78)
    add("")
    add("DETECTED HARDWARE")
    add("-" * 78)
    topology = f"{p.cores} cores / {p.threads} threads"
    if p.hybrid:
        topology += f"  ({p.p_cores} performance + {p.e_cores} efficiency)"
    add(f"  CPU       {p.cpu_name}")
    add(f"            {topology}"
        + (f" @ up to {p.max_clock_ghz} GHz" if p.max_clock_ghz else ""))
    add(f"  GPU       {p.gpu_name}")
    add(f"            {p.vram_gb:g} GB VRAM"
        + (f", driver {p.driver_version}" if p.driver_version else ""))
    add(f"  Memory    {p.ram_gb:g} GB"
        + (f" @ {p.ram_speed_mts} MT/s" if p.ram_speed_mts else "")
        + (f" across {len(p.ram_sticks)} module(s)" if p.ram_sticks else ""))
    add(f"  Display   {p.width}x{p.height} @ {p.refresh_hz} Hz"
        + (f" (panel supports up to {p.max_refresh_hz} Hz)" if p.max_refresh_hz > p.refresh_hz else ""))
    if p.os_name:
        add(f"  OS        {p.os_name} (build {p.os_build})")
    if not p.detected:
        add("  NOTE      Hardware was not detected on this machine; a sample profile is in use.")
    for note in p.detection_notes:
        add(f"  NOTE      {note}")
    add("")

    add("PREDICTION")
    add("-" * 78)
    add(f"  Preset            {rec.target.preset}")
    add(f"  Target            {rec.target.width}x{rec.target.height} @ {rec.target.refresh_hz} Hz"
        + (", VRR on" if rec.target.vrr else ", no VRR"))
    add(f"  GPU-limited       ~{rec.gpu_fps} FPS   ({rec.gpu.get('name', '?')})")
    add(f"  CPU-limited       ~{rec.cpu_fps} FPS   ({rec.cpu.get('id', '?')})")
    add(f"  Expected in game  ~{rec.predicted_fps} FPS, {rec.bottleneck}-limited")
    add(f"  Frame cap set to  {rec.frame_cap} FPS")
    if rec.headroom_note:
        add(f"  {rec.headroom_note}")
    add("")
    add("  These are estimates from a model, not measurements from your machine.")
    add("  Turn the in-game FPS counter on and compare.")
    add("")

    if comparison is not None:
        for line in render_comparison(comparison).splitlines():
            add(line)
        add("")

    add("IN-GAME SETTINGS")
    add("-" * 78)
    grouped: dict[str, list] = {}
    for choice in rec.settings:
        grouped.setdefault(choice.menu, []).append(choice)
    for menu, choices in grouped.items():
        add(f"  [{menu}]")
        for choice in choices:
            value = "leave as-is" if choice.value == "keep" else choice.display
            add(f"    {choice.label:<42} {value}")
    add("")

    add("WHY")
    add("-" * 78)
    for choice in rec.settings:
        if choice.value == "keep":
            continue
        add(f"  {choice.label} -> {choice.display}")
        for wrapped in _wrap(choice.reason, 74):
            add(f"      {wrapped}")
        add("")

    if rec.warnings:
        add("WARNINGS AND DELIBERATE OMISSIONS")
        add("-" * 78)
        for warning in rec.warnings:
            add(f"  [{warning.severity.upper()}] {warning.title}")
            for wrapped in _wrap(warning.body, 74):
                add(f"      {wrapped}")
            add("")

    if rec.tweaks:
        add("SYSTEM CHECKS (not applied automatically)")
        add("-" * 78)
        for tweak in rec.tweaks:
            add(f"  [{tweak.get('severity', 'low').upper()}] {tweak['label']}")
            for wrapped in _wrap(tweak.get("why", ""), 74):
                add(f"      {wrapped}")
            for wrapped in _wrap("How: " + tweak.get("how", ""), 74):
                add(f"      {wrapped}")
            add("")

    add("USER.CFG")
    add("-" * 78)
    for line in render_user_cfg(rec).replace("\r\n", "\n").splitlines():
        add(f"  {line}")
    return "\n".join(lines) + "\n"


def render_json(rec: Recommendation, comparison: "Comparison | None" = None) -> str:
    payload: dict = {
        "app": {"name": APP_NAME, "version": __version__},
        "hardware": rec.profile.to_dict(),
        "matched": {"gpu": rec.gpu, "cpu": rec.cpu},
        "target": rec.target.__dict__,
        "prediction": {
            "gpu_fps": rec.gpu_fps, "cpu_fps": rec.cpu_fps,
            "predicted_fps": rec.predicted_fps, "bottleneck": rec.bottleneck,
            "frame_cap": rec.frame_cap, "upscaler": rec.upscaler_mode,
            "upscaler_tech": rec.upscaler_tech, "quality_step": rec.quality_step,
        },
        "settings": [c.__dict__ for c in rec.settings],
        "user_cfg": [
            {"key": line.key, "value": line.value, "comment": line.comment}
            for line in rec.cfg if line.key
        ],
        "warnings": [w.__dict__ for w in rec.warnings],
        "system_checks": rec.tweaks,
    }
    if comparison is not None:
        payload["comparison"] = {
            "available": comparison.available,
            "reason_unavailable": comparison.reason_unavailable,
            "current": {
                "gpu_fps": comparison.current_gpu_fps, "cpu_fps": comparison.current_cpu_fps,
                "predicted_fps": comparison.current_predicted,
                "bottleneck": comparison.current_bottleneck,
            },
            "fps_delta": comparison.fps_delta,
            "vram_delta_gb": comparison.vram_delta_gb,
            "changes": [c.__dict__ for c in comparison.changes],
            "unchanged": [c.label for c in comparison.unchanged],
            "not_comparable": [c.label for c in comparison.unknown],
            "left_alone": [c.label for c in comparison.personal],
            "user_cfg": [c.__dict__ for c in comparison.cfg_changes if c.action != "same"],
        }
    return json.dumps(payload, indent=2, default=str)


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(" ".join(str(text).split()), width=width) or [""]
