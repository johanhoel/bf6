# BF6 Tuner

A Windows configurator for Battlefield 6 that reads your actual hardware and
produces both the in-game settings and a `User.cfg` matched to it — with the
reasoning shown for every value, and a predicted frame rate before you launch
the game.

---

## Why this is not just another copy-paste config

Most Battlefield `User.cfg` guides hand you the same block of text regardless of
what is in your machine. The centrepiece of nearly all of them is a
`Thread.ProcessorCount` cap sold as "lower CPU usage, more FPS". Independent
testing says otherwise:

- On **Intel hybrid CPUs** (12th gen and newer, Core Ultra) it stops the game
  using the E-cores. Reported result: roughly **15–20% lower 1% low FPS** — that
  is visible stutter, even when the average barely moves.
- On **AMD** systems one measured run showed average FPS down 4% but 1% lows
  down 15% and 0.1% lows down 16%.

So BF6 Tuner detects your CPU topology properly — via
`GetLogicalProcessorInformationEx`, which reports a per-core efficiency class,
not a guess from the model name — and **refuses to write those lines on hardware
where they hurt**, telling you why. On a dual-CCD X3D chip it points you at the
actual fix (CCD preference) instead. Where the overrides are defensible, they are
still opt-in behind an advanced toggle.

That principle runs through the whole tool: every recommendation carries its
reason, and the things it deliberately *doesn't* do are listed just as
prominently as the things it does.

## What it does

**Detects** — CPU model, physical cores, logical threads, P-core/E-core split;
GPU model, real VRAM (from the driver registry key, not the 4GB-capped WMI
field), driver version; RAM capacity, speed and module count; native resolution
and the panel's maximum refresh rate; which drive the game is installed on and
whether it is an SSD; and where Battlefield 6 actually lives (Steam library
folders, EA App, Epic, plus a scan of other drives).

**Predicts** — a GPU-limited and a CPU-limited frame rate, separately, then
names the bottleneck. This matters: on a CPU-limited machine, turning graphics
settings down does not raise your frame rate, it just makes the game uglier.

**Recommends** — in-game settings for four presets (Esports, Competitive,
Balanced, Quality), with textures capped to your VRAM, an upscaler mode chosen
from your GPU vendor and the gap to your target, and a frame cap computed from
your refresh rate and what the machine can actually hold.

**Writes** — `User.cfg` into the game folder (with the right extension, which is
the single most common reason these files "do nothing"), and optionally patches
`PROFSAVE_profile`. Both are backed up first, and both can be restored.

**Checks the system** — XMP/EXPO left off, single-channel memory, ReBAR, HAGS,
game on a mechanical drive, and so on, each with the reason and the fix. These
are reported, never applied silently.

## Getting the executable

**Download it.** Every push builds `BF6Tuner.exe` on a real Windows runner, runs
the test suite, executes the built binary, and uploads it. Grab it from the
GitHub Actions run for this branch → artifact `BF6Tuner-windows-x64`. Drop it
wherever you keep tools — e.g. `C:\Users\<you>\claude\bf6-configurator\`.

It is portable: one file, no installer, no registry writes. Configuration
backups go to `%APPDATA%\BF6Tuner\backups\`.

**Or build it yourself** on any Windows machine with Python 3.11+:

```bat
git clone <this repo>
cd bf6-configurator
packaging\build.bat
```

That one script creates a virtual environment, installs dependencies, runs the
tests, builds `dist\BF6Tuner.exe`, and opens the folder. Add `--obfuscate` to
run PyArmor over the source as well.

## About "encrypted"

Stated plainly, because this is worth being precise about:

- The settings database is **AES-256-GCM encrypted** and compressed into a
  single blob inside the executable. No editable JSON ships alongside it, and a
  build that finds an encrypted bundle will not fall back to plain files.
- The GCM tag is **verified before use**, so a modified database refuses to load
  rather than silently feeding wrong values into the engine.
- A **fresh key is generated on every build** and injected into the binary. It is
  never committed.
- Optional **PyArmor obfuscation** (`--obfuscate`) compiles the Python source so
  the logic is not readable either.

What this buys: the data cannot be read or edited with a text editor, cannot be
lifted out and reused, and cannot be tampered with undetected. What it does not
buy: protection from someone with a debugger. The key has to be inside the binary
because the binary decrypts without a server — that is true of every client-side
scheme, and anyone claiming otherwise is selling something. If you need stronger
guarantees, the database has to move behind a licensed server.

## Running it

Double-click for the GUI. Everything recomputes live as you change the preset,
resolution, refresh rate or options; nothing is written until you press a button.

There is also a full command-line mode:

```
BF6Tuner.exe --preset competitive --resolution 2560x1440 --refresh 165
BF6Tuner.exe --preset esports --apply --apply-ingame
BF6Tuner.exe --preset balanced --report report.txt --json report.json
BF6Tuner.exe --print-cfg > User.cfg
```

`--allow-thread-overrides`, `--allow-frame-gen` and `--legacy` unlock the opt-in
behaviour described above. `--help` lists everything.

## Where the files go

| File | Location | Notes |
|---|---|---|
| `User.cfg` | The **install** folder, next to the game executable | Not Documents. This trips up almost everyone. |
| `PROFSAVE_profile` | `Documents\Battlefield 6\settings\` (`\steam\` on the Steam build) | May be redirected into OneDrive; the app checks there too. |
| Backups | `%APPDATA%\BF6Tuner\backups\` | Timestamped, restorable from the GUI. |

Close Battlefield 6 before writing either file — it rewrites its own settings on
exit and will discard anything written while it is running. The app checks and
refuses.

## The database

Five JSON datasets under `data/`, encrypted into one bundle at build time:

| File | Contents |
|---|---|
| `gpu_db.json` | 78 GPUs — VRAM, relative BF6 performance index, DLSS/FSR/XeSS support, frame-generation capability, ray tracing strength |
| `cpu_db.json` | 60 CPUs — core/thread counts, hybrid topology, X3D and chiplet layout, estimated CPU-limited FPS, plus a fallback heuristic for anything not listed |
| `cfg_commands.json` | 39 `User.cfg` commands, each with a **confidence level** (`documented` / `community` / `legacy`), a **risk level**, a hardware policy, and an explanation |
| `ingame_settings.json` | 32 in-game settings with per-preset values, VRAM gates, GPU/CPU/VRAM cost weights and the competitive trade-off for each |
| `system_tweaks.json` | 14 OS/BIOS/driver checks with trigger conditions |

Commands are marked `legacy` when they are real Frostbite console variables from
BF3/BF4/BFV that may be silent no-ops in BF6. Those are excluded by default
rather than padded into the output to make it look impressive. A few commands are
in the database purely so they can be documented as *never* emitted — forcing the
DX12 backend (a known cause of `DXGI_ERROR_DEVICE_HUNG` on GTX 10-series), and
anything that removes the skybox or the HUD, which is a visibility exploit and
exactly the shape of change anti-cheat and tournament rules care about.

## Accuracy, honestly

The predicted frame rates come from a model, not from your machine. The GPU index
is anchored so that a score of 100 (RTX 4080 SUPER) renders 165 FPS at 1440p on
the Balanced preset; everything else scales from there by resolution, preset and
upscaler cost. CPU scores are estimated 64-player Conquest ceilings.

Expect the estimates to be directionally right and numerically approximate. The
tool turns the in-game FPS overlay and frame time graph on by default so you can
check its homework. If reality disagrees, the numbers in `data/*.json` are the
only thing that needs changing.

## Development

```bash
pip install -r requirements.txt
python -m pytest tests -q          # 37 tests
PYTHONPATH=src python -m bf6tuner --preset competitive   # CLI, runs on Linux too
```

Off Windows, hardware detection returns a clearly-labelled sample profile so the
engine and UI can be exercised anywhere. `python packaging/build.py --bundle-only`
builds just the encrypted database.

```
bf6-configurator/
├── data/                 five JSON datasets (the database)
├── src/bf6tuner/
│   ├── hardware.py       detection: CIM, registry, core topology, display modes
│   ├── paths.py          finding the game and its two config files
│   ├── engine.py         matching, frame rate model, setting selection, cfg policy
│   ├── writer.py         rendering, backups, PROFSAVE patching, reports
│   ├── crypto.py         AES-256-GCM bundle format
│   ├── database.py       encrypted-bundle-first loader
│   ├── cli.py            headless mode
│   └── ui/               Qt window and theme
├── packaging/            build.py, build.bat, PyInstaller spec, icon generator
└── tests/                engine policy and crypto tests
```

## Sources

Settings and command research drew on the FieldTuner project's settings database
and on community documentation of Battlefield 6's `User.cfg`, including the EA
Forums threads measuring the CPU thread-count trick on Intel E-cores and on AMD.
Where sources disagreed — and on the thread-count question they disagree
sharply — the disagreement is encoded in the database as a confidence level and
surfaced in the app, rather than resolved silently in favour of whichever guide
was loudest.

- <https://github.com/tomstetson/FieldTuner-Desktop>
- <https://forums.ea.com/discussions/battlefield-6-general-discussion-en/user-cfg-trick-lower-cpu-usage-doesnt-work---hurts-fps-on-intel-e-cores/12779845>
- <https://forums.ea.com/discussions/battlefield-6-technical-issues-en/battlefield-6-%E2%80%93-raw-core-user-cfg-v2-6-fixes-fps-drops--stutter/12914570>
- <https://mein-mmo.de/en/battlefield-6-changing-user-cfg-what-is-it-and-where-can-i-find-it,1531767/>
- <https://n1kobg.blogspot.com/p/battlefield-6-so-for-too-high.html>
