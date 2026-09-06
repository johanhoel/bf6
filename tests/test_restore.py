"""Tests for backups and recovery."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bf6tuner import database, writer  # noqa: E402
from bf6tuner.engine import Target, recommend  # noqa: E402
from bf6tuner.hardware import HardwareProfile, MemoryStick  # noqa: E402


@pytest.fixture(scope="session")
def db():
    return database.load()


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """An isolated APPDATA plus a fake game folder and profile."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

    game = tmp_path / "Battlefield 6"
    game.mkdir()
    user_cfg = game / "User.cfg"
    user_cfg.write_text("GameTime.MaxVariableFps 0\r\nMy.Custom 1\r\n", newline="")

    settings = tmp_path / "Documents" / "Battlefield 6" / "settings"
    settings.mkdir(parents=True)
    profsave = settings / "PROFSAVE_profile"
    profsave.write_text(
        "GstRender.ShadowQuality 3\r\nGstRender.MotionBlurWorld 0.500000\r\n"
        "GstRender.EffectsQuality 3\r\nSome.Untouched.Key 5\r\n", newline="",
    )
    return {"user_cfg": user_cfg, "profsave": profsave, "root": tmp_path}


@pytest.fixture
def rec(db):
    profile = HardwareProfile(
        cpu_name="AMD Ryzen 7 7800X3D 8-Core Processor", cpu_vendor="amd",
        cores=8, threads=16, p_cores=8, e_cores=0, max_clock_ghz=5.0,
        gpu_name="NVIDIA GeForce RTX 4070 SUPER", gpu_vendor="nvidia", vram_gb=12.0,
        ram_gb=32.0, ram_speed_mts=6000,
        ram_sticks=[MemoryStick(16.0, 6000, "A1"), MemoryStick(16.0, 6000, "B1")],
        width=2560, height=1440, refresh_hz=165, max_refresh_hz=165, detected=True,
    )
    return recommend(db, profile, Target(preset="competitive"))


def targets(sandbox):
    return {"user_cfg": sandbox["user_cfg"], "profsave": sandbox["profsave"]}


# -- restore points ----------------------------------------------------------

def test_restore_point_captures_both_files_together(sandbox):
    point = writer.create_restore_point(targets(sandbox), "test")
    assert point is not None
    assert set(point.roles) == {"user_cfg", "profsave"}
    assert (point.directory / "manifest.json").is_file()
    assert (point.directory / "User.cfg").read_text() == sandbox["user_cfg"].read_text()


def test_restore_points_are_listed_newest_first(sandbox):
    import datetime

    root = writer.backup_root()
    for hour, label in ((9, "morning"), (17, "evening"), (13, "midday")):
        moment = datetime.datetime(2026, 5, 4, hour, 0, 0)
        directory = root / f"{moment:%Y%m%d-%H%M%S}_{label}"
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_text(
            f'{{"created": "{moment.isoformat()}", "label": "{label}", "files": []}}'
        )
    assert [p.label for p in writer.list_restore_points()] == ["evening", "midday", "morning"]


def test_nothing_to_snapshot_returns_none():
    assert writer.create_restore_point({"user_cfg": None, "profsave": None}, "empty") is None


def test_corrupt_restore_point_is_skipped_not_fatal(sandbox):
    writer.create_restore_point(targets(sandbox), "good")
    bad = writer.backup_root() / "20200101-000000_broken"
    bad.mkdir(parents=True)
    (bad / "manifest.json").write_text("{ not json")
    assert len(writer.list_restore_points()) == 1


# -- round trip --------------------------------------------------------------

def test_apply_then_restore_returns_both_files_exactly(sandbox, rec):
    original_cfg = sandbox["user_cfg"].read_bytes()
    original_prof = sandbox["profsave"].read_bytes()

    point = writer.create_restore_point(targets(sandbox), "before")
    writer.write_user_cfg(rec, sandbox["user_cfg"], restore_point=point)
    writer.write_profsave(rec, sandbox["profsave"], restore_point=point)
    assert sandbox["user_cfg"].read_bytes() != original_cfg
    assert sandbox["profsave"].read_bytes() != original_prof

    results = writer.restore_from(point)
    assert len(results) == 2
    assert sandbox["user_cfg"].read_bytes() == original_cfg
    assert sandbox["profsave"].read_bytes() == original_prof


def test_restoring_deletes_a_file_that_did_not_exist(sandbox, rec):
    sandbox["user_cfg"].unlink()
    point = writer.create_restore_point(targets(sandbox), "no user.cfg")
    writer.write_user_cfg(rec, sandbox["user_cfg"], restore_point=point)
    assert sandbox["user_cfg"].is_file()

    writer.restore_from(point)
    assert not sandbox["user_cfg"].exists(), "an undo must remove a file the app created"


def test_restore_can_target_a_single_role(sandbox, rec):
    original_prof = sandbox["profsave"].read_bytes()
    point = writer.create_restore_point(targets(sandbox), "before")
    writer.write_user_cfg(rec, sandbox["user_cfg"], restore_point=point)
    writer.write_profsave(rec, sandbox["profsave"], restore_point=point)

    writer.restore_from(point, roles=["profsave"])
    assert sandbox["profsave"].read_bytes() == original_prof
    assert "BF6 Tuner" in sandbox["user_cfg"].read_text()


def test_restore_survives_a_missing_backup_file(sandbox):
    point = writer.create_restore_point(targets(sandbox), "before")
    (point.directory / "User.cfg").unlink()
    results = writer.restore_from(point)
    assert any("missing from the backup folder" in r.message for r in results)


# -- writing behaviour -------------------------------------------------------

def test_profsave_write_leaves_unmanaged_keys_alone(sandbox, rec):
    writer.write_profsave(rec, sandbox["profsave"])
    assert "Some.Untouched.Key 5" in sandbox["profsave"].read_text()


def test_profsave_write_applies_the_unit_scale(sandbox, rec):
    writer.write_profsave(rec, sandbox["profsave"])
    assert "GstRender.MotionBlurWorld 0.000000" in sandbox["profsave"].read_text()


def test_numerically_equal_values_are_not_treated_as_changes(rec):
    existing = {"GstRender.ShadowQuality": "1", "GstRender.MotionBlurWorld": "0.000000"}
    plan = writer.profsave_plan(rec, existing)
    assert all(key != "GstRender.MotionBlurWorld" for key, _, _ in plan)


def test_writing_without_a_restore_point_still_backs_up(sandbox, rec):
    result = writer.write_user_cfg(rec, sandbox["user_cfg"])
    assert result.backup is not None and result.backup.is_file()


# -- pruning -----------------------------------------------------------------

def test_pruning_keeps_the_newest(sandbox):
    import datetime

    root = writer.backup_root()
    for index in range(6):
        moment = datetime.datetime(2026, 1, 1, 12, index)
        directory = root / f"{moment:%Y%m%d-%H%M%S}_point{index}"
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_text(
            f'{{"created": "{moment.isoformat()}", "label": "p{index}", "files": []}}'
        )
    removed = writer.prune_restore_points(keep=2)
    assert removed == 4
    remaining = writer.list_restore_points()
    assert len(remaining) == 2
    assert remaining[0].label == "p5"
