"""Finding Battlefield 6 on disk.

Two different files, in two different places, which is the single most common
reason a User.cfg "does nothing":

* ``User.cfg``       -> the *install* folder, next to the game executable.
* ``PROFSAVE_profile`` -> ``Documents\\Battlefield 6\\settings`` (with a
  ``steam`` subfolder on the Steam build), and Documents may be redirected
  into OneDrive.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

IS_WINDOWS = os.name == "nt"

EXE_NAMES = ("bf6.exe", "battlefield6.exe", "bf6_be.exe", "battlefield_6.exe")
FOLDER_NAMES = ("Battlefield 6", "Battlefield6", "BATTLEFIELD 6")


@dataclass
class GamePaths:
    install_dir: Path | None = None
    executable: Path | None = None
    user_cfg: Path | None = None
    profsave: Path | None = None
    documents_dir: Path | None = None
    candidates: list[Path] = field(default_factory=list)
    profsave_candidates: list[Path] = field(default_factory=list)
    searched: list[Path] = field(default_factory=list)
    overridden: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)

    def search_summary(self) -> str:
        """What was looked at, so a failure is diagnosable rather than mysterious."""
        if not self.searched:
            return "No Battlefield settings folders were found to search."
        lines = [f"Searched {len(self.searched)} location(s):"]
        lines += [f"  {path}" for path in self.searched[:12]]
        if len(self.searched) > 12:
            lines.append(f"  ... and {len(self.searched) - 12} more")
        return "\n".join(lines)

    @property
    def install_drive(self) -> str:
        if self.install_dir:
            return str(self.install_dir.drive).rstrip(":").upper()
        return ""


def _expand(value: str) -> Path:
    return Path(os.path.expandvars(value))


def _steam_root() -> Path | None:
    if not IS_WINDOWS:
        return None
    try:
        import winreg
    except ImportError:  # pragma: no cover
        return None
    for hive, key in (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
    ):
        try:
            with winreg.OpenKey(hive, key) as handle:
                for value_name in ("SteamPath", "InstallPath"):
                    try:
                        path = Path(winreg.QueryValueEx(handle, value_name)[0])
                        if path.is_dir():
                            return path
                    except OSError:
                        continue
        except OSError:
            continue
    return None


def _steam_libraries() -> list[Path]:
    root = _steam_root()
    if not root:
        return []
    libraries = [root]
    vdf = root / "steamapps" / "libraryfolders.vdf"
    if vdf.is_file():
        try:
            text = vdf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return libraries
        # The VDF format changes between Steam versions; pulling every "path"
        # string out is more durable than parsing the structure.
        for match in re.finditer(r'"path"\s+"([^"]+)"', text):
            candidate = Path(match.group(1).replace("\\\\", "\\"))
            if candidate.is_dir():
                libraries.append(candidate)
    return libraries


def _registry_install_dirs() -> list[Path]:
    if not IS_WINDOWS:
        return []
    try:
        import winreg
    except ImportError:  # pragma: no cover
        return []

    found: list[Path] = []
    uninstall_keys = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    for hive, base in uninstall_keys:
        try:
            with winreg.OpenKey(hive, base) as root:
                for index in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        sub_name = winreg.EnumKey(root, index)
                        with winreg.OpenKey(root, sub_name) as sub:
                            try:
                                display = str(winreg.QueryValueEx(sub, "DisplayName")[0])
                            except OSError:
                                continue
                            if "battlefield 6" not in display.lower():
                                continue
                            for value_name in ("InstallLocation", "InstallDir"):
                                try:
                                    path = Path(str(winreg.QueryValueEx(sub, value_name)[0]))
                                except OSError:
                                    continue
                                if path.is_dir():
                                    found.append(path)
                    except OSError:
                        continue
        except OSError:
            continue

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\EA Games\Battlefield 6") as key:
            path = Path(str(winreg.QueryValueEx(key, "Install Dir")[0]))
            if path.is_dir():
                found.append(path)
    except OSError:
        pass
    return found


def _documents_dirs() -> list[Path]:
    dirs: list[Path] = []
    profile = os.environ.get("USERPROFILE") or str(Path.home())
    dirs.append(Path(profile) / "Documents")

    if IS_WINDOWS:
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            ) as key:
                raw = str(winreg.QueryValueEx(key, "Personal")[0])
                redirected = _expand(raw)
                if redirected not in dirs:
                    dirs.insert(0, redirected)
        except OSError:
            pass

    for env in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        value = os.environ.get(env)
        if value:
            dirs.append(Path(value) / "Documents")

    seen: list[Path] = []
    for d in dirs:
        if d not in seen:
            seen.append(d)
    return seen


def _profsave_roots(install_dir: Path | None) -> list[Path]:
    """Every plausible parent of a Battlefield settings folder.

    Battlefield titles have historically put PROFSAVE_profile under Documents,
    but the exact layout moves between releases and storefronts: BF2042 on Steam
    adds a 'steam' subfolder, and some EA titles nest one folder per account id.
    Rather than encode a guess, search the places it could be.
    """
    roots: list[Path] = list(_documents_dirs())
    for env in ("LOCALAPPDATA", "APPDATA", "USERPROFILE"):
        value = os.environ.get(env)
        if value:
            roots.append(Path(value))
    profile = os.environ.get("USERPROFILE")
    if profile:
        roots.append(Path(profile) / "Saved Games")
    if install_dir:
        roots.append(install_dir)
        roots.append(install_dir.parent)

    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


def _bounded_walk(base: Path, max_depth: int = 4, max_dirs: int = 400):
    """Yield directories under ``base``, depth- and count-limited.

    A settings tree is tiny, but these roots include %USERPROFILE%, so an
    unbounded walk could wander into something enormous.
    """
    queue: list[tuple[Path, int]] = [(base, 0)]
    seen = 0
    while queue and seen < max_dirs:
        current, depth = queue.pop(0)
        yield current
        seen += 1
        if depth >= max_depth:
            continue
        try:
            for child in current.iterdir():
                if child.is_dir() and not child.is_symlink():
                    queue.append((child, depth + 1))
        except OSError:
            continue


def find_profsave_files(install_dir: Path | None = None) -> tuple[list[Path], list[Path]]:
    """Return (files found, directories searched), newest file first."""
    found: list[Path] = []
    searched: list[Path] = []

    for root in _profsave_roots(install_dir):
        if not root.is_dir():
            continue
        try:
            game_dirs = [
                child for child in root.iterdir()
                if child.is_dir() and child.name.lower().replace(" ", "").startswith("battlefield")
            ]
        except OSError:
            continue

        for game_dir in game_dirs:
            # Only Battlefield 6, but tolerate naming variations like "Battlefield6".
            compact = game_dir.name.lower().replace(" ", "").replace("_", "")
            if not compact.startswith("battlefield6"):
                continue
            for directory in _bounded_walk(game_dir):
                searched.append(directory)
                try:
                    for entry in directory.iterdir():
                        if entry.is_file() and entry.name.upper().startswith("PROFSAVE"):
                            found.append(entry)
                except OSError:
                    continue

    unique: list[Path] = []
    for path in found:
        if path not in unique:
            unique.append(path)
    # Several profiles can exist (multiple EA accounts); the one the player last
    # used is the one the game wrote most recently.
    unique.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
    return unique, searched


def _find_executable(install_dir: Path) -> Path | None:
    for name in EXE_NAMES:
        direct = install_dir / name
        if direct.is_file():
            return direct
    try:
        for child in install_dir.iterdir():
            if child.is_file() and child.suffix.lower() == ".exe":
                stem = child.stem.lower()
                if "battlefield" in stem or stem.startswith("bf6"):
                    return child
    except OSError:
        pass
    return None


def discover(overrides: dict[str, str] | None = None) -> GamePaths:
    result = GamePaths()
    candidates: list[Path] = []

    for library in _steam_libraries():
        for folder in FOLDER_NAMES:
            candidates.append(library / "steamapps" / "common" / folder)

    candidates.extend(_registry_install_dirs())

    for base in (
        r"%ProgramFiles%\EA Games", r"%ProgramFiles(x86)%\EA Games",
        r"%ProgramFiles%\Origin Games", r"%ProgramFiles(x86)%\Origin Games",
        r"%ProgramFiles%\Epic Games", r"%ProgramFiles(x86)%\Epic Games",
        r"%ProgramFiles(x86)%\Steam\steamapps\common",
        r"%ProgramFiles%\Steam\steamapps\common",
    ):
        expanded = _expand(base)
        for folder in FOLDER_NAMES:
            candidates.append(expanded / folder)

    # Same relative layout on any other fixed drive, for the common case of a
    # dedicated games SSD that no registry key knows about.
    if IS_WINDOWS:
        for letter in "DEFGHIJKL":
            drive = Path(f"{letter}:\\")
            if not drive.exists():
                continue
            for tail in (
                r"SteamLibrary\steamapps\common", r"Steam\steamapps\common",
                "EA Games", "Games", "",
            ):
                for folder in FOLDER_NAMES:
                    candidates.append(drive / tail / folder if tail else drive / folder)

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_dir():
            result.candidates.append(candidate)

    for candidate in result.candidates:
        exe = _find_executable(candidate)
        if exe:
            result.install_dir = candidate
            result.executable = exe
            break
    if result.install_dir is None and result.candidates:
        result.install_dir = result.candidates[0]
        result.notes.append(
            f"Found a Battlefield 6 folder at {result.install_dir} but no game executable inside it. "
            "Confirm this is the install folder before writing User.cfg."
        )

    if result.install_dir:
        result.user_cfg = result.install_dir / "User.cfg"

    profsave_files, searched = find_profsave_files(result.install_dir)
    result.profsave_candidates = profsave_files
    result.searched = searched
    if profsave_files:
        result.profsave = profsave_files[0]
        result.documents_dir = profsave_files[0].parent
        if len(profsave_files) > 1:
            result.notes.append(
                f"Found {len(profsave_files)} profile files; using the most recently written one "
                f"({result.profsave}). Use 'Locate...' to pick a different one."
            )

    if result.install_dir is None:
        result.notes.append(
            "Battlefield 6's install folder was not found automatically. Set it by hand: it is "
            "the folder containing the game executable, not the Documents folder."
        )
    # A path the user picked by hand always wins over anything detected.
    for role, value in (overrides or {}).items():
        path = Path(value)
        if role == "profsave" and path.is_file():
            result.profsave = path
            result.documents_dir = path.parent
            result.overridden.add("profsave")
        elif role == "install_dir" and path.is_dir():
            result.install_dir = path
            result.executable = _find_executable(path)
            result.user_cfg = path / "User.cfg"
            result.overridden.add("install_dir")

    if result.profsave is None:
        result.notes.append(
            "PROFSAVE_profile was not found. It is written the first time Battlefield 6 saves "
            "its video settings, so launch the game once, change any video setting, apply it and "
            "quit fully. If it still is not found, use 'Locate...' to point at it - the app then "
            "remembers the path."
        )
    return result


def is_game_running() -> bool:
    """Best-effort check. Writing either file while the game is open is pointless."""
    if not IS_WINDOWS:
        return False
    try:
        import subprocess

        proc = subprocess.run(
            ["tasklist.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=20, creationflags=0x08000000,
        )
        listing = (proc.stdout or "").lower()
        return any(name in listing for name in EXE_NAMES)
    except Exception:
        return False
