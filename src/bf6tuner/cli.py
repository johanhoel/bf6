"""Headless interface.

Useful for three things: driving the tool from a script, checking what would be
written before anything is written, and testing the engine off-Windows.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import APP_NAME, __version__
from . import compare, database, hardware, paths, writer
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
    parser.add_argument("--no-compare", action="store_true",
                        help="Skip the current-vs-recommended section.")

    parser.add_argument("--backup", action="store_true",
                        help="Take a restore point covering both config files and exit.")
    parser.add_argument("--list-restore-points", action="store_true",
                        help="List every restore point, newest first, and exit.")
    parser.add_argument("--restore", metavar="STAMP",
                        help="Restore a point by its timestamp (or 'latest') and exit.")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    db = database.load()
    profile = hardware.detect()
    game = paths.discover()
    targets = {"user_cfg": game.user_cfg, "profsave": game.profsave}

    if args.list_restore_points:
        points = writer.list_restore_points()
        if not points:
            print(f"No restore points yet. They will appear in {writer.backup_root()}")
        for point in points:
            print(f"{point.created:%Y%m%d-%H%M%S}  {point.label:<38} {point.describe()}")
        return 0

    if args.restore:
        points = writer.list_restore_points()
        if not points:
            print("No restore points exist.", file=sys.stderr)
            return 6
        if args.restore == "latest":
            chosen = points[0]
        else:
            chosen = next(
                (p for p in points if p.created.strftime("%Y%m%d-%H%M%S").startswith(args.restore)),
                None,
            )
        if chosen is None:
            print(f"No restore point matching {args.restore!r}. Use --list-restore-points.",
                  file=sys.stderr)
            return 6
        if paths.is_game_running():
            print("Battlefield 6 is running. Close it first.", file=sys.stderr)
            return 4
        for result in writer.restore_from(chosen):
            print(result.message)
        return 0

    if args.backup:
        point = writer.create_restore_point(targets, "Manual backup (CLI)")
        if point is None:
            print("Nothing to back up: neither config file was found.", file=sys.stderr)
            return 7
        writer.prune_restore_points()
        print(f"Restore point {point.created:%Y%m%d-%H%M%S} taken: {point.describe()}")
        print(point.directory)
        return 0

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

    comparison = None if args.no_compare else compare.from_paths(
        db, rec, game.profsave, game.user_cfg
    )

    print(writer.render_report(rec, comparison))

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(writer.render_report(rec, comparison), encoding="utf-8")
        print(f"Report written to {args.report}")
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(writer.render_json(rec, comparison), encoding="utf-8")
        print(f"JSON written to {args.json_out}")

    restore_point = None
    if args.out or args.apply or args.apply_ingame:
        restore_point = writer.create_restore_point(
            dict(targets, **({"user_cfg": args.out} if args.out else {})),
            f"Before applying {args.preset} (CLI)",
        )
        if restore_point:
            print(f"Restore point taken: {restore_point.directory}")

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
        result = writer.write_user_cfg(rec, destination, restore_point=restore_point)
        print(result.message)

    if args.apply_ingame:
        if not game.profsave:
            print("PROFSAVE_profile not found; in-game settings were not written.", file=sys.stderr)
            return 5
        if paths.is_game_running():
            print("Battlefield 6 is running - it would overwrite the file on exit. Close it first.",
                  file=sys.stderr)
            return 4
        result = writer.write_profsave(rec, game.profsave, restore_point=restore_point)
        print(result.message)

    if restore_point:
        writer.prune_restore_points()
        print(f"Undo everything with:  bf6tuner --restore {restore_point.created:%Y%m%d-%H%M%S}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
