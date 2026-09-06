"""PyInstaller entry point. Kept free of relative imports so it can be run as a script."""

import sys

from bf6tuner.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
