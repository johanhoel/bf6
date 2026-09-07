"""Checks the GitHub repository for a newer build than the one running.

There is no release/tag mechanism here (see README "Getting the executable"):
every push to `main` builds on GitHub Actions and uploads the executables as a
workflow artifact. So "is there an update" is answered by comparing the commit
this build was made from against the tip of `main`, via the public GitHub REST
API - no token needed for a public repo's read endpoints, and this module
never pushes or writes anything anywhere.

Never raises. Any network failure, malformed response, or unknown local
commit comes back as an ``UpdateInfo`` with ``status="error"``/``"unknown"``
rather than a traceback, so a flaky or offline connection cannot block
startup - the same philosophy as ``hardware.detect()``.
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
    ``packaging/build.py`` (mirroring ``_keyring.py``'s pattern - generated,
    gitignored, regenerated every build). A source checkout falls back to
    asking git directly, so a dev run of ``python -m bf6tuner`` gets a
    meaningful answer too.
    """
    try:
        from . import _build_info  # type: ignore[attr-defined]
        sha = getattr(_build_info, "GIT_COMMIT", "")
        if sha:
            return sha
    except ImportError:
        pass

    if getattr(sys, "frozen", False):
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
