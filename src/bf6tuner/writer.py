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
from dataclasses import dataclass
from pathlib import Path

from . import APP_NAME, __version__
from .engine import Recommendation

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


def _timestamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def backup_file(path: Path, tag: str) -> Path | None:
    if not path.is_file():
        return None
    root = backup_root() / tag
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"{path.name}.{_timestamp()}.bak"
    shutil.copy2(path, destination)
    return destination


def list_backups(tag: str) -> list[Path]:
    root = backup_root() / tag
    if not root.is_dir():
        return []
    return sorted(root.glob("*.bak"), reverse=True)


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


def write_user_cfg(rec: Recommendation, path: Path, include_comments: bool = True) -> WriteResult:
    path = Path(path)
    if path.suffix.lower() != ".cfg":
        raise ValueError(
            f"Refusing to write {path.name}: the file must be named User.cfg. "
            "Windows hides known extensions, so a file called 'User.cfg.txt' looks correct "
            "in Explorer and does nothing in game."
        )
    existed = path.is_file()
    backup = backup_file(path, "user_cfg")
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
        new_text = str(new)
        if existing[key].strip() != new_text:
            plan.append((key, existing[key], new_text))
    return plan


def write_profsave(rec: Recommendation, path: Path) -> WriteResult:
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
    backup = backup_file(path, "profsave")

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


def restore(backup: Path, destination: Path) -> WriteResult:
    shutil.copy2(backup, destination)
    return WriteResult(path=destination, backup=backup, created=False,
                       message=f"Restored {destination.name} from {backup.name}.")


# --------------------------------------------------------------------------
# Human-readable report
# --------------------------------------------------------------------------

def render_report(rec: Recommendation) -> str:
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


def render_json(rec: Recommendation) -> str:
    return json.dumps(
        {
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
        },
        indent=2, default=str,
    )


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(" ".join(str(text).split()), width=width) or [""]
