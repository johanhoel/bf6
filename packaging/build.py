"""One-shot build: encrypt the database, generate the icon, produce BF6Tuner.exe.

Run it from anywhere:

    python packaging/build.py                 # normal build
    python packaging/build.py --obfuscate     # additionally run PyArmor over the source
    python packaging/build.py --bundle-only   # just produce the encrypted database

Produces two binaries from one analysis. Windows fixes whether an executable is
a GUI or a console program at link time, so BF6Tuner.exe is the double-clickable
GUI (no console window) and BF6Tuner-cli.exe is the same application with a
console attached, which is what makes stdout, exit codes and output redirection
work from a terminal.

What "encrypted" means here, stated plainly: the settings database is AES-256-GCM
encrypted and the executable verifies its integrity tag before using it, so the
data cannot be read or edited with a text editor and a tampered bundle refuses to
load. The key is inside the binary, because the binary must decrypt without a
server. That defeats copying and casual modification. It does not defeat a
debugger, and no client-side scheme can. --obfuscate raises the bar further by
compiling the Python source through PyArmor before packaging.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
DATA = ROOT / "data"
BUILD = ROOT / "packaging" / "_build"
DIST = ROOT / "dist"

sys.path.insert(0, str(SRC))
sys.path.insert(0, str(ROOT / "packaging"))

from bf6tuner import __version__, crypto  # noqa: E402
from bf6tuner.database import DATASETS  # noqa: E402
from make_icon import build_ico  # noqa: E402


def log(message: str) -> None:
    print(f"[build] {message}", flush=True)


def prepare_bundle() -> tuple[Path, bytes]:
    payload: dict[str, object] = {}
    for name in DATASETS:
        path = DATA / f"{name}.json"
        if not path.is_file():
            raise SystemExit(f"Missing database source: {path}")
        payload[name] = json.loads(path.read_text(encoding="utf-8"))

    key = crypto.new_key()
    header = {"version": __version__, "datasets": list(DATASETS)}
    blob = crypto.pack(payload, key, header)

    BUILD.mkdir(parents=True, exist_ok=True)
    bundle = BUILD / "bf6tuner.db"
    bundle.write_bytes(blob)

    keyring = SRC / "bf6tuner" / "_keyring.py"
    keyring.write_text(
        '"""Generated at build time. Not checked in; regenerated on every build."""\n\n'
        f'BUNDLE_KEY = "{base64.b64encode(key).decode()}"\n',
        encoding="utf-8",
    )
    log(f"encrypted database -> {bundle} ({len(blob):,} bytes, key regenerated)")

    # Fail loudly here rather than in front of a user.
    restored, restored_header = crypto.unpack(bundle.read_bytes(), key)
    assert set(restored) == set(DATASETS) and restored_header["version"] == __version__
    log("bundle verified: decrypts, passes its integrity tag, contains every dataset")
    return bundle, key


def write_build_info() -> Path:
    """Record the commit this build was made from, so the running app can ask
    GitHub whether `main` has moved on since (see bf6tuner.update)."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True,
            text=True, timeout=10, check=True,
        ).stdout.strip()
    except Exception:
        commit = ""
    built_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    path = SRC / "bf6tuner" / "_build_info.py"
    path.write_text(
        '"""Generated at build time. Not checked in; regenerated on every build."""\n\n'
        f'GIT_COMMIT = "{commit}"\n'
        f'BUILT_AT = "{built_at}"\n',
        encoding="utf-8",
    )
    log(f"build info -> commit {commit[:7] or 'unknown'}, built {built_at}")
    return path


def write_version_info() -> Path:
    parts = (__version__.split(".") + ["0", "0", "0"])[:4]
    numbers = ", ".join(str(int(p)) for p in parts)
    path = BUILD / "version_info.txt"
    path.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({numbers}), prodvers=({numbers}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', 'BF6 Tuner - Battlefield 6 settings configurator'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', 'BF6Tuner'),
      StringStruct('OriginalFilename', 'BF6Tuner.exe'),
      StringStruct('ProductName', 'BF6 Tuner'),
      StringStruct('ProductVersion', '{__version__}'),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
        encoding="utf-8",
    )
    return path


def run_pyarmor() -> Path:
    """Obfuscate the package with PyArmor and return the source root to package from."""
    if shutil.which("pyarmor") is None:
        raise SystemExit(
            "--obfuscate needs PyArmor. Install it with: pip install pyarmor\n"
            "Or drop the flag; the database stays encrypted either way."
        )
    output = BUILD / "obfuscated"
    if output.exists():
        shutil.rmtree(output)
    log("running PyArmor over src/bf6tuner ...")
    subprocess.run(
        ["pyarmor", "gen", "--output", str(output), "--recursive", str(SRC / "bf6tuner")],
        check=True, cwd=str(ROOT),
    )
    log(f"obfuscated source -> {output}")
    return output


def run_pyinstaller(source_root: Path, clean: bool) -> list[Path]:
    command = [
        sys.executable, "-m", "PyInstaller",
        str(ROOT / "packaging" / "bf6tuner.spec"),
        "--distpath", str(DIST),
        "--workpath", str(BUILD / "work"),
        "--noconfirm",
    ]
    if clean:
        command.append("--clean")
    # Prepend rather than replace: clobbering PYTHONPATH breaks the build on any
    # machine that already has one set.
    existing = os.environ.get("PYTHONPATH", "")
    environment = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join([str(source_root)] + ([existing] if existing else [])),
    )
    log("running PyInstaller ...")
    subprocess.run(command, check=True, cwd=str(ROOT), env=environment)

    suffix = ".exe" if os.name == "nt" else ""
    built = [DIST / f"BF6Tuner{suffix}", DIST / f"BF6Tuner-cli{suffix}"]
    missing = [path for path in built if not path.is_file()]
    if missing:
        raise SystemExit(
            "PyInstaller finished but these are missing: "
            + ", ".join(str(path) for path in missing)
        )
    return built


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build BF6Tuner.exe")
    parser.add_argument("--obfuscate", action="store_true",
                        help="Run PyArmor over the source before packaging.")
    parser.add_argument("--bundle-only", action="store_true",
                        help="Only produce the encrypted database bundle.")
    parser.add_argument("--no-clean", action="store_true", help="Reuse PyInstaller's cache.")
    args = parser.parse_args(argv)

    if os.name != "nt":
        log("WARNING: not running on Windows. PyInstaller builds for the host platform "
            "only, so this will not produce a Windows .exe. Use the GitHub Actions "
            "workflow or run this on Windows.")

    bundle, _ = prepare_bundle()
    if args.bundle_only:
        return 0

    build_ico(BUILD / "bf6tuner.ico")
    log(f"icon -> {BUILD / 'bf6tuner.ico'}")
    write_version_info()
    write_build_info()

    source_root = run_pyarmor() if args.obfuscate else SRC
    built = run_pyinstaller(source_root, clean=not args.no_clean)

    for path in built:
        log(f"done: {path} ({path.stat().st_size / (1024 * 1024):.1f} MB)")
    log("BF6Tuner is the GUI; BF6Tuner-cli is the same app with a working console.")
    log("The database is encrypted inside the executable; no editable JSON ships with it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
