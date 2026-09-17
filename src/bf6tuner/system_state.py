"""Read-only checks of the Windows-level settings `system_tweaks.json` used to
recommend unconditionally, regardless of whether they were already correct
(see ARCHITECTURE.md's work log). Same discipline as diagnostics.py's own
registry/event-log reads: never raise, degrade a check that can't be
answered into "unknown" (``None``) rather than a guess, and never write
anything - this app decides what to tell you, not what to change for you.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

_POWER_PLAN_NAMES = {
    "381b4222-f694-41f0-9685-ff5bb260df2e": "Balanced",
    "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c": "High performance",
    "a1841308-3541-4fab-bc81-f71556f20b4a": "Power saver",
    "e9a42b02-d5df-448d-aa00-03f14749eb61": "Ultimate Performance",
}
_HIGH_PERF_GUIDS = {"8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c", "e9a42b02-d5df-448d-aa00-03f14749eb61"}

# Processes that hook the presentation path - the specific concern in
# system_tweaks.json's "overlays" entry. Steam itself is deliberately not
# included: it is a required, always-running process for a Steam install,
# and its own overlay's on/off state is not reliably readable from here, so
# flagging "Steam is running" would just be noise.
_OVERLAY_PROCESSES = {
    "discord.exe": "Discord",
    "discordcanary.exe": "Discord Canary",
    "discordptb.exe": "Discord PTB",
    "rtss.exe": "RTSS (MSI Afterburner's overlay)",
    "nvidia share.exe": "NVIDIA overlay (GeForce Experience/NVIDIA App)",
    "overwolf.exe": "Overwolf",
}


@dataclass
class WindowsState:
    """Every field is ``None`` (sliders/enums: absent from the dict) when the
    check could not be answered - engine._evaluate_tweaks treats that the
    same as "don't know", not "assume the worst"."""

    hags_enabled: bool | None = None
    game_mode_enabled: bool | None = None
    power_plan_name: str | None = None
    power_plan_high_performance: bool | None = None
    fullscreen_opts_disabled_for_game: bool | None = None
    overlay_processes: list[str] = field(default_factory=list)


def _read_dword(hive, path: str, name: str) -> int | None:
    try:
        import winreg
        with winreg.OpenKey(hive, path) as key:
            value, _kind = winreg.QueryValueEx(key, name)
        return int(value)
    except OSError:
        return None


def _hags_enabled() -> bool | None:
    import winreg
    value = _read_dword(
        winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode",
    )
    # Unlike Game Mode below, an absent key here is left as "unknown" rather
    # than assumed off - whether the value gets pre-populated varies by
    # Windows build/driver, so a missing key isn't a reliable "never turned
    # on" signal the way it is for Game Mode.
    return None if value is None else value == 2


def _game_mode_enabled() -> bool | None:
    import winreg
    value = _read_dword(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\GameBar", "AutoGameModeEnabled")
    if value is None:
        # Absent key means the user has never touched this - Windows' own
        # default, since Windows 10 1809, is on.
        return True
    return value != 0


def _power_plan() -> tuple[str | None, bool | None]:
    try:
        result = subprocess.run(
            ["powercfg", "/getactivescheme"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        return None, None
    lowered = (result.stdout or "").lower()
    for guid, name in _POWER_PLAN_NAMES.items():
        if guid in lowered:
            return name, guid in _HIGH_PERF_GUIDS
    return None, None


def _fullscreen_opts_disabled(executable: Path | None) -> bool | None:
    if not executable:
        return None
    import winreg
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers",
        ) as key:
            value, _kind = winreg.QueryValueEx(key, str(executable))
    except OSError:
        # Key or this exe's entry absent - no compatibility flag has been
        # set for it, which means the setting has not been disabled.
        return False
    return "DISABLEDXMAXIMIZEDWINDOWEDMODE" in str(value).upper()


def _running_overlay_processes() -> list[str]:
    try:
        result = subprocess.run(
            ["tasklist.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        return []
    lowered = (result.stdout or "").lower()
    found: list[str] = []
    for exe, label in _OVERLAY_PROCESSES.items():
        if exe in lowered and label not in found:
            found.append(label)
    return found


def detect(executable: Path | None) -> WindowsState:
    """One-shot, best-effort - called once per hardware (re)detect, not on
    every settings change (see app.py's refresh()/redetect() split): a
    registry read is cheap, but the two subprocess calls here are not cheap
    enough to run on every keystroke."""
    state = WindowsState()
    if not IS_WINDOWS:
        return state
    try:
        state.hags_enabled = _hags_enabled()
    except Exception:
        pass
    try:
        state.game_mode_enabled = _game_mode_enabled()
    except Exception:
        pass
    try:
        state.power_plan_name, state.power_plan_high_performance = _power_plan()
    except Exception:
        pass
    try:
        state.fullscreen_opts_disabled_for_game = _fullscreen_opts_disabled(executable)
    except Exception:
        pass
    try:
        state.overlay_processes = _running_overlay_processes()
    except Exception:
        pass
    return state


# --------------------------------------------------------------------------
# Network - user-triggered, not part of detect() above (see app.py's
# System checks tab: a ping takes a second or two, too slow to run on every
# refresh() the way the checks above do).
# --------------------------------------------------------------------------

SPEED_TEST_URL = "https://fast.com"


@dataclass
class PingResult:
    host: str
    avg_ms: float | None = None
    loss_pct: float | None = None
    error: str | None = None


def ping(host: str = "8.8.8.8", count: int = 4) -> PingResult:
    """A general read on whether the connection is responsive and stable -
    deliberately not presented as in-game latency, which depends on which
    Battlefield 6 server a match actually puts you on and this app has no
    way to know. Never raises."""
    try:
        result = subprocess.run(
            ["ping", "-n", str(count), host],
            capture_output=True, text=True, timeout=count * 3 + 5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as exc:
        return PingResult(host=host, error=str(exc))

    text = result.stdout or ""
    avg_match = re.search(r"Average = (\d+)ms", text)
    loss_match = re.search(r"\((\d+)% loss\)", text)
    if avg_match is None and loss_match is None:
        return PingResult(host=host, error="No reply - check your connection.")
    return PingResult(
        host=host,
        avg_ms=float(avg_match.group(1)) if avg_match else None,
        loss_pct=float(loss_match.group(1)) if loss_match else None,
    )
