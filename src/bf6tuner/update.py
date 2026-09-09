"""Checks the GitHub repository for a newer build than the one running, and
(new, 2026-09-08) can fetch and install one.

"Is there an update" is answered by comparing the commit this build was made
from against the tip of `main`, via the public GitHub REST API - no token
needed for a public repo's read endpoints.

``check_for_update()`` never raises: any network failure, malformed response,
or unknown local commit comes back as an ``UpdateInfo`` with
``status="error"``/``"unknown"`` rather than a traceback, so a flaky or
offline connection cannot block startup - the same philosophy as
``hardware.detect()``.

Actually fetching a build is different, and lives in its own section below
(``download_update`` / ``apply_update_and_relaunch``) with its own exception
type, ``SelfUpdateError`` - this *is* a user-initiated action (a button
press), so it raises with a clear message on failure rather than failing
silently, the same way ``BenchmarkError``/``BundleError`` do elsewhere in
this app. It downloads from a **GitHub Release**, not the GitHub Actions
artifact ``check_for_update`` links to: Actions artifacts require an
authenticated API call and expire after 90 days, neither of which a shipped
app can rely on without embedding a credential (bad idea for a public repo).
``packaging/build.py``'s CI workflow instead publishes every push to `main`
to a single rolling release tagged ``latest`` (see ``.github/workflows/
build.yml``) - a public, permanent, unauthenticated download URL, matching
this project's existing "every push is a build" continuous-deploy model
rather than introducing real version tags.

Self-update only applies to a genuinely frozen build (``sys.frozen``) - a
source checkout has no single ``.exe`` to replace; use ``git pull`` instead,
which is exactly why ``run-from-source.bat`` exists as the SmartScreen
workaround it is (see ARCHITECTURE.md).
"""

from __future__ import annotations

import datetime as _dt
import json
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = "johanhoel/bf6"
BRANCH = "main"
API_ROOT = f"https://api.github.com/repos/{REPO}"
ACTIONS_URL = f"https://github.com/{REPO}/actions?query=branch%3A{BRANCH}"
COMPARE_URL = f"https://github.com/{REPO}/compare"
RELEASE_TAG = "latest"
_USER_AGENT = "BF6Tuner-update-check"


@dataclass
class CommitInfo:
    sha: str
    message: str
    author: str
    date: str
    url: str

    @property
    def short_sha(self) -> str:
        return self.sha[:7]

    @property
    def subject(self) -> str:
        """First line of the commit message - what the changelog shows."""
        return self.message.splitlines()[0] if self.message else ""


@dataclass
class UpdateInfo:
    current_sha: str = ""
    latest_sha: str = ""
    ahead_by: int = 0
    commits: list[CommitInfo] = field(default_factory=list)
    # "up_to_date" | "update_available" | "unknown" | "error"
    status: str = "unknown"
    error: str = ""
    build_url: str = ACTIONS_URL
    compare_url: str = ""

    @property
    def available(self) -> bool:
        return self.status == "update_available"


def local_commit() -> str:
    """The commit this running build was produced from, best effort.

    Frozen builds get it from ``_build_info.py``, written at build time by
    ``packaging/build.py`` (generated, gitignored, regenerated every build).
    A source checkout asks git directly instead - deliberately *never*
    trusting ``_build_info.py`` there, even if one happens to be lying
    around from an old local ``packaging/build.py`` run: that file is
    gitignored, so nothing ever cleans it up, and running it through
    ``run-from-source.bat``/``python -m bf6tuner`` afterwards would silently
    report an old build's commit instead of the code actually on disk (this
    happened for real - see ARCHITECTURE.md's work log). Only a genuinely
    frozen build, where git may not even be installed, has any reason to
    read the baked-in file at all.
    """
    if getattr(sys, "frozen", False):
        try:
            from . import _build_info  # type: ignore[attr-defined]
            return getattr(_build_info, "GIT_COMMIT", "") or ""
        except ImportError:
            return ""  # built without _build_info.py - can't know

    try:
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(root), capture_output=True,
            text=True, timeout=5, check=True,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _get_json(url: str, timeout: float) -> Any:
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": _USER_AGENT,
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _commit_info(entry: dict[str, Any]) -> CommitInfo:
    commit = entry.get("commit", {}) or {}
    author = commit.get("author", {}) or {}
    login = ((entry.get("author") or {}).get("login")) or ""
    return CommitInfo(
        sha=entry.get("sha", ""),
        message=commit.get("message", ""),
        author=author.get("name") or login or "unknown",
        date=author.get("date", ""),
        url=entry.get("html_url", ""),
    )


def parse_compare(payload: dict[str, Any]) -> tuple[int, list[CommitInfo]]:
    """Pure parsing of the GitHub 'compare' API response.

    Kept separate from the network call so it is unit-testable with a
    hand-built payload rather than a live request.
    """
    ahead_by = int(payload.get("ahead_by", 0) or 0)
    entries = payload.get("commits") or []
    commits = [_commit_info(c) for c in entries]
    commits.reverse()  # the API lists oldest-first; a changelog reads newest-first
    return ahead_by, commits


def parse_commit_list(payload: list[dict[str, Any]]) -> list[CommitInfo]:
    return [_commit_info(c) for c in payload]


def _friendly_error(exc: Exception) -> str:
    """GitHub's unauthenticated API is capped at 60 requests/hour *per IP*,
    shared with everything else on the same network - easy to exhaust and
    easy to mistake for the check being broken. Recognise it and say so."""
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 403:
        remaining = (exc.headers.get("X-RateLimit-Remaining") if exc.headers else None)
        if remaining == "0":
            when = ""
            reset = exc.headers.get("X-RateLimit-Reset") if exc.headers else None
            if reset:
                try:
                    dt = _dt.datetime.fromtimestamp(int(reset), tz=_dt.timezone.utc)
                    when = f" It resets at {dt.strftime('%H:%M UTC')}."
                except (ValueError, OSError, OverflowError):
                    pass
            return (
                "GitHub's API rate limit was hit - 60 checks per hour, shared across every "
                "unauthenticated request from this network, not just this app." + when +
                " This clears on its own; no action needed."
            )
    return str(exc)


def check_for_update(timeout: float = 6.0) -> UpdateInfo:
    current = local_commit()

    if current:
        try:
            payload = _get_json(f"{API_ROOT}/compare/{current}...{BRANCH}", timeout)
            ahead_by, commits = parse_compare(payload)
            entries = payload.get("commits") or []
            latest_sha = entries[-1].get("sha", "") if entries else current
            status = "update_available" if ahead_by > 0 else "up_to_date"
            return UpdateInfo(
                current_sha=current, latest_sha=latest_sha, ahead_by=ahead_by,
                commits=commits, status=status,
                compare_url=f"{COMPARE_URL}/{current}...{BRANCH}",
            )
        except Exception:
            pass  # history may have diverged (rebase/force-push) - fall through

    # Either the local commit is unknown, or comparing against it failed.
    # Show what has recently landed with no "ahead by" claim, rather than
    # nothing at all.
    try:
        payload = _get_json(f"{API_ROOT}/commits?sha={BRANCH}&per_page=15", timeout)
        commits = parse_commit_list(payload)
        latest_sha = commits[0].sha if commits else ""
        return UpdateInfo(
            current_sha=current, latest_sha=latest_sha, ahead_by=0,
            commits=commits, status="unknown",
        )
    except Exception as exc:
        return UpdateInfo(current_sha=current, status="error", error=_friendly_error(exc))


# --------------------------------------------------------------------------
# Self-update: download the latest release and swap the running exe for it.
# See the module docstring for why this is a GitHub Release, not the Actions
# artifact, and why it only applies to a frozen build.
# --------------------------------------------------------------------------

class SelfUpdateError(RuntimeError):
    """Raised when the in-app self-update can't proceed. Always caught and
    shown to the user - this is a button press, not a passive background
    check, so unlike check_for_update() it is allowed to raise."""


def _require_frozen() -> None:
    if not getattr(sys, "frozen", False):
        raise SelfUpdateError(
            "Self-update only applies to the built .exe, not a source checkout - "
            "use 'git pull' (or UPDATE.bat) instead."
        )


def find_asset_url(payload: dict[str, Any], asset_name: str) -> str | None:
    """Pure lookup of one asset's download URL in a GitHub release API
    payload. Kept separate from the network call so it is unit-testable with
    a hand-built payload, same as parse_compare/parse_commit_list above."""
    for asset in payload.get("assets") or []:
        if asset.get("name") == asset_name:
            url = asset.get("browser_download_url")
            if url:
                return url
    return None


def _latest_release_asset_url(asset_name: str, timeout: float) -> str:
    """The download URL for `asset_name` (e.g. 'BF6Tuner.exe') attached to
    the rolling 'latest' release. Raises SelfUpdateError with a clear reason
    rather than letting a KeyError/network error surface raw."""
    try:
        payload = _get_json(f"{API_ROOT}/releases/tags/{RELEASE_TAG}", timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise SelfUpdateError(
                "No 'latest' release was found on GitHub yet - it may not have "
                "published for the first time. Try the Actions artifact instead "
                "(see 'Open GitHub Actions build')."
            ) from exc
        raise SelfUpdateError(_friendly_error(exc)) from exc
    except Exception as exc:
        raise SelfUpdateError(f"Could not reach GitHub: {exc}") from exc

    url = find_asset_url(payload, asset_name)
    if url is None:
        raise SelfUpdateError(
            f"The latest release has no '{asset_name}' asset. It may still be publishing - "
            "try again in a minute, or download it manually from the Releases page."
        )
    return url


def download_update(dest: Path, timeout: float = 60.0) -> Path:
    """Download the current exe's counterpart from the 'latest' release to
    `dest` (a temp path, not the running exe itself - see
    apply_update_and_relaunch for why). Raises SelfUpdateError on anything
    that isn't a clean success, including a sanity check on the downloaded
    bytes: a Windows PE binary starts with 'MZ' and this app's exes are
    always well over a megabyte, so a truncated download or an unexpected
    HTML error page (e.g. a GitHub outage page) gets caught here rather than
    silently "installed".
    """
    _require_frozen()
    asset_name = Path(sys.executable).name
    url = _latest_release_asset_url(asset_name, timeout)

    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            data = response.read()
    except Exception as exc:
        raise SelfUpdateError(f"Download failed: {exc}") from exc

    if len(data) < 1_000_000 or data[:2] != b"MZ":
        raise SelfUpdateError(
            "The downloaded file doesn't look like a valid Windows executable "
            f"({len(data):,} bytes) - not installing it. Try again, or download "
            "manually from the Releases page."
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def apply_update_and_relaunch(new_exe: Path) -> None:
    """Swap the running exe for `new_exe` and relaunch - then the caller
    must exit immediately.

    Windows will not let a running process delete or overwrite its own .exe
    file, so this writes a tiny detached helper script that waits for every
    process running this exe to disappear, then does the move and
    relaunch, then deletes itself. Best-effort by nature, same as every
    self-updater that has ever shipped on Windows without a separate
    updater binary - there is no cleaner way to replace a running exe with
    itself from inside itself.

    Waits on *every process running this exe's path*, not just this
    process's own PID: a PyInstaller onefile build is actually two
    processes (an outer bootloader stub and an inner re-exec'd child doing
    the real work - `os.getpid()` from inside Python only ever sees the
    child). PyInstaller 6.22.1+ added a security check where the child
    verifies its parent's exe path matches its own, to block PID-reuse
    spoofing attacks - hit for real here: waiting only for the child PID
    let the relaunch race the bootloader parent's own exit, and the new
    instance's validation failed with "parent process has different
    executable" once the file had already been replaced out from under a
    parent that hadn't quite finished exiting yet.

    This is a PowerShell script, not a batch file - not a style choice, a
    bug fix. A batch-file version of this (see git history) was verified
    correct twice by hand - running the exact generated `.bat` directly
    from an interactive shell worked perfectly every time - yet it still
    hung/failed every time it was actually spawned by the running app.
    The difference: `DETACHED_PROCESS` gives the spawned process *no
    console at all*, and legacy console utilities this script depended on
    (`timeout`, and the `tasklist | find` pipe) can misbehave or hang
    without one - invisible when you run the script yourself from a real
    console, real when the app spawns it headless. PowerShell's own
    cmdlets (`Get-Process`, `Start-Sleep`, `Move-Item`, `Start-Process`)
    are .NET calls, not external console programs, so they carry no such
    dependency.
    """
    _require_frozen()
    current = Path(sys.executable)
    script = current.with_suffix(".update.ps1")
    script.write_text(
        # Compare by the process's own resolved image path (Get-Process's
        # .Path), not by name - catches both the bootloader parent and the
        # child regardless of which PID either one has, unlike waiting on
        # a single specific PID (see docstring for why that was the bug).
        f"while (Get-Process | Where-Object {{ $_.Path -eq '{current}' }}) {{ Start-Sleep -Milliseconds 500 }}\n"
        # A small extra buffer past "the process list looks empty" - even
        # after every process is gone, Windows can take a moment to fully
        # release the exe's image/handle; the same margin-on-the-tight-
        # side lesson as every other guess in this function so far.
        f"Start-Sleep -Seconds 1\n"
        f"for ($i = 0; $i -lt 10; $i++) {{\n"
        f"    try {{\n"
        f"        Move-Item -LiteralPath '{new_exe}' -Destination '{current}' -Force -ErrorAction Stop\n"
        f"        break\n"
        f"    }} catch {{\n"
        f"        Start-Sleep -Seconds 1\n"
        f"    }}\n"
        f"}}\n"
        f"Start-Process -FilePath '{current}'\n"
        f"Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force\n",
        encoding="utf-8",
    )
    # CREATE_NO_WINDOW (not DETACHED_PROCESS - see docstring) still gives
    # PowerShell a real, working console under the hood, just an invisible
    # one - the console-dependency failure mode this replaces doesn't apply
    # here regardless, since nothing in the script calls out to an external
    # console utility, but there is no reason to also fight that battle a
    # second time by reusing the flag that caused it.
    base_flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    args = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-WindowStyle", "Hidden", "-File", str(script),
    ]
    try:
        subprocess.Popen(args, creationflags=base_flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, close_fds=True)
    except OSError:
        subprocess.Popen(args, creationflags=base_flags, close_fds=True)
