"""Generates the application icon at build time.

Drawn in pure Python so there is no binary blob in the repository and no image
library in the build requirements. Produces a 256/128/64/48/32/16 px .ico.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

BG = (17, 19, 24, 255)
RING = (76, 141, 255, 255)
TICK = (230, 233, 239, 255)
SIZES = (256, 128, 64, 48, 32, 16)


def _blend(base, over, alpha):
    return tuple(int(b + (o - b) * alpha) for b, o in zip(base[:3], over[:3])) + (255,)


def _render(size: int) -> bytes:
    """A reticle: rounded dark tile, blue ring, four ticks, centre dot."""
    s = size
    radius = s * 0.20          # corner radius of the tile
    ring_r = s * 0.315
    ring_w = max(1.0, s * 0.055)
    dot_r = max(1.0, s * 0.052)
    tick_inner, tick_outer = s * 0.36, s * 0.46
    tick_w = max(1.0, s * 0.055)
    cx = cy = (s - 1) / 2.0

    rows = bytearray()
    for y in range(s):
        rows.append(0)  # PNG filter type 0
        for x in range(s):
            px, py = x + 0.5, y + 0.5

            # Rounded-rectangle mask with a soft edge.
            dx = max(radius - px, px - (s - radius), 0.0)
            dy = max(radius - py, py - (s - radius), 0.0)
            corner = math.hypot(dx, dy)
            tile_alpha = min(1.0, max(0.0, (radius - corner) + 0.5)) if corner > 0 else 1.0
            if tile_alpha <= 0.0:
                rows += b"\x00\x00\x00\x00"
                continue

            colour = BG
            dist = math.hypot(px - cx, py - cy)

            ring_edge = abs(dist - ring_r)
            if ring_edge < ring_w / 2 + 0.5:
                colour = _blend(colour, RING, min(1.0, ring_w / 2 + 0.5 - ring_edge))

            for axis_distance, cross in (
                (abs(px - cx), abs(py - cy)), (abs(py - cy), abs(px - cx)),
            ):
                if cross < tick_w / 2 + 0.5 and tick_inner <= axis_distance <= tick_outer:
                    colour = _blend(colour, TICK, min(1.0, tick_w / 2 + 0.5 - cross))

            if dist < dot_r + 0.5:
                colour = _blend(colour, RING, min(1.0, dot_r + 0.5 - dist))

            rows += bytes(colour[:3]) + bytes([int(255 * tile_alpha)])

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", s, s, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )


def build_ico(destination: Path) -> Path:
    images = [(size, _render(size)) for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    directory, blobs = b"", b""
    for size, png in images:
        directory += struct.pack(
            "<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset
        )
        blobs += png
        offset += len(png)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(header + directory + blobs)
    return destination


if __name__ == "__main__":
    import sys

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("build/bf6tuner.ico")
    print(f"Wrote {build_ico(out)} ({out.stat().st_size} bytes)")
