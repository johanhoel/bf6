"""Tests for benchmark.py's pure logic - CSV parsing, percentile math, and
recording storage. Nothing here launches a real PresentMon: that can't be
exercised in CI (no game running, no ETW capture permissions on a GitHub
Actions runner), so run_capture's subprocess invocation is covered only by
mocking subprocess.run to hit its error-handling paths.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bf6tuner import benchmark  # noqa: E402


def write_csv(tmp_path: Path, header: str, rows: list[str]) -> Path:
    path = tmp_path / "capture.csv"
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


# -- CSV parsing --------------------------------------------------------------

def test_parses_the_classic_column_name(tmp_path):
    # 100 frames at a steady 10ms (100 FPS), then 5 slow ones for a 1% low.
    rows = ["10.0"] * 95 + ["50.0"] * 5
    path = write_csv(tmp_path, "MsBetweenPresents", rows)
    stats = benchmark.parse_csv(path)
    assert stats.sample_count == 100
    assert stats.avg_fps == pytest.approx(1000 / stats.avg_frame_ms)
    assert stats.low_1pct_fps == pytest.approx(1000 / 50.0)


def test_matches_column_name_case_insensitively_with_other_columns_present(tmp_path):
    path = write_csv(
        tmp_path, "Application,ProcessID,msbetweenpresents,Dropped",
        ["bf6.exe,1234,16.6,0", "bf6.exe,1234,16.7,0"],
    )
    stats = benchmark.parse_csv(path)
    assert stats.sample_count == 2


def test_falls_back_to_a_fuzzy_column_match(tmp_path):
    path = write_csv(tmp_path, "MsBetweenDisplayChange", ["16.6", "16.7"])
    stats = benchmark.parse_csv(path)
    assert stats.sample_count == 2


def test_unrecognised_columns_raise_a_clear_error_listing_them(tmp_path):
    path = write_csv(tmp_path, "SomeUnrelatedColumn,Another", ["1,2"])
    with pytest.raises(benchmark.BenchmarkError, match="SomeUnrelatedColumn"):
        benchmark.parse_csv(path)


def test_non_numeric_and_zero_rows_are_skipped_not_fatal(tmp_path):
    path = write_csv(tmp_path, "MsBetweenPresents", ["16.6", "not a number", "0", "", "16.7"])
    stats = benchmark.parse_csv(path)
    assert stats.sample_count == 2


def test_a_file_with_only_bad_rows_raises_a_clear_error(tmp_path):
    path = write_csv(tmp_path, "MsBetweenPresents", ["0", "not a number", ""])
    with pytest.raises(benchmark.BenchmarkError, match="no usable frame times"):
        benchmark.parse_csv(path)


def test_missing_file_raises_benchmarkerror_not_a_raw_oserror(tmp_path):
    with pytest.raises(benchmark.BenchmarkError):
        benchmark.parse_csv(tmp_path / "does-not-exist.csv")


# -- percentile math ------------------------------------------------------

def test_1pct_low_is_the_average_of_the_slowest_1_percent():
    # 99 frames at 10ms, 1 frame at 100ms -> the 1% low is exactly the
    # single slowest frame.
    frame_times = [10.0] * 99 + [100.0]
    low = benchmark._low_fps(frame_times, 0.01)
    assert low == pytest.approx(1000 / 100.0)


def test_low_fps_rounds_up_to_at_least_one_frame_on_a_small_sample():
    # 0.1% of 10 frames rounds to 0 frames without the max(1, ...) floor.
    frame_times = [10.0] * 9 + [50.0]
    low = benchmark._low_fps(frame_times, 0.001)
    assert low is not None


def test_low_fps_of_an_empty_list_is_none():
    assert benchmark._low_fps([], 0.01) is None


# -- arg construction -------------------------------------------------------

def test_build_args_substitutes_process_output_and_duration(tmp_path):
    exe = tmp_path / "PresentMon.exe"
    output = tmp_path / "out.csv"
    args = benchmark.build_args(exe, "bf6.exe", output, 60)
    assert args[0] == str(exe)
    assert "bf6.exe" in args
    assert str(output) in args
    assert "60" in args


def test_build_args_respects_a_custom_template(tmp_path):
    exe = tmp_path / "PresentMon.exe"
    args = benchmark.build_args(exe, "bf6.exe", tmp_path / "out.csv", 30,
                                template="-p {process} -o {output} -t {duration}")
    assert args == [str(exe), "-p", "bf6.exe", "-o", str(tmp_path / "out.csv"), "-t", "30"]


# -- run_capture error handling (mocked subprocess) --------------------------

def test_run_capture_raises_on_nonzero_exit(tmp_path, monkeypatch):
    def fake_run(*a, **k):
        return subprocess.CompletedProcess(a, returncode=1, stdout="", stderr="bad flag")
    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(benchmark.BenchmarkError, match="bad flag"):
        benchmark.run_capture(tmp_path / "PresentMon.exe", "bf6.exe", tmp_path / "out.csv", 5)


def test_run_capture_raises_if_no_csv_was_produced(tmp_path, monkeypatch):
    def fake_run(*a, **k):
        return subprocess.CompletedProcess(a, returncode=0, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(benchmark.BenchmarkError, match="did not write"):
        benchmark.run_capture(tmp_path / "PresentMon.exe", "bf6.exe", tmp_path / "out.csv", 5)


def test_run_capture_raises_a_clear_error_on_timeout(tmp_path, monkeypatch):
    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="PresentMon", timeout=35)
    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(benchmark.BenchmarkError, match="did not finish"):
        benchmark.run_capture(tmp_path / "PresentMon.exe", "bf6.exe", tmp_path / "out.csv", 5)


def test_run_capture_succeeds_when_process_exits_clean_and_csv_exists(tmp_path, monkeypatch):
    output = tmp_path / "out.csv"
    def fake_run(*a, **k):
        output.write_text("MsBetweenPresents\n16.6\n")
        return subprocess.CompletedProcess(a, returncode=0, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    benchmark.run_capture(tmp_path / "PresentMon.exe", "bf6.exe", output, 5)  # no raise


# -- find_presentmon ----------------------------------------------------------

def test_find_presentmon_prefers_an_explicit_path_that_exists(tmp_path):
    exe = tmp_path / "PresentMon.exe"
    exe.write_bytes(b"")
    assert benchmark.find_presentmon(explicit=exe) == exe


def test_find_presentmon_ignores_an_explicit_path_that_does_not_exist(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert benchmark.find_presentmon(explicit=tmp_path / "missing.exe") is None


# -- Recording storage --------------------------------------------------------

def make_recording(**overrides) -> "benchmark.Recording":
    stats = benchmark.FrameStats(
        sample_count=1000, duration_s=16.6, avg_fps=165.0,
        low_1pct_fps=140.0, low_01pct_fps=120.0, avg_frame_ms=6.06, max_frame_ms=12.0,
    )
    base = dict(
        stats=stats, taken_at="2026-09-07T12:00:00Z", preset="competitive",
        predicted_fps=168, gpu_fps=200, cpu_fps=168, bottleneck="CPU",
        resolution="2560x1440", refresh_hz=165, cpu_name="Ryzen 7 7800X3D",
        gpu_name="RTX 4070 SUPER",
    )
    base.update(overrides)
    return benchmark.Recording(**base)


def test_recording_round_trips_through_dict():
    original = make_recording()
    restored = benchmark.Recording.from_dict(original.to_dict())
    assert restored == original


def test_recording_delta_fps_is_measured_minus_predicted():
    recording = make_recording(predicted_fps=150)
    assert recording.delta_fps == round(recording.stats.avg_fps) - 150


def test_save_and_list_recordings_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    recording = make_recording(label="Tournament run")
    path = benchmark.save_recording(recording)
    assert path.is_file()

    listed = benchmark.list_recordings()
    assert len(listed) == 1
    listed_path, listed_recording = listed[0]
    assert listed_path == path
    assert listed_recording == recording


def test_list_recordings_is_newest_first(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    import time
    benchmark.save_recording(make_recording(label="first"))
    time.sleep(1.1)  # filenames are second-resolution timestamps
    benchmark.save_recording(make_recording(label="second"))
    listed = benchmark.list_recordings()
    assert [r.label for _, r in listed] == ["second", "first"]


def test_corrupt_recording_file_is_skipped_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    benchmark.save_recording(make_recording())
    (benchmark.benchmark_root() / "broken.json").write_text("{ not json")
    listed = benchmark.list_recordings()
    assert len(listed) == 1


def test_delete_recording_removes_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = benchmark.save_recording(make_recording())
    benchmark.delete_recording(path)
    assert not path.exists()
    assert benchmark.list_recordings() == []


def test_deleting_a_missing_recording_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    benchmark.delete_recording(benchmark.benchmark_root() / "never-existed.json")
