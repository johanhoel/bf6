"""A single-file diagnostics bundle for troubleshooting, separate from
writer.render_report()/render_json().

Those two are about the *recommendation* - which settings, why, what they
cost. This is about the *environment*: app version, which database entries
actually matched this hardware (or fell back to the heuristic), where
PresentMon and the game's files were found, and the handful of Windows
security features already confirmed by hand, in real sessions, to affect
this app (Smart App Control blocking self-update relaunches, Core
Isolation/Memory Integrity blocking PresentMon's ETW capture - see
ARCHITECTURE.md). Collecting all of that by hand, one PowerShell command at
a time, is exactly what slowed down diagnosing both of those - this exists
so the next one starts from one file instead.

Contains local file paths (install folders, profile locations) - reasonable
to include since this is a single-user desktop tool's own troubleshooting
export, not customer data, but worth a glance before pasting it somewhere
public.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .engine import Recommendation
from .hardware import HardwareProfile
from .paths import GamePaths


def _core_isolation_state() -> str:
    """Memory Integrity / Core Isolation - confirmed (2026-09-16, a real
    live investigation) to block PresentMon's ETW capture regardless of
    admin rights. Registry-only check, read-only, Windows-only."""
    if sys.platform != "win32":
        return "not applicable (not Windows)"
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "Enabled")
        return "ON - can block PresentMon/other ETW-based capture tools regardless of admin rights" if value else "off"
    except OSError:
        return "could not be determined (registry key not present or not accessible)"


def _recent_code_integrity_blocks() -> str:
    """Whether Windows has logged a Smart App Control / Code Integrity
    block recently - a heuristic ("has this happened before"), not a live
    status query. Confirmed useful during the self-update SAC investigation
    and the PresentMon one (both entries in ARCHITECTURE.md)."""
    if sys.platform != "win32":
        return "not applicable (not Windows)"
    try:
        import subprocess
        result = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-Command",
                "(Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-CodeIntegrity/Operational'; "
                "Id=3077,3118} -MaxEvents 1 -ErrorAction SilentlyContinue).TimeCreated",
            ],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        stamp = result.stdout.strip()
        return f"most recent block logged at {stamp}" if stamp else "none found in the event log"
    except Exception:  # never let a diagnostics export itself crash
        return "could not be checked (event log unavailable)"


def _db_match_line(entry: dict[str, Any] | None, kind: str) -> str:
    if not entry:
        return f"{kind}: no recommendation computed yet"
    if entry.get("matched"):
        return f"{kind}: matched database entry '{entry.get('id')}'"
    return f"{kind}: not found in database - using the fallback heuristic"


def build_report(
    profile: HardwareProfile,
    rec: Recommendation | None,
    game: GamePaths,
    presentmon_path: Path | None,
    setting_override_count: int,
    cfg_override_count: int,
    local_commit: str,
) -> str:
    """Pure (aside from the two Windows-security reads above, which are
    themselves read-only and side-effect-free) - takes exactly what a
    caller already has in hand, no new hardware detection performed here."""
    lines: list[str] = []
    add = lines.append

    add(f"BF6 Tuner diagnostics - {datetime.now().astimezone().isoformat(timespec='seconds')}")
    add(f"Version {__version__} (commit {local_commit or 'unknown'})")
    add("")

    add("-- Hardware --")
    add(f"CPU: {profile.cpu_name} ({profile.cores}c/{profile.threads}t"
        f"{', hybrid' if profile.hybrid else ''})")
    add(f"GPU: {profile.gpu_name} ({profile.vram_gb:g} GB VRAM, driver {profile.driver_version or 'unknown'})")
    if len(profile.all_gpus) > 1:
        add(f"  Other adapters seen: {', '.join(g for g in profile.all_gpus if g != profile.gpu_name)}")
    add(f"RAM: {profile.ram_gb:g} GB @ {profile.ram_speed_mts} MT/s, {len(profile.ram_sticks)} module(s)")
    add(f"Display: {profile.resolution} @ {profile.refresh_hz} Hz"
        f"{' (HDR)' if profile.hdr_display else ''}")
    add(f"OS: {profile.os_name} (build {profile.os_build})")
    if profile.detection_notes:
        add("Detection notes:")
        for note in profile.detection_notes:
            add(f"  - {note}")
    add("")

    add("-- Database matching --")
    add(_db_match_line(rec.gpu if rec else None, "GPU"))
    add(_db_match_line(rec.cpu if rec else None, "CPU"))
    add("")

    if rec is not None:
        add("-- Current recommendation --")
        add(f"Preset: {rec.target.preset}")
        add(f"Predicted: {rec.predicted_fps} FPS "
            f"(GPU limit {rec.gpu_fps}, CPU limit {rec.cpu_fps}, bottleneck: {rec.bottleneck})")
        add(f"Upscaler: {rec.upscaler_mode} ({rec.upscaler_tech})")
        add(f"Setting overrides: {setting_override_count}, User.cfg overrides: {cfg_override_count}")
        add("")

    add("-- Files --")
    add(f"Game install: {game.install_dir or 'not found'}")
    add(f"User.cfg: {game.user_cfg or 'not found'}")
    add(f"PROFSAVE_profile: {game.profsave or 'not found'}")
    add(f"PresentMon: {presentmon_path or 'not located'}")
    add("")

    add("-- Windows security features known to affect this app --")
    add(f"Core Isolation / Memory Integrity: {_core_isolation_state()}")
    add(f"Code Integrity blocks (Smart App Control or similar) in the event log: {_recent_code_integrity_blocks()}")
    add("")
    add("(Local file paths above - review before sharing this outside your own machine.)")

    return "\n".join(lines)
