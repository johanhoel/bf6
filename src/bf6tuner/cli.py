"""Headless interface.

Useful for three things: driving the tool from a script, checking what would be
written before anything is written, and testing the engine off-Windows.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import APP_NAME, __version__
from . import database, hardware, paths, writer
from .engine import PRESETS, Target, recommend


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bf6tuner",
        description=f"{APP_NAME} {__version__} - hardware-aware Battlefield 6 settings and User.cfg generator.",
    )
    parser.add_argument("--preset", choices=PRESETS, default="balanced")
    parser.add_argument("--resolution", help="e.g. 2560x1440. Defaults to the detected display.")
    parser.add_argument("--refresh", type=int, help="Refresh rate in Hz. Defaults to the detected panel.")
    parser.add_argument("--no-vrr", action="store_true", help="Display has no G-Sync/FreeSync.")
    parser.add_argument("--hdr", action="store_true", help="Display genuinely does HDR well.")
    parser.add_argument("--background-load", action="store_true",
                        help="You stream, record, or keep heavy apps open while playing.")
    parser.add_argument("--allow-frame-gen", action="store_true",
                        help="Permit frame generation where the GPU supports it (adds latency).")
    parser.add_argument("--allow-thread-overrides", action="store_true",
                        help="Permit Thread.* overrides where the CPU topology makes them defensible.")
    parser.add_argument("--legacy", action="store_true",
                        help="Include legacy Frostbite keys that may be no-ops in BF6.")
    parser.add_argument("--no-overlay", action="store_true", help="Do not enable the in-game FPS overlay.")

    parser.add_argument("--out", type=Path, help="Write the User.cfg to this path instead of the game folder.")
    parser.add_argument("--apply", action="store_true", help="Write User.cfg into the detected game folder.")
    parser.add_argument("--apply-ingame", action="store_true",
                        help="Also patch PROFSAVE_profile. The game must be closed.")
    parser.add_argument("--report", type=Path, help="Write the full explained report to this path.")
    parser.add_argument("--json", dest="json_out", type=Path, help="Write machine-readable output here.")
    parser.add_argument("--print-cfg", action="store_true", help="Print the User.cfg to stdout and exit.")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    db = database.load()
    profile = hardware.detect()
    game = paths.discover()

    width, height = profile.width, profile.height
    if args.resolution:
        try:
            width, height = (int(part) for part in args.resolution.lower().split("x", 1))
        except ValueError:
            print(f"Could not read --resolution {args.resolution!r}; expected WIDTHxHEIGHT.", file=sys.stderr)
            return 2

    target = Target(
        preset=args.preset,
        width=width, height=height,
        refresh_hz=args.refresh or profile.max_refresh_hz or profile.refresh_hz,
        vrr=not args.no_vrr,
        background_load=args.background_load,
        hdr_display=args.hdr,
        allow_frame_generation=args.allow_frame_gen,
        allow_thread_overrides=args.allow_thread_overrides,
        include_legacy_commands=args.legacy,
        show_fps_overlay=not args.no_overlay,
    )

    media = game.install_drive and profile.drive_media.get(game.install_drive)
    rec = recommend(db, profile, target, install_drive_media=media)

    if args.print_cfg:
        sys.stdout.write(writer.render_user_cfg(rec).replace("\r\n", "\n"))
        return 0

    print(writer.render_report(rec))

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(writer.render_report(rec), encoding="utf-8")
        print(f"Report written to {args.report}")
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(writer.render_json(rec), encoding="utf-8")
        print(f"JSON written to {args.json_out}")

    destination = args.out
    if args.apply and destination is None:
        if not game.user_cfg:
            print("Could not find the Battlefield 6 install folder. Pass --out with an explicit path.",
                  file=sys.stderr)
            return 3
        destination = game.user_cfg

    if destination:
        if paths.is_game_running():
            print("Battlefield 6 appears to be running. Close it first.", file=sys.stderr)
            return 4
        result = writer.write_user_cfg(rec, destination)
        print(result.message + (f" (backup: {result.backup})" if result.backup else ""))

    if args.apply_ingame:
        if not game.profsave:
            print("PROFSAVE_profile not found; in-game settings were not written.", file=sys.stderr)
            return 5
        if paths.is_game_running():
            print("Battlefield 6 is running - it would overwrite the file on exit. Close it first.",
                  file=sys.stderr)
            return 4
        result = writer.write_profsave(rec, game.profsave)
        print(result.message + (f" (backup: {result.backup})" if result.backup else ""))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
