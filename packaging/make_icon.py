"""Generates the application icon at build time.

The actual pixel-drawing logic lives in ``bf6tuner.icon`` so the *running*
app can reuse it for its own window/taskbar icon (a source checkout should
look the same as the shipped .exe, not show a generic Python icon). This
module stays as a thin wrapper purely so `python packaging/make_icon.py` and
`packaging/build.py`'s `from make_icon import build_ico` keep working
unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bf6tuner.icon import build_ico  # noqa: E402,F401


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("build/bf6tuner.ico")
    print(f"Wrote {build_ico(out)} ({out.stat().st_size} bytes)")
