"""Tests for locating the game's two config files.

The rigid original lookup only accepted
``<Documents>/Battlefield 6/settings/PROFSAVE_profile`` (or a ``steam``
subfolder). Real installs put it elsewhere, so these cover the layouts that
lookup missed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bf6tuner import paths, prefs  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    for name in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "Documents").mkdir()
    return tmp_path


def write_profile(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "PROFSAVE_profile"
    target.write_text("GstRender.ShadowQuality 3\n")
    return target


# -- layouts the old lookup handled -----------------------------------------

def test_finds_the_classic_documents_layout(home):
    expected = write_profile(home / "Documents" / "Battlefield 6" / "settings")
    found, _ = paths.find_profsave_files()
    assert found == [expected]


def test_finds_the_steam_subfolder_layout(home):
    expected = write_profile(home / "Documents" / "Battlefield 6" / "settings" / "steam")
    found, _ = paths.find_profsave_files()
    assert found == [expected]


# -- layouts it did not ------------------------------------------------------

def test_finds_a_per_account_subfolder(home):
    expected = write_profile(
        home / "Documents" / "Battlefield 6" / "settings" / "1000123456789"
    )
    found, _ = paths.find_profsave_files()
    assert found == [expected]


def test_finds_a_profile_without_the_settings_folder(home):
    expected = write_profile(home / "Documents" / "Battlefield 6")
    found, _ = paths.find_profsave_files()
    assert found == [expected]


def test_finds_a_compact_folder_name(home):
    expected = write_profile(home / "Documents" / "Battlefield6" / "settings")
    found, _ = paths.find_profsave_files()
    assert found == [expected]


def test_finds_a_profile_under_localappdata(home):
    expected = write_profile(
        home / "AppData" / "Local" / "Battlefield 6" / "settings"
    )
    found, _ = paths.find_profsave_files()
    assert found == [expected]


def test_finds_a_profile_under_saved_games(home):
    expected = write_profile(home / "Saved Games" / "Battlefield 6")
    found, _ = paths.find_profsave_files()
    assert found == [expected]


def test_ignores_other_battlefield_titles(home):
    write_profile(home / "Documents" / "Battlefield 2042" / "settings")
    write_profile(home / "Documents" / "Battlefield V" / "settings")
    found, _ = paths.find_profsave_files()
    assert found == []


def test_multiple_profiles_are_ranked_newest_first(home):
    import os
    import time

    older = write_profile(home / "Documents" / "Battlefield 6" / "settings" / "acct1")
    newer = write_profile(home / "Documents" / "Battlefield 6" / "settings" / "acct2")
    old_time = time.time() - 3600
    os.utime(older, (old_time, old_time))

    found, _ = paths.find_profsave_files()
    assert found[0] == newer
    assert set(found) == {older, newer}


def test_search_is_reported_even_when_nothing_is_found(home):
    (home / "Documents" / "Battlefield 6" / "settings" / "empty").mkdir(parents=True)
    found, searched = paths.find_profsave_files()
    assert found == []
    assert searched, "a failed search must still say where it looked"
    summary = paths.GamePaths(searched=searched).search_summary()
    assert "Searched" in summary


def test_walk_is_bounded(home):
    """A root like %USERPROFILE% must not turn into an unbounded filesystem walk."""
    deep = home / "Documents" / "Battlefield 6"
    current = deep
    for index in range(12):
        current = current / f"level{index}"
    write_profile(current)

    found, searched = paths.find_profsave_files()
    assert found == [], "a profile buried 12 levels deep is not a real layout"
    assert len(searched) < 400


# -- manual overrides --------------------------------------------------------

def test_override_wins_over_detection(home, monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    detected = write_profile(home / "Documents" / "Battlefield 6" / "settings")
    elsewhere = write_profile(tmp_path / "somewhere else")

    auto = paths.discover()
    assert auto.profsave == detected
    assert "profsave" not in auto.overridden

    forced = paths.discover({"profsave": str(elsewhere)})
    assert forced.profsave == elsewhere
    assert "profsave" in forced.overridden


def test_override_pointing_at_a_missing_file_is_ignored(home):
    detected = write_profile(home / "Documents" / "Battlefield 6" / "settings")
    result = paths.discover({"profsave": str(home / "gone" / "PROFSAVE_profile")})
    assert result.profsave == detected


def test_prefs_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert prefs.load() == {}
    prefs.set_override("profsave", r"D:\somewhere\PROFSAVE_profile")
    assert prefs.load()["profsave"] == r"D:\somewhere\PROFSAVE_profile"
    prefs.set_override("profsave", None)
    assert prefs.load() == {}


def test_corrupt_prefs_file_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    directory = tmp_path / "BF6Tuner"
    directory.mkdir()
    (directory / "paths.json").write_text("{ not json")
    assert prefs.load() == {}


def test_profiles_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert prefs.load_profiles() == {}

    data = {"preset": "competitive", "width": 2560, "height": 1440,
            "setting_overrides": {"shadow_quality": 3}, "cfg_overrides": {}}
    prefs.save_profile("Tournament", data)
    assert prefs.load_profiles() == {"Tournament": data}

    prefs.save_profile("Chill", {"preset": "quality"})
    assert set(prefs.load_profiles()) == {"Tournament", "Chill"}

    prefs.delete_profile("Chill")
    assert set(prefs.load_profiles()) == {"Tournament"}


def test_target_round_trip(tmp_path, monkeypatch):
    """The last-used preset/toggles (see prefs.py's module docstring for why
    this, unlike most state, is persisted rather than re-derived)."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert prefs.load_target() == {}

    prefs.save_target({"preset": "esports", "overlay": False, "vrr": True})
    assert prefs.load_target() == {"preset": "esports", "overlay": False, "vrr": True}

    prefs.save_target({"preset": "quality", "overlay": True})
    assert prefs.load_target() == {"preset": "quality", "overlay": True}


def test_rename_profile_preserves_data_and_drops_the_old_name(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    prefs.save_profile("Old name", {"preset": "esports"})
    prefs.rename_profile("Old name", "New name")
    profiles = prefs.load_profiles()
    assert "Old name" not in profiles
    assert profiles["New name"] == {"preset": "esports"}


def test_rename_profile_is_a_no_op_for_a_name_that_does_not_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    prefs.rename_profile("Nothing here", "Still nothing")
    assert prefs.load_profiles() == {}


def test_deleting_a_profile_that_does_not_exist_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    prefs.delete_profile("Never saved")
    assert prefs.load_profiles() == {}


def test_corrupt_profiles_file_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    directory = tmp_path / "BF6Tuner"
    directory.mkdir()
    (directory / "profiles.json").write_text("{ not json")
    assert prefs.load_profiles() == {}


# -- find_running_game_pid ----------------------------------------------------
# IS_WINDOWS is monkeypatched rather than relying on the actual platform, so
# these run the same way in CI (Linux) as on a real Windows machine.

def test_find_running_game_pid_parses_tasklist_csv(monkeypatch):
    monkeypatch.setattr(paths, "IS_WINDOWS", True)
    listing = (
        '"notepad.exe","999","Console","1","10 K"\r\n'
        '"bf6.exe","12345","Console","1","500,000 K"\r\n'
    )
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=listing, stderr=""),
    )
    assert paths.find_running_game_pid() == 12345


def test_find_running_game_pid_returns_none_when_not_found(monkeypatch):
    monkeypatch.setattr(paths, "IS_WINDOWS", True)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout='"notepad.exe","999"\r\n', stderr=""),
    )
    assert paths.find_running_game_pid() is None


def test_find_running_game_pid_returns_none_off_windows(monkeypatch):
    monkeypatch.setattr(paths, "IS_WINDOWS", False)
    assert paths.find_running_game_pid() is None


def test_find_running_game_pid_never_raises_on_a_broken_tasklist(monkeypatch):
    monkeypatch.setattr(paths, "IS_WINDOWS", True)
    def broken(*a, **k):
        raise OSError("tasklist.exe not found")
    monkeypatch.setattr(subprocess, "run", broken)
    assert paths.find_running_game_pid() is None
