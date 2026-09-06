"""Entry point. Launches the GUI, or the CLI when arguments are supplied."""

from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) > 1:
        from .cli import main as cli_main

        return cli_main()

    try:
        from .ui.app import run
    except ImportError as exc:
        print(
            f"The graphical interface could not start ({exc}).\n"
            "Install it with:  pip install PySide6\n"
            "Or run headless:  python -m bf6tuner --preset balanced",
            file=sys.stderr,
        )
        return 1
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
