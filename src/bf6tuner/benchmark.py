"""Closing the loop on the FPS prediction with a real measurement.

The README is explicit that the predicted FPS is a model, not a measurement,
and that the app turns the in-game overlay on by default "so you can check
its homework." This module is the other half of that: a real frame-time
capture via PresentMon (Intel/Microsoft's open-source, ETW-based capture
tool - the same engine behind NVIDIA FrameView and CapFrameX), so "check its
homework" can mean an actual measured 1%/0.1% low, not just eyeballing an
overlay number.

PresentMon itself is never bundled or auto-downloaded - same policy as
everywhere else this app fetches something external (see update.py). You
point the app at your own copy once, the path is remembered via prefs.py's
existing generic path-override mechanism (role "presentmon_exe"), same as
the game install folder or PROFSAVE_profile.

Current Intel PresentMon (2.x) ships three pieces - a background service, a
GUI, and a standalone console application (see
github.com/GameTechDev/PresentMon, README-ConsoleApplication.md). This module
targets the console application specifically (a single self-contained .exe,
e.g. `PresentMon-2.3.1-x64.exe`, no service required) with its documented
double-dash flags (`--process_name`, `--output_file`, `--timed`,
`--stop_existing_session`, `--no_console_stats`) - not the older 1.x
single-dash flags this file used before those were checked against the
actual current docs. If a given build still rejects them (flags do drift
between releases), the failure surfaces as a clear error (PresentMon's own
stderr) rather than a silent bad capture - see `run_capture`. CSV column
names are matched flexibly for the same reason (`_find_column`) -
`MsBetweenPresents` is confirmed present in the 2.x console app's output.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Frame-time column, in priority order - PresentMon has used "MsBetweenPresents"
# consistently across both major CLI generations; the others are fallbacks
# for older/renamed builds. Matched case-insensitively against the CSV header.
FRAME_TIME_COLUMNS = ("msbetweenpresents", "msbetweendisplaychange", "frametime", "frame_time_ms")

PRESENTMON_CANDIDATE_NAMES = ("presentmon.exe", "presentmon64.exe")

# CreateProcess's ERROR_ELEVATION_REQUIRED. Seen in practice (2026-09-07) when
# pointed at the installed Intel PresentMon *application* (its exe is
# manifested requireAdministrator, since it manages the background service)
# instead of the standalone console tool, which isn't. See run_capture().
_ERROR_ELEVATION_REQUIRED = 740

# {process}/{pid}, {output}, {duration} are substituted by build_args().
# Editable from the UI's Advanced field if a given PresentMon build wants
# different flags - not hardcoded past this one place. Flags verified
# against `PresentMon-2.5.1-x64.exe --help`'s actual current output (not
# just README-ConsoleApplication.md, which drifts): --process_name/
# --process_id, --output_file and --timed are the documented capture/
# duration flags; --stop_existing_session clears a stale trace under the
# same name rather than erroring; --no_console_stats suppresses the live
# per-frame console output, which this app doesn't read anyway since it
# captures via subprocess.run().
#
# --terminate_after_timed (2026-09-09): confirmed live, reproducibly, that
# --timed alone only stops *recording* - the process itself keeps running
# afterwards (waiting on a hotkey that was never configured here), so
# `run_capture`'s subprocess.run() would sit until its own timeout fired.
# Without this flag every capture eventually looked like a hang/timeout
# regardless of anything else being right, and killing the stuck process by
# hand leaves its ETW trace session dangling for the *next* attempt to trip
# over ("a trace session named 'PresentMon' is already running" - also
# reproduced live). This flag makes PresentMon exit cleanly on its own the
# moment the timed capture finishes, which is what --stop_existing_session
# was already assuming would happen for the *next* run.
#
# PID targeting is preferred when a PID is known (see run_capture's `pid`
# param): PresentMon's own runtime warning says resolving a process by
# *name* needs elevation for short-lived processes or ones started under
# another account, which a known PID sidesteps - a real BF6 process is
# neither of those, so this may turn out to have been a red herring that
# --terminate_after_timed's absence made look like an elevation problem;
# kept as a fallback rather than removed, since it was added in response to
# a real observed error and hasn't been *disproven*, only made less certain.
DEFAULT_ARGS_TEMPLATE = (
    "--process_name {process} --output_file {output} --timed {duration} "
    "--terminate_after_timed --stop_existing_session --no_console_stats"
)
DEFAULT_PID_ARGS_TEMPLATE = (
    "--process_id {pid} --output_file {output} --timed {duration} "
    "--terminate_after_timed --stop_existing_session --no_console_stats"
)


class BenchmarkError(RuntimeError):
    pass


def find_presentmon(explicit: Path | None = None) -> Path | None:
    """Best-effort search; a manual 'Locate PresentMon...' pick always wins.

    Checked in order: the explicit override, PATH, and the folder NVIDIA
    FrameView installs it into (the most common way people already have a
    copy without knowing it).
    """
    if explicit and explicit.is_file():
        return explicit

    import shutil as _shutil
    for name in PRESENTMON_CANDIDATE_NAMES:
        found = _shutil.which(name)
        if found:
            return Path(found)

    if sys.platform == "win32":
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        candidate = Path(program_files) / "NVIDIA Corporation" / "FrameView" / "PresentMon.exe"
        if candidate.is_file():
            return candidate

    return None


def build_args(exe: Path, process_name: str, output_csv: Path, duration_s: int,
                template: str | None = None, pid: int | None = None) -> list[str]:
    if template is None:
        template = DEFAULT_PID_ARGS_TEMPLATE if pid else DEFAULT_ARGS_TEMPLATE
    rendered = template.format(
        process=process_name, pid=pid or "", output=str(output_csv), duration=duration_s,
    )
    return [str(exe), *rendered.split()]


def run_capture(exe: Path, process_name: str, output_csv: Path, duration_s: int,
                 template: str | None = None, timeout_s: float | None = None,
                 pid: int | None = None) -> None:
    """Blocking - run this off the UI thread. Raises BenchmarkError with
    PresentMon's own stderr on a non-zero exit rather than guessing why.

    Prefers targeting by `pid` when the caller has one (see the note above
    DEFAULT_PID_ARGS_TEMPLATE) - `process_name` is still required as the
    fallback template's substitution and for error messages either way.
    """
    args = build_args(exe, process_name, output_csv, duration_s, template, pid=pid)
    try:
        result = subprocess.run(
            args, capture_output=True, text=True,
            timeout=timeout_s or (duration_s + 30),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired as exc:
        raise BenchmarkError(
            f"PresentMon did not finish within {exc.timeout:.0f}s. It may need different "
            "flags for this version - check the Advanced command template."
        ) from exc
    except OSError as exc:
        if getattr(exc, "winerror", None) == _ERROR_ELEVATION_REQUIRED:
            raise BenchmarkError(
                f"{exe} requires administrator privileges to run (Windows error 740). "
                "This usually means you've pointed BF6 Tuner at the installed Intel "
                "PresentMon application (under Program Files) rather than the standalone "
                "console tool. Download the single PresentMon-<version>-x64.exe from "
                "github.com/GameTechDev/PresentMon/releases instead - that one does not "
                "need elevation - and Locate that file instead."
            ) from exc
        raise BenchmarkError(f"Could not run PresentMon at {exe}: {exc}") from exc

    target_desc = f"PID {pid}" if pid else f"process name '{process_name}' (no PID was found)"

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "no output").strip()[-1500:]
        if "access denied" in detail.lower() or "elevat" in detail.lower():
            if pid:
                # Targeted by PID and *still* elevation-denied: the "needs
                # elevation to resolve a name" theory is wrong, or incomplete -
                # say so plainly rather than repeat the by-name explanation.
                raise BenchmarkError(
                    f"PresentMon still needs administrator rights even targeting {target_desc} "
                    "directly, not by name - so this isn't just a name-resolution requirement. "
                    "Close BF6 Tuner and relaunch it as Administrator, then try again. If that "
                    "still fails even fully elevated, elevation may not be the real cause - two "
                    "things confirmed to cause exactly this: (1) a full PresentMon app install "
                    "(not just the standalone console tool) can leave its own background service "
                    "holding the trace session even after you stop or uninstall it - check Task "
                    "Manager's Services tab (or 'logman query -ets' in an elevated prompt) for a "
                    "lingering PresentMon-related session, and reboot if you find one, since "
                    "stopping the service alone does not release it; (2) Windows' Core Isolation / "
                    "Memory Integrity (Settings -> Windows Security -> Device security) is "
                    "independently known to block this kind of ETW capture regardless of admin "
                    "rights - worth testing with it temporarily off if the above doesn't help.\n\n"
                    f"PresentMon's message:\n{detail}"
                )
            raise BenchmarkError(
                f"PresentMon needs administrator rights to trace another process by name "
                f"(targeted {target_desc}). Close BF6 Tuner and relaunch it as Administrator "
                "(right-click -> Run as administrator), then try recording again - nothing "
                f"else in the app needs elevation, only this.\n\nPresentMon's message:\n{detail}"
            )
        raise BenchmarkError(
            f"PresentMon (targeted {target_desc}) exited with code {result.returncode}:\n{detail}"
        )
    if not output_csv.is_file():
        raise BenchmarkError(
            "PresentMon reported success but did not write a CSV file. "
            "The capture may have been stopped before any frames were presented - "
            "make sure the game was running and in focus during the capture."
        )


def _find_column(header: list[str]) -> str:
    lowered = {name.lower(): name for name in header}
    for candidate in FRAME_TIME_COLUMNS:
        if candidate in lowered:
            return lowered[candidate]
    for candidate in FRAME_TIME_COLUMNS:
        for lower_name, real_name in lowered.items():
            if candidate in lower_name:
                return real_name
    raise BenchmarkError(
        "Could not find a frame-time column in PresentMon's output. "
        f"Columns seen: {', '.join(header) or '(empty file)'}"
    )


def _low_fps(frame_times_ms: list[float], fraction: float) -> float | None:
    if not frame_times_ms:
        return None
    take = max(1, round(len(frame_times_ms) * fraction))
    slowest = sorted(frame_times_ms, reverse=True)[:take]
    avg_ms = sum(slowest) / len(slowest)
    return 1000.0 / avg_ms if avg_ms > 0 else None


@dataclass
class FrameStats:
    sample_count: int
    duration_s: float
    avg_fps: float
    low_1pct_fps: float | None
    low_01pct_fps: float | None
    avg_frame_ms: float
    max_frame_ms: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_csv(path: Path) -> FrameStats:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
    except OSError as exc:
        raise BenchmarkError(f"Could not read {path}: {exc}") from exc

    reader = csv.DictReader(text.splitlines())
    if reader.fieldnames is None:
        raise BenchmarkError(f"{path} has no header row - is it a PresentMon CSV?")
    column = _find_column(list(reader.fieldnames))

    frame_times: list[float] = []
    for row in reader:
        raw = row.get(column)
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        if value > 0:
            frame_times.append(value)

    if not frame_times:
        raise BenchmarkError(
            f"{path} parsed but contained no usable frame times in column '{column}'. "
            "The capture may have started before the game was rendering anything."
        )

    avg_ms = sum(frame_times) / len(frame_times)
    return FrameStats(
        sample_count=len(frame_times),
        duration_s=sum(frame_times) / 1000.0,
        avg_fps=1000.0 / avg_ms,
        low_1pct_fps=_low_fps(frame_times, 0.01),
        low_01pct_fps=_low_fps(frame_times, 0.001),
        avg_frame_ms=avg_ms,
        max_frame_ms=max(frame_times),
    )


@dataclass
class Recording:
    """One capture, auto-saved with the prediction it's being checked
    against, so a history of "did reality match the model" builds up
    without the user managing files by hand."""
    stats: FrameStats
    taken_at: str
    preset: str
    predicted_fps: int
    gpu_fps: int
    cpu_fps: int
    bottleneck: str
    resolution: str
    refresh_hz: int
    cpu_name: str
    gpu_name: str
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Recording":
        stats_data = dict(data.get("stats", {}))
        stats = FrameStats(**stats_data)
        rest = {k: v for k, v in data.items() if k != "stats"}
        return Recording(stats=stats, **rest)

    @property
    def delta_fps(self) -> int:
        return round(self.stats.avg_fps) - self.predicted_fps


def benchmark_root() -> Path:
    base = os.environ.get("APPDATA") or os.path.join(str(Path.home()), ".config")
    root = Path(base) / "BF6Tuner" / "benchmarks"
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_recording(recording: Recording) -> Path:
    root = benchmark_root()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = root / f"{stamp}.json"
    path.write_text(json.dumps(recording.to_dict(), indent=2), encoding="utf-8")
    return path


def list_recordings() -> list[tuple[Path, Recording]]:
    """Newest first. A corrupt/hand-edited file is skipped, not fatal -
    same tolerance as writer.list_restore_points()."""
    results: list[tuple[Path, Recording]] = []
    for path in benchmark_root().glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            results.append((path, Recording.from_dict(data)))
        except (OSError, ValueError, TypeError, KeyError):
            continue
    results.sort(key=lambda pair: pair[0].name, reverse=True)
    return results


def delete_recording(path: Path) -> None:
    path.unlink(missing_ok=True)
