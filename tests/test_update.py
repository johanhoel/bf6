"""Tests for the pure parsing logic in bf6tuner.update.

check_for_update() itself talks to the network and is exercised by hand, not
in CI - these tests cover the response-parsing helpers with hand-built
payloads shaped like the real GitHub API, so no network access is needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bf6tuner import update  # noqa: E402


def _commit(sha: str, message: str, name: str = "Someone") -> dict:
    return {
        "sha": sha,
        "html_url": f"https://github.com/johanhoel/bf6/commit/{sha}",
        "commit": {"message": message, "author": {"name": name, "date": "2026-09-06T12:00:00Z"}},
        "author": {"login": name.lower()},
    }


def test_parse_compare_reports_ahead_by_and_newest_first():
    payload = {
        "ahead_by": 2,
        "commits": [
            _commit("aaa1111", "Older commit\n\nBody text"),
            _commit("bbb2222", "Newer commit"),
        ],
    }
    ahead_by, commits = update.parse_compare(payload)
    assert ahead_by == 2
    assert [c.sha for c in commits] == ["bbb2222", "aaa1111"]
    assert commits[0].subject == "Newer commit"
    assert commits[1].subject == "Older commit"  # only the first line, body dropped


def test_parse_compare_up_to_date_has_no_commits():
    ahead_by, commits = update.parse_compare({"ahead_by": 0, "commits": []})
    assert ahead_by == 0
    assert commits == []


def test_parse_commit_list_falls_back_to_login_when_author_name_is_missing():
    payload = [{
        "sha": "ccc3333", "html_url": "https://github.com/johanhoel/bf6/commit/ccc3333",
        "commit": {"message": "Fix something", "author": {}},
        "author": {"login": "someone"},
    }]
    commits = update.parse_commit_list(payload)
    assert commits[0].author == "someone"
    assert commits[0].short_sha == "ccc3333"[:7]


def test_commit_info_short_sha_and_subject():
    info = update.CommitInfo(sha="0123456789abcdef", message="Subject line\n\nBody", author="a",
                             date="", url="")
    assert info.short_sha == "0123456"
    assert info.subject == "Subject line"


def test_update_info_available_only_when_status_is_update_available():
    assert update.UpdateInfo(status="update_available").available
    assert not update.UpdateInfo(status="up_to_date").available
    assert not update.UpdateInfo(status="unknown").available
    assert not update.UpdateInfo(status="error").available


def test_local_commit_never_raises():
    # Whatever it returns (a real sha in this checkout, or "" off-git), it must
    # not raise - the same never-crash guarantee as hardware.detect().
    assert isinstance(update.local_commit(), str)
