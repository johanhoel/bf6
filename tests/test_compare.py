"""Tests for the current-vs-recommended comparison."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bf6tuner import compare, database, writer  # noqa: E402
from bf6tuner.engine import Target, recommend  # noqa: E402
from bf6tuner.hardware import HardwareProfile, MemoryStick  # noqa: E402


@pytest.fixture(scope="session")
def db():
    return database.load()


def make_profile(**overrides) -> HardwareProfile:
    base = dict(
        cpu_name="AMD Ryzen 7 7800X3D 8-Core Processor", cpu_vendor="amd",
        cores=8, threads=16, p_cores=8, e_cores=0, hybrid=False, max_clock_ghz=5.0,
        gpu_name="NVIDIA GeForce RTX 4070 SUPER", gpu_vendor="nvidia", vram_gb=12.0,
        ram_gb=32.0, ram_speed_mts=6000,
        ram_sticks=[MemoryStick(16.0, 6000, "A1"), MemoryStick(16.0, 6000, "B1")],
        width=2560, height=1440, refresh_hz=165, max_refresh_hz=165, detected=True,
    )
    base.update(overrides)
    return HardwareProfile(**base)


ULTRA_PROFSAVE = "\n".join([
    "GstRender.FieldOfViewVertical 70",
    "GstRender.VSyncMode 1",
    "GstRender.TextureQuality 3",
    "GstRender.ShadowQuality 3",
    "GstRender.LightingQuality 3",
    "GstRender.EffectsQuality 3",
    "GstRender.MeshQuality 3",
    "GstRender.TerrainQuality 3",
    "GstRender.VegetationQuality 3",
    "GstRender.PostProcessQuality 3",
    "GstRender.AmbientOcclusion 2",
    "GstRender.MotionBlurWorld 0.500000",
    "GstRender.ChromaticAberration 1",
    "GstRender.FilmGrain 1",
    "GstRender.RayTracingEnabled 1",
    "GstRender.UltraLowLatency 0",
    "GstRender.Brightness 0.500000",
    "GstAudio.MusicVolume 80",
    "GstInput.MouseSensitivity 0.42",
    "Some.Key.We.Do.Not.Manage 5",
]) + "\n"


@pytest.fixture
def rec(db):
    return recommend(db, make_profile(), Target(preset="competitive", width=2560, height=1440, refresh_hz=165))


def change_for(comparison, setting_id):
    return next((c for c in comparison.changes if c.setting_id == setting_id), None)


# -- reading the current configuration --------------------------------------

def test_current_values_are_read_and_scaled(db, rec):
    c = compare.build(db, rec, ULTRA_PROFSAVE, "")
    shadows = change_for(c, "shadow_quality")
    assert shadows.current_value == 3 and shadows.current_display == "Ultra"

    # Motion blur is 0.0-1.0 on disk and a percentage in the app.
    blur = change_for(c, "motion_blur")
    assert blur.current_value == 50
    assert blur.new_value == 0


def test_keys_absent_from_the_profile_are_reported_as_not_comparable(db, rec):
    c = compare.build(db, rec, ULTRA_PROFSAVE, "")
    ids = {u.setting_id for u in c.unknown}
    assert "upscaler" in ids and "display_mode" in ids
    assert all(u.current_display == "not stored in the profile" for u in c.unknown)


def test_personal_settings_are_shown_but_never_changed(db, rec):
    c = compare.build(db, rec, ULTRA_PROFSAVE, "")
    personal = {p.setting_id: p for p in c.personal}
    assert "mouse_sensitivity" in personal
    assert personal["mouse_sensitivity"].new_display == "left alone"
    assert all(p.setting_id != "mouse_sensitivity" for p in c.changes)


def test_matching_configuration_produces_no_changes(db, rec):
    """Feed the recommendation back in as the current config."""
    lines = []
    for choice in rec.settings:
        if not choice.profsave_key or choice.value == "keep":
            continue
        value = choice.value
        if isinstance(value, (int, float)) and choice.profsave_scale != 1.0:
            value = f"{value * choice.profsave_scale:.6f}"
        lines.append(f"{choice.profsave_key} {value}")
    c = compare.build(db, rec, "\n".join(lines) + "\n", writer.render_user_cfg(rec))
    assert c.changes == []
    assert c.fps_delta == 0
    assert "already matches" in c.headline
    assert all(x.action == "same" for x in c.cfg_changes)


# -- direction, trade-offs and impact ---------------------------------------

def test_direction_and_tradeoff_text_follow_the_change(db, rec):
    c = compare.build(db, rec, ULTRA_PROFSAVE, "")
    shadows = change_for(c, "shadow_quality")
    assert shadows.direction == "lower"
    assert shadows.pros and shadows.cons
    assert any("largest frame rate lever" in p for p in shadows.pros)
    assert any("around corners" in x for x in shadows.cons)


def test_every_change_carries_a_tradeoff_and_a_reason(db, rec):
    c = compare.build(db, rec, ULTRA_PROFSAVE, "")
    assert c.changes
    for change in c.changes:
        assert change.pros or change.cons, f"{change.setting_id} has no trade-off text"
        assert change.reason, f"{change.setting_id} has no reason"
        assert change.impact_summary


def test_raising_a_setting_gets_the_other_side_of_the_tradeoff(db):
    low = "\n".join([
        "GstRender.TextureQuality 0", "GstRender.ShadowQuality 0", "GstRender.MotionBlurWorld 0.0",
    ]) + "\n"
    recommendation = recommend(db, make_profile(), Target(preset="quality", width=2560, height=1440, refresh_hz=165))
    c = compare.build(db, recommendation, low, "")
    textures = change_for(c, "texture_quality")
    assert textures.direction == "raise"
    assert any("cheapest image quality" in p for p in textures.pros)
    assert textures.vram_delta_gb > 0


def test_costlier_settings_report_a_bigger_fps_gain(db, rec):
    c = compare.build(db, rec, ULTRA_PROFSAVE, "")
    shadows = change_for(c, "shadow_quality")
    grain = change_for(c, "film_grain")
    assert shadows.fps_delta > grain.fps_delta
    assert shadows.gpu_delta_pct < 0  # cheaper frame time


def test_changes_are_ranked_by_impact(db, rec):
    c = compare.build(db, rec, ULTRA_PROFSAVE, "")
    deltas = [abs(x.fps_delta) for x in c.changes]
    assert deltas == sorted(deltas, reverse=True)


def test_frame_time_model_does_not_saturate_on_many_changes(db, rec):
    """The whole point of composing absolute costs rather than summing deltas."""
    c = compare.build(db, rec, ULTRA_PROFSAVE, "")
    assert len(c.changes) > 10
    assert c.current_predicted > 0
    assert c.current_predicted < c.new_predicted
    # Ultra + ray tracing on a 4070 SUPER at 1440p is slow, but not absurdly so.
    assert 40 <= c.current_predicted <= 120


def test_gpu_change_shows_no_gain_on_a_cpu_limited_machine(db):
    profile = make_profile(
        cpu_name="Intel(R) Core(TM) i5-8400", cpu_vendor="intel", cores=6, threads=6,
        max_clock_ghz=4.0, gpu_name="NVIDIA GeForce RTX 4090", vram_gb=24.0,
    )
    recommendation = recommend(db, profile, Target(preset="competitive", width=1920, height=1080, refresh_hz=240))
    c = compare.build(db, recommendation, ULTRA_PROFSAVE, "")
    assert c.current_bottleneck == "CPU"
    post = change_for(c, "post_process_quality")
    if post is not None:  # pure-GPU setting
        assert post.fps_delta == 0


# -- User.cfg diff -----------------------------------------------------------

def test_user_cfg_additions_changes_and_removals(db, rec):
    existing = "GameTime.MaxVariableFps 0\nThread.ProcessorCount 8\n# a comment\n\n"
    c = compare.build(db, rec, ULTRA_PROFSAVE, existing)
    by_key = {x.key: x for x in c.cfg_changes}

    assert by_key["GameTime.MaxVariableFps"].action == "change"
    assert by_key["GameTime.MaxVariableFps"].current == "0"
    # This CPU does not get thread overrides, so an existing cap would be dropped.
    assert by_key["Thread.ProcessorCount"].action == "remove"
    assert any(x.action == "add" for x in c.cfg_changes)


def test_removals_are_surfaced_because_the_file_is_rewritten_wholesale(db, rec):
    c = compare.build(db, rec, ULTRA_PROFSAVE, "Totally.Custom.Line 7\n")
    removal = next(x for x in c.cfg_changes if x.key == "Totally.Custom.Line")
    assert removal.action == "remove"
    assert "backup" in removal.detail.lower()


def test_missing_user_cfg_means_everything_is_an_addition(db, rec):
    c = compare.build(db, rec, ULTRA_PROFSAVE, None)
    assert c.cfg_changes
    assert all(x.action == "add" for x in c.cfg_changes)


def test_cfg_changes_carry_pros_cons_and_risk(db, rec):
    c = compare.build(db, rec, ULTRA_PROFSAVE, "")
    cap = next(x for x in c.cfg_changes if x.key == "GameTime.MaxVariableFps")
    assert cap.pros and cap.cons and cap.confidence == "documented"


# -- degraded inputs ---------------------------------------------------------

def test_missing_profsave_still_compares_the_user_cfg(db, rec):
    c = compare.build(db, rec, None, "GameTime.MaxVariableFps 0\n")
    assert not c.available
    assert "Launch Battlefield 6 once" in c.reason_unavailable
    assert c.cfg_changes
    assert c.headline == "Current configuration unavailable"


def test_unparseable_profile_does_not_crash(db, rec):
    c = compare.build(db, rec, "garbage\n\n!!!\nGstRender.ShadowQuality notanumber\n", "")
    assert c.available
    assert c.current_predicted > 0


# -- rendering ---------------------------------------------------------------

def test_report_includes_the_comparison(db, rec):
    c = compare.build(db, rec, ULTRA_PROFSAVE, "GameTime.MaxVariableFps 0\n")
    report = writer.render_report(rec, c)
    assert "CURRENT vs RECOMMENDED" in report
    assert "SETTING(S) WOULD CHANGE" in report
    assert "USER.CFG - " in report
    assert "marginal" in report


def test_json_export_includes_the_comparison(db, rec):
    import json

    c = compare.build(db, rec, ULTRA_PROFSAVE, "")
    payload = json.loads(writer.render_json(rec, c))
    assert payload["comparison"]["available"] is True
    assert payload["comparison"]["changes"]
    assert payload["comparison"]["current"]["predicted_fps"] > 0


def test_report_without_a_comparison_still_renders(db, rec):
    assert "CURRENT vs RECOMMENDED" not in writer.render_report(rec)


def test_comparison_uses_the_overridden_values(db):
    """An override is what will be written, so it is what the diff must show."""
    forced = recommend(db, make_profile(), Target(preset="competitive", width=2560,
                                                  height=1440, refresh_hz=165),
                       overrides={"shadow_quality": 3})
    c = compare.build(db, forced, ULTRA_PROFSAVE, "")
    shadows = change_for(c, "shadow_quality")
    assert shadows is None, "current is Ultra and the override is Ultra, so nothing changes"
    assert any(u.setting_id == "shadow_quality" for u in c.unchanged)


# -- closest_preset: guessing a starting preset from the real config ---------
# Used for a genuine first-ever launch (see prefs.py / ui/app.py) so the app
# doesn't have to hardcode a default that may not match what's actually saved.

def test_closest_preset_picks_the_preset_with_fewest_changes(db, tmp_path):
    """ULTRA_PROFSAVE is everything maxed out - manually confirmed to need 8
    changes against the 'quality' preset's recommendation vs. 16-18 against
    every other preset, so 'quality' must win."""
    profsave = tmp_path / "PROFSAVE_profile"
    profsave.write_text(ULTRA_PROFSAVE, encoding="utf-8")
    base = Target(preset="competitive", width=2560, height=1440, refresh_hz=165)

    result = compare.closest_preset(db, make_profile(), base, profsave, None)

    assert result == "quality"


def test_closest_preset_returns_none_without_a_profsave_file(db, tmp_path):
    """Nothing to compare against yet - must not guess blind."""
    base = Target(preset="competitive", width=2560, height=1440, refresh_hz=165)
    missing = tmp_path / "does-not-exist"

    assert compare.closest_preset(db, make_profile(), base, missing, None) is None
    assert compare.closest_preset(db, make_profile(), base, None, None) is None


# -- seed_overrides_from_current: matching real config exactly, not just a preset --

def test_seed_overrides_from_current_captures_every_real_difference(db):
    """Even against its own closest preset ('quality'), ULTRA_PROFSAVE still
    differs on 8 settings (confirmed via closest_preset's own test) - every
    one of those must come back as a ready-to-use override at its real
    current value, and nothing else."""
    quality_rec = recommend(db, make_profile(), Target(preset="quality", width=2560,
                                                        height=1440, refresh_hz=165))
    c = compare.build(db, quality_rec, ULTRA_PROFSAVE, "")

    seed = compare.seed_overrides_from_current(c)

    assert set(seed) == {change.setting_id for change in c.changes}
    assert len(seed) == 8
    assert seed["texture_quality"] == 3  # Ultra, read straight off the profile
    assert seed["vsync"] == 1  # ULTRA_PROFSAVE's real current value, not the recommendation's
    # Personal (mouse sensitivity) and anything not on a preset's own list
    # must never be force-set - only real, non-personal differences.
    assert "mouse_sensitivity" not in seed


def test_seed_overrides_from_current_is_empty_when_nothing_differs(db, rec):
    """A config matching the recommendation exactly needs no overrides at
    all - seeding must not invent redundant ones."""
    lines = []
    for choice in rec.settings:
        if not choice.profsave_key or choice.value == "keep":
            continue
        value = choice.value
        if isinstance(value, (int, float)) and choice.profsave_scale != 1.0:
            value = f"{value * choice.profsave_scale:.6f}"
        lines.append(f"{choice.profsave_key} {value}")
    c = compare.build(db, rec, "\n".join(lines), "")

    assert compare.seed_overrides_from_current(c) == {}
