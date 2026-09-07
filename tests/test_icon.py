"""Tests for the pure-Python icon renderer (moved from packaging/make_icon.py
into bf6tuner.icon so the running app can reuse it - see ARCHITECTURE.md).

No image library is used to build or to check these, on purpose - same
"no binary blob, no extra dependency" policy as the renderer itself, so
these parse the raw PNG/ICO structure with struct instead.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bf6tuner import icon  # noqa: E402


def test_render_png_has_a_valid_signature_and_the_requested_dimensions():
    blob = icon.render_png(32)
    assert blob.startswith(b"\x89PNG\r\n\x1a\n")
    # IHDR is the first chunk: length(4) + b"IHDR" + width(4) + height(4) + ...
    width, height = struct.unpack(">II", blob[16:24])
    assert (width, height) == (32, 32)


def test_render_png_ends_with_an_iend_chunk():
    blob = icon.render_png(16)
    assert blob.endswith(b"IEND\xae\x42\x60\x82")


def test_build_ico_contains_every_declared_size(tmp_path):
    destination = tmp_path / "bf6tuner.ico"
    result = icon.build_ico(destination)

    assert result == destination
    assert destination.is_file()

    data = destination.read_bytes()
    reserved, image_type, count = struct.unpack("<HHH", data[:6])
    assert reserved == 0
    assert image_type == 1  # ICO, not CUR
    assert count == len(icon.SIZES)

    # Each 16-byte directory entry's declared width/height (0 means 256, per
    # the ICO format) should match bf6tuner.icon.SIZES.
    declared_sizes = set()
    for i in range(count):
        entry = data[6 + i * 16 : 6 + (i + 1) * 16]
        width, height = entry[0], entry[1]
        declared_sizes.add(256 if width == 0 else width)
        assert (256 if height == 0 else height) == (256 if width == 0 else width)
    assert declared_sizes == set(icon.SIZES)


def test_build_ico_creates_missing_parent_directories(tmp_path):
    destination = tmp_path / "nested" / "dir" / "bf6tuner.ico"
    icon.build_ico(destination)
    assert destination.is_file()
