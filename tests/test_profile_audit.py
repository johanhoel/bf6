"""Tests for spotting settings keys a game update added.

The trap these guard against: the shipped baseline is one real profile that
contains only GstRender/GstAudio keys. A naive "not in the baseline" check
would report every mouse, controller and key-binding key on a real machine
as "new", which would be false and would bury the one signal that matters.
"""

from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bf6tuner import database, prefs, profile_audit, writer  # noqa: E402
from bf6tuner.engine import Target, recommend  # noqa: E402
from bf6tuner.hardware import HardwareProfile  # noqa: E402

DUMP = ROOT / "examples" / "PROFSAVEbf6mp_profile"
TODAY = dt.date(2026, 10, 18)


@pytest.fixture(scope="session")
def db():
    return database.load()


@pytest.fixture(scope="session")
def dump_text() -> str:
    return DUMP.read_text(encoding="utf-8", errors="ignore")


def with_lines(text: str, *lines: str) -> str:
    return text.rstrip("\n") + "\n" + "\n".join(lines) + "\n"


# -- the baseline itself -----------------------------------------------------

def test_baseline_matches_the_committed_real_profile(dump_text):
    """The baseline is generated from the dump; it must not drift from it."""
    baseline = profile_audit.load_baseline()
    patterns = [re.compile(p) for p in baseline["ignore_patterns"]]
    expected = sorted({
        key for key in profile_audit.parse_keys(dump_text)
        if not any(p.match(key) for p in patterns)
    })
    assert baseline["keys"] == expected
    assert baseline["families"] == sorted({k.split(".")[0] for k in expected})


def test_baseline_excludes_shader_cache_stamps():
    baseline = profile_audit.load_baseline()
    assert not any("ShaderBundleVersion" in key for key in baseline["keys"])


def test_baseline_records_that_it_predates_season_5():
    baseline = profile_audit.load_baseline()
    assert "Season 5" in baseline["captured_before"]


# -- first scan: comparing against the baseline ------------------------------

def test_a_profile_identical_to_the_baseline_has_nothing_new(db, dump_text):
    result, _ = profile_audit.audit(db, dump_text, {}, TODAY)
    assert result.first_scan
    assert result.new_keys == []
    assert "No settings keys beyond" in result.headline


def test_a_new_render_key_is_reported_on_first_scan(db, dump_text):
    text = with_lines(dump_text, "GstRender.SomethingSeason5Added 1")
    result, _ = profile_audit.audit(db, text, {}, TODAY)
    assert [k.key for k in result.new_keys] == ["GstRender.SomethingSeason5Added"]
    assert result.new_keys[0].value == "1"
    assert "never seen" in result.headline


def test_input_keys_are_not_reported_as_new_on_first_scan(db, dump_text):
    """The baseline has no GstInput family - absence there proves nothing."""
    text = with_lines(
        dump_text,
        "GstInput.MouseSensitivity 0.42",
        "GstInput.LeftStickDeadzone 0.0",
        "GstInput.SomeControllerOption 1",
    )
    result, _ = profile_audit.audit(db, text, {}, TODAY)
    assert result.new_keys == []


def test_key_bindings_and_shader_stamps_are_always_ignored(db, dump_text):
    text = with_lines(
        dump_text,
        "GstKeyBinding.infantry.Fire.0.button 256",
        "GstRender.ShaderBundleVersion_deadbeef 3",
    )
    first, history = profile_audit.audit(db, text, {}, TODAY)
    assert first.new_keys == []

    later_text = with_lines(text, "GstKeyBinding.gunner.Zoom.0.button 257")
    later, _ = profile_audit.audit(db, later_text, history, TODAY + dt.timedelta(days=1))
    assert later.new_keys == [], "key bindings appear when a page is visited, not after an update"


# -- later scans: the machine's own history ----------------------------------

def test_key_added_between_scans_is_reported(db, dump_text):
    _, history = profile_audit.audit(db, dump_text, {}, TODAY)
    after_patch = with_lines(dump_text, "GstAudio.NewSeasonFiveSlider 0.500000")
    result, _ = profile_audit.audit(db, after_patch, history, TODAY + dt.timedelta(days=7))

    assert not result.first_scan
    assert [k.key for k in result.new_keys] == ["GstAudio.NewSeasonFiveSlider"]
    assert result.new_keys[0].first_seen == "2026-10-25"
    assert result.last_scan == "2026-10-18"
    assert "since the last scan on 2026-10-18" in result.headline


def test_history_covers_every_family_including_input(db, dump_text):
    """Once there is real history, a new input key is genuine news."""
    first_text = with_lines(dump_text, "GstInput.MouseSensitivity 0.42")
    _, history = profile_audit.audit(db, first_text, {}, TODAY)
    later = with_lines(first_text, "GstInput.TrueZeroDeadzone 1")
    result, _ = profile_audit.audit(db, later, history, TODAY + dt.timedelta(days=1))
    assert [k.key for k in result.new_keys] == ["GstInput.TrueZeroDeadzone"]


def test_nothing_new_between_identical_scans(db, dump_text):
    _, history = profile_audit.audit(db, dump_text, {}, TODAY)
    result, _ = profile_audit.audit(db, dump_text, history, TODAY + dt.timedelta(days=1))
    assert result.new_keys == []
    assert "No new settings keys since the last scan" in result.headline


def test_first_seen_dates_are_kept_across_scans(db, dump_text):
    _, history = profile_audit.audit(db, dump_text, {}, TODAY)
    _, later = profile_audit.audit(db, dump_text, history, TODAY + dt.timedelta(days=30))
    assert later["keys"]["GstRender.TextureQuality"] == "2026-10-18"
    assert later["last_scan"] == "2026-11-17"


def test_a_key_removed_by_an_update_stays_in_history(db, dump_text):
    """Removal is not reported, but must not make the key look new if it returns."""
    _, history = profile_audit.audit(db, dump_text, {}, TODAY)
    without = "\n".join(l for l in dump_text.splitlines() if not l.startswith("GstRender.TextureQuality"))
    _, history = profile_audit.audit(db, without, history, TODAY + dt.timedelta(days=1))
    result, _ = profile_audit.audit(db, dump_text, history, TODAY + dt.timedelta(days=2))
    assert result.new_keys == []


def test_audit_is_pure_and_does_not_touch_disk(db, dump_text, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    history: dict = {}
    profile_audit.audit(db, dump_text, history, TODAY)
    assert history == {}, "the caller's history must not be mutated"
    assert not (tmp_path / "BF6Tuner").exists()


# -- counts and inventory ----------------------------------------------------

def test_modelled_and_ignored_counts(db, dump_text):
    result, _ = profile_audit.audit(db, dump_text, {}, TODAY)
    modelled = {s["profsave_key"] for s in db.settings if s.get("profsave_key")}
    keys = profile_audit.parse_keys(dump_text)
    assert result.total_keys == len(keys)
    assert result.modelled == len(modelled & set(keys))
    assert result.ignored == sum(1 for k in keys if "ShaderBundleVersion" in k)
    assert result.total_keys == result.modelled + result.ignored + sum(result.unmodelled_by_family.values())


def test_empty_values_are_parsed(db):
    keys = profile_audit.parse_keys("GstAudio.SpeakerConfiguration \nGstAudio.Volume 1.000000\n")
    assert keys == {"GstAudio.SpeakerConfiguration": "", "GstAudio.Volume": "1.000000"}


def test_garbage_lines_are_skipped(db):
    keys = profile_audit.parse_keys("\n# comment\n!!!\n   \nGstRender.Valid 1\n")
    assert keys == {"GstRender.Valid": "1"}


# -- output ------------------------------------------------------------------

def test_render_lists_keys_and_says_they_are_not_recommendations(db, dump_text):
    text = with_lines(dump_text, "GstRender.SomethingSeason5Added 1")
    result, _ = profile_audit.audit(db, text, {}, TODAY)
    rendered = profile_audit.render(result)
    assert "GstRender.SomethingSeason5Added = 1" in rendered
    assert "not recommendations" in rendered


def test_report_includes_the_section_only_when_given_an_audit(db, dump_text):
    rec = recommend(db, HardwareProfile(detected=True), Target(preset="competitive"))
    result, _ = profile_audit.audit(db, dump_text, {}, TODAY)
    assert "NEW SETTINGS KEYS" in writer.render_report(rec, None, result)
    assert "NEW SETTINGS KEYS" not in writer.render_report(rec)


def test_history_round_trips_through_prefs(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert prefs.load_profile_key_history() == {}
    prefs.save_profile_key_history({"last_scan": "2026-10-18", "keys": {"GstRender.A": "2026-10-18"}})
    assert prefs.load_profile_key_history()["keys"] == {"GstRender.A": "2026-10-18"}
