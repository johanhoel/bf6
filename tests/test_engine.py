"""Tests for the parts of the engine where being wrong costs the user frames."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bf6tuner import database, writer  # noqa: E402
from bf6tuner.engine import Target, match_cpu, match_gpu, recommend  # noqa: E402
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


def cfg_keys(rec) -> set[str]:
    return {line.key for line in rec.cfg if line.key}


# -- database matching -----------------------------------------------------

@pytest.mark.parametrize(
    "name,expected",
    [
        ("NVIDIA GeForce RTX 4070 SUPER", "rtx 4070 super"),
        ("NVIDIA GeForce RTX 4070", "rtx 4070"),
        ("AMD Radeon RX 7900 XTX", "rx 7900 xtx"),
        ("Intel(R) Arc(TM) B580 Graphics", "arc b580"),
        ("NVIDIA GeForce GTX 1080 Ti", "gtx 1080 ti"),
    ],
)
def test_gpu_matching_prefers_the_longest_name(db, name, expected):
    gpu = match_gpu(make_profile(gpu_name=name, vram_gb=0), db)
    assert gpu["id"] == expected
    assert gpu["matched"]


def test_gpu_split_sku_resolved_by_detected_vram(db):
    eight = match_gpu(make_profile(gpu_name="NVIDIA GeForce RTX 4060 Ti", vram_gb=8.0), db)
    sixteen = match_gpu(make_profile(gpu_name="NVIDIA GeForce RTX 4060 Ti", vram_gb=16.0), db)
    assert eight["id"] == "rtx 4060 ti"
    assert sixteen["id"] == "rtx 4060 ti 16gb"


def test_unknown_gpu_falls_back_without_crashing(db):
    gpu = match_gpu(make_profile(gpu_name="Acme Nitro 9000", gpu_vendor="unknown", vram_gb=6.0), db)
    assert not gpu["matched"]
    assert gpu["score"] > 0


def test_cpu_matching(db):
    cpu = match_cpu(make_profile(cpu_name="13th Gen Intel(R) Core(TM) i7-13700K"), db)
    assert cpu["id"] == "i7-13700k"
    assert cpu["hybrid"] is True


def test_unknown_cpu_scored_by_topology(db):
    weak = match_cpu(make_profile(cpu_name="Generic CPU X1", cores=4, threads=8, max_clock_ghz=3.0), db)
    strong = match_cpu(make_profile(cpu_name="Generic CPU X9", cores=16, threads=32, max_clock_ghz=5.5), db)
    assert not weak["matched"]
    assert strong["score"] > weak["score"]


# -- the thread-override policy, which is the whole point -------------------

def test_hybrid_intel_never_gets_thread_caps(db):
    profile = make_profile(
        cpu_name="13th Gen Intel(R) Core(TM) i7-13700K", cpu_vendor="intel",
        cores=16, threads=24, p_cores=8, e_cores=8, hybrid=True,
    )
    target = Target(preset="competitive", allow_thread_overrides=True)
    rec = recommend(db, profile, target)

    assert "Thread.ProcessorCount" not in cfg_keys(rec)
    assert "Thread.MaxProcessorCount" not in cfg_keys(rec)
    explanation = " ".join(w.body for w in rec.warnings)
    assert "efficiency cores" in explanation
    assert "1% low" in explanation


def test_thread_caps_are_opt_in_even_on_homogeneous_cpus(db):
    profile = make_profile(cpu_name="AMD Ryzen 9 5950X", cores=16, threads=32)
    off = recommend(db, profile, Target(preset="competitive"))
    on = recommend(db, profile, Target(preset="competitive", allow_thread_overrides=True))

    assert "Thread.ProcessorCount" not in cfg_keys(off)
    assert "Thread.ProcessorCount" in cfg_keys(on)
    written = {line.key: line.value for line in on.cfg if line.key}
    assert written["Thread.ProcessorCount"] == 30  # 32 threads less two for the OS


def test_small_cpus_never_get_thread_caps(db):
    profile = make_profile(cpu_name="Intel(R) Core(TM) i5-10400", cpu_vendor="intel",
                           cores=6, threads=12, p_cores=6, e_cores=0)
    rec = recommend(db, profile, Target(preset="esports", allow_thread_overrides=True))
    assert "Thread.ProcessorCount" not in cfg_keys(rec)


def test_dual_ccd_x3d_gets_ccd_advice_not_thread_caps(db):
    profile = make_profile(cpu_name="AMD Ryzen 9 7950X3D 16-Core Processor", cores=16, threads=32)
    rec = recommend(db, profile, Target(preset="competitive", allow_thread_overrides=True))
    assert "Thread.ProcessorCount" not in cfg_keys(rec)
    assert any("chiplet" in w.body for w in rec.warnings)
    assert any(t["id"] == "ccd_preference" for t in rec.tweaks)


# -- unsafe and legacy command handling ------------------------------------

def test_never_emits_visibility_exploits_or_api_forcing(db):
    rec = recommend(db, make_profile(), Target(preset="esports", include_legacy_commands=True))
    keys = cfg_keys(rec)
    for forbidden in ("WorldRender.SkyEnable", "UI.DrawEnable", "WorldRender.DrawDebugEnable",
                      "RenderDevice.Dx12Enable", "GstRender.Dx12Enabled"):
        assert forbidden not in keys


def test_legacy_commands_are_opt_in(db):
    plain = cfg_keys(recommend(db, make_profile(), Target(preset="esports")))
    legacy = cfg_keys(recommend(db, make_profile(), Target(preset="esports", include_legacy_commands=True)))
    assert "WorldRender.MotionBlurMaxSampleCount" not in plain
    assert "WorldRender.MotionBlurMaxSampleCount" in legacy


# -- quality selection -----------------------------------------------------

def test_textures_capped_by_vram(db):
    low = recommend(db, make_profile(gpu_name="NVIDIA GeForce RTX 3050", vram_gb=8.0),
                    Target(preset="quality", width=3840, height=2160, refresh_hz=60))
    texture = next(c for c in low.settings if c.setting_id == "texture_quality")
    assert texture.value <= 2
    assert any("VRAM" in w.title for w in low.warnings)


def test_high_vram_card_keeps_max_textures(db):
    rec = recommend(db, make_profile(gpu_name="NVIDIA GeForce RTX 4090", vram_gb=24.0),
                    Target(preset="quality", width=2560, height=1440, refresh_hz=144))
    texture = next(c for c in rec.settings if c.setting_id == "texture_quality")
    assert texture.value == 4


def test_weak_gpu_at_4k_reaches_for_the_upscaler(db):
    rec = recommend(db, make_profile(gpu_name="NVIDIA GeForce RTX 3060", vram_gb=12.0),
                    Target(preset="balanced", width=3840, height=2160, refresh_hz=144))
    assert rec.upscaler_mode in ("quality", "balanced", "performance")
    assert rec.quality_step < 0


def test_1080p_never_drops_below_quality_upscaling(db):
    rec = recommend(db, make_profile(gpu_name="NVIDIA GeForce RTX 3050", vram_gb=8.0),
                    Target(preset="esports", width=1920, height=1080, refresh_hz=240))
    assert rec.upscaler_mode in ("off", "quality")


def test_cpu_bottleneck_is_named_and_stops_pointless_downgrades(db):
    profile = make_profile(
        cpu_name="Intel(R) Core(TM) i5-8400", cpu_vendor="intel", cores=6, threads=6,
        max_clock_ghz=4.0, gpu_name="NVIDIA GeForce RTX 4080 SUPER", vram_gb=16.0,
    )
    rec = recommend(db, profile, Target(preset="competitive", width=1920, height=1080, refresh_hz=240))
    assert rec.bottleneck == "CPU"
    assert rec.upscaler_mode == "off"
    assert any("CPU-limited" in w.title for w in rec.warnings)


def test_motion_blur_and_friends_are_always_off(db):
    for preset in ("esports", "competitive", "balanced", "quality"):
        rec = recommend(db, make_profile(), Target(preset=preset))
        for setting_id in ("motion_blur", "chromatic_aberration", "film_grain", "vignette", "lens_distortion"):
            assert next(c for c in rec.settings if c.setting_id == setting_id).value == 0


def test_personal_settings_are_never_written(db):
    rec = recommend(db, make_profile(), Target(preset="esports"))
    for setting_id in ("mouse_sensitivity", "ads_sensitivity", "master_volume"):
        assert next(c for c in rec.settings if c.setting_id == setting_id).value == "keep"


# -- frame cap -------------------------------------------------------------

def test_frame_cap_sits_below_refresh_when_vrr_is_on(db):
    rec = recommend(db, make_profile(gpu_name="NVIDIA GeForce RTX 4090", vram_gb=24.0),
                    Target(preset="competitive", width=1920, height=1080, refresh_hz=144, vrr=True))
    assert rec.frame_cap == 141


def test_frame_cap_never_promises_more_than_the_machine_can_hold(db):
    rec = recommend(db, make_profile(gpu_name="NVIDIA GeForce GTX 1060", vram_gb=6.0),
                    Target(preset="quality", width=3840, height=2160, refresh_hz=240))
    assert rec.frame_cap <= max(60, int(rec.predicted_fps))


# -- memory penalties ------------------------------------------------------

def test_xmp_off_is_detected_and_quantified(db):
    profile = make_profile(ram_speed_mts=2133,
                           ram_sticks=[MemoryStick(8.0, 2133, "A1"), MemoryStick(8.0, 2133, "B1")],
                           ram_gb=16.0)
    rec = recommend(db, profile, Target(preset="competitive"))
    assert any(t["id"] == "xmp_expo" for t in rec.tweaks)
    assert "MT/s" in rec.headroom_note


def test_single_channel_is_detected(db):
    profile = make_profile(ram_gb=16.0, ram_sticks=[MemoryStick(16.0, 3200, "A1")], ram_speed_mts=3200)
    rec = recommend(db, profile, Target(preset="balanced"))
    assert any(t["id"] == "dual_channel" for t in rec.tweaks)


# -- output rendering ------------------------------------------------------

def test_user_cfg_renders_key_value_pairs_with_crlf(db):
    rec = recommend(db, make_profile(), Target(preset="balanced"))
    text = writer.render_user_cfg(rec)
    assert "\r\n" in text
    body = [ln for ln in text.replace("\r\n", "\n").splitlines() if ln and not ln.startswith("#")]
    assert body
    for line in body:
        assert len(line.split(" ", 1)) == 2


def test_user_cfg_refuses_a_wrong_extension(db, tmp_path):
    rec = recommend(db, make_profile(), Target(preset="balanced"))
    with pytest.raises(ValueError, match="User.cfg"):
        writer.write_user_cfg(rec, tmp_path / "User.cfg.txt")


def test_profsave_plan_only_touches_existing_keys(db):
    rec = recommend(db, make_profile(), Target(preset="esports"))
    existing = {"GstRender.ShadowQuality": "3", "GstRender.MotionBlurWorld": "0.5", "Unrelated.Key": "7"}
    plan = writer.profsave_plan(rec, existing)
    keys = {key for key, _, _ in plan}
    assert keys <= set(existing)
    assert "GstRender.ShadowQuality" in keys


def test_report_and_json_render(db):
    rec = recommend(db, make_profile(), Target(preset="quality"))
    report = writer.render_report(rec)
    assert "DETECTED HARDWARE" in report and "USER.CFG" in report
    import json

    payload = json.loads(writer.render_json(rec))
    assert payload["prediction"]["bottleneck"] in ("CPU", "GPU", "balanced")


def test_every_preset_produces_output(db):
    for preset in ("esports", "competitive", "balanced", "quality"):
        rec = recommend(db, make_profile(), Target(preset=preset))
        assert rec.settings and rec.cfg and rec.frame_cap >= 60


def test_unknown_preset_rejected(db):
    with pytest.raises(ValueError):
        recommend(db, make_profile(), Target(preset="ultra-mega"))


# -- CPU generation inference and memory verdicts ---------------------------
# Added after a real machine (Ryzen 7 9850X3D, DDR5-4800) was scored as an
# unknown CPU and as having healthy memory. Both were wrong, and together they
# flipped the reported bottleneck.

from bf6tuner.engine import infer_generation, memory_verdict  # noqa: E402


@pytest.mark.parametrize(
    "name,expected",
    [
        ("AMD Ryzen 7 9850X3D 8-Core Processor", "zen5"),
        ("AMD Ryzen 5 7600 6-Core Processor", "zen4"),
        ("AMD Ryzen 9 5900X 12-Core Processor", "zen3"),
        ("13th Gen Intel(R) Core(TM) i7-13700K", "raptorlake"),
        ("Intel(R) Core(TM) i5-12400", "alderlake"),
        ("Intel(R) Core(TM) Ultra 9 285K", "arrowlake"),
        ("Some Unbranded CPU", "unknown"),
    ],
)
def test_generation_inferred_from_the_model_number(db, name, expected):
    assert infer_generation(name, db) == expected


def test_unlisted_modern_cpu_is_not_scored_as_ancient(db):
    modern = match_cpu(make_profile(cpu_name="AMD Ryzen 7 9950X3D2 16-Core Processor",
                                    cores=16, threads=32, max_clock_ghz=5.7), db)
    old = match_cpu(make_profile(cpu_name="AMD Ryzen 7 2700X Eight-Core Processor",
                                 cores=8, threads=16, max_clock_ghz=4.3), db)
    assert modern["score"] > old["score"] * 1.5


def test_x3d_naming_is_worth_something_in_the_heuristic(db):
    with_cache = match_cpu(make_profile(cpu_name="AMD Ryzen 5 9650X3D 6-Core Processor",
                                        cores=6, threads=12, max_clock_ghz=5.2), db)
    without = match_cpu(make_profile(cpu_name="AMD Ryzen 5 9650X 6-Core Processor",
                                     cores=6, threads=12, max_clock_ghz=5.2), db)
    assert not with_cache["matched"] and not without["matched"]
    assert with_cache["score"] > without["score"]


@pytest.mark.parametrize(
    "speed,generation,underclocked",
    [
        (4800, "DDR5", True),    # JEDEC default - EXPO never switched on
        (5200, "DDR5", True),
        (6000, "DDR5", False),
        (6400, "DDR5", False),
        (2133, "DDR4", True),    # JEDEC default - XMP never switched on
        (3000, "DDR4", False),
        (3600, "DDR4", False),
    ],
)
def test_memory_verdict_understands_both_ddr_generations(db, speed, generation, underclocked):
    verdict = memory_verdict(make_profile(ram_speed_mts=speed))
    assert verdict["generation"] == generation
    assert verdict["underclocked"] is underclocked


def test_ddr5_at_jedec_speed_raises_the_expo_check(db):
    """The old 'below 3000 MT/s' rule called DDR5-4800 healthy, which is backwards."""
    profile = make_profile(
        cpu_name="AMD Ryzen 7 9850X3D 8-Core Processor", ram_speed_mts=4800, ram_gb=64.0,
        ram_sticks=[MemoryStick(32, 4800, "A1"), MemoryStick(32, 4800, "B1")],
    )
    rec = recommend(db, profile, Target(preset="competitive"))
    assert any(t["id"] == "xmp_expo" for t in rec.tweaks)
    expo = next(t for t in rec.tweaks if t["id"] == "xmp_expo")
    assert "6000" in expo["why"], "the advice should name the speed to aim for"
    assert "4800" in expo["why"]


def test_unknown_memory_speed_is_not_treated_as_a_fault(db):
    verdict = memory_verdict(make_profile(ram_speed_mts=0))
    assert verdict["underclocked"] is False and verdict["penalty"] == 1.0
