"""Tests for the diagnostics export bundle.

Only the pure text-assembly logic is exercised here - the two Windows-
security reads (_core_isolation_state/_recent_code_integrity_blocks) are
themselves read-only registry/event-log queries with nothing to unit test
beyond "does this machine's registry say X," which is exactly what they
were built to answer live (see ARCHITECTURE.md's Core Isolation
investigation) - so they're monkeypatched here to keep the rest
deterministic across machines.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bf6tuner import database, diagnostics  # noqa: E402
from bf6tuner.engine import Target, recommend  # noqa: E402
from bf6tuner.hardware import HardwareProfile, MemoryStick  # noqa: E402
from bf6tuner.paths import GamePaths  # noqa: E402


@pytest.fixture(scope="session")
def db():
    return database.load()


@pytest.fixture(autouse=True)
def _stub_windows_checks(monkeypatch):
    monkeypatch.setattr(diagnostics, "_core_isolation_state", lambda: "off")
    monkeypatch.setattr(diagnostics, "_recent_code_integrity_blocks", lambda: "none found in the event log")


def make_profile(**overrides) -> HardwareProfile:
    base = dict(
        cpu_name="AMD Ryzen 7 9850X3D 8-Core Processor", cpu_vendor="amd",
        cores=8, threads=16, p_cores=8, e_cores=0, hybrid=False, max_clock_ghz=5.0,
        gpu_name="NVIDIA GeForce RTX 5090", gpu_vendor="nvidia", vram_gb=31.8,
        ram_gb=61.6, ram_speed_mts=6000, ram_sticks=[MemoryStick(32.0, 6000, "A1")],
        width=5120, height=1440, refresh_hz=144, max_refresh_hz=144, detected=True,
        os_name="Windows 11 Pro", os_build="26200",
    )
    base.update(overrides)
    return HardwareProfile(**base)


def test_report_includes_hardware_and_matched_db_entries(db):
    profile = make_profile()
    rec = recommend(db, profile, Target(preset="competitive"))
    report = diagnostics.build_report(
        profile, rec, GamePaths(), None, setting_override_count=0, cfg_override_count=0,
        local_commit="abc1234",
    )
    assert "AMD Ryzen 7 9850X3D" in report
    assert "NVIDIA GeForce RTX 5090" in report
    assert "matched database entry 'rtx 5090'" in report
    assert "matched database entry 'ryzen 7 9850x3d'" in report
    assert "abc1234" in report


def test_report_flags_an_unmatched_gpu_as_using_the_fallback(db):
    profile = make_profile(gpu_name="Some GPU Nobody Has Heard Of")
    rec = recommend(db, profile, Target(preset="competitive"))
    report = diagnostics.build_report(
        profile, rec, GamePaths(), None, setting_override_count=0, cfg_override_count=0,
        local_commit="abc1234",
    )
    assert "GPU: not found in database - using the fallback heuristic" in report


def test_report_handles_no_recommendation_yet(db):
    """The app can export diagnostics before hardware detection finishes -
    rec is None in that window, and this must not crash."""
    profile = HardwareProfile()
    report = diagnostics.build_report(
        profile, None, GamePaths(), None, setting_override_count=0, cfg_override_count=0,
        local_commit="",
    )
    assert "no recommendation computed yet" in report
    assert "unknown" in report  # commit falls back to this


def test_report_shows_located_files_and_presentmon(db, tmp_path):
    profile = make_profile()
    rec = recommend(db, profile, Target(preset="competitive"))
    game = GamePaths(
        install_dir=tmp_path / "Battlefield 6",
        user_cfg=tmp_path / "Battlefield 6" / "User.cfg",
        profsave=tmp_path / "PROFSAVE_profile",
    )
    presentmon = tmp_path / "PresentMon.exe"
    report = diagnostics.build_report(
        profile, rec, game, presentmon, setting_override_count=3, cfg_override_count=1,
        local_commit="abc1234",
    )
    assert str(game.install_dir) in report
    assert str(presentmon) in report
    assert "Setting overrides: 3, User.cfg overrides: 1" in report


def test_report_never_omits_the_share_caution(db):
    profile = make_profile()
    rec = recommend(db, profile, Target(preset="competitive"))
    report = diagnostics.build_report(
        profile, rec, GamePaths(), None, setting_override_count=0, cfg_override_count=0,
        local_commit="abc1234",
    )
    assert "review before sharing this outside your own machine" in report
