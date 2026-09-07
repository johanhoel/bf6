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

**Detects** — CPU model (with the microarchitecture inferred from the model number when the exact part is not in the database), physical cores, logical threads, P-core/E-core split;
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

**Lets you disagree** — every recommended in-game value is editable, and so is
every `User.cfg` line the app decides to write. Change an in-game setting and the
predicted frame rate, the frame cap and the comparison all follow, so you can see
what your choice costs rather than guessing. `User.cfg` commands are policy calls,
not a modelled frame-time cost, so overriding one is honestly shown as *not*
moving the FPS estimate — the pros, cons, risk and confidence for that command are
shown instead, and the real effect is yours to measure with the frame time graph.
The frame cap itself lives in exactly one place (the in-game "Frame limit" control)
so the file and the prediction can never disagree about it. Overrides of either
kind persist across restarts and preset changes, are marked in their table, and
reset individually or all at once.

Both tables group their rows by category (Video &gt; Basic/Advanced/Ray Tracing,
Controls, Audio for settings; CPU Threading, Render Pipeline, Frame Pacing and so
on for `User.cfg`), with a search box, a jump-to-category dropdown, and
expand/collapse-all — the settings list alone is 39 rows and only getting longer.

**Compares** — reads your *current* settings out of `PROFSAVE_profile` and your
existing `User.cfg`, then shows every single change side by side with what it
costs, what it buys and what the argument against it is. Nothing is applied
before you have seen that list. See below.

**Remembers more than one setup** — the 4 presets are a starting point; the
sidebar's "Profiles" card lets you save the current preset, target and every
override under a name ("Tournament", "Chill") and switch between as many as
you want, not just tweak the same one slot.

**Writes** — `User.cfg` into the game folder (with the right extension, which is
the single most common reason these files "do nothing"), and optionally patches
`PROFSAVE_profile`. Both are snapshotted together first, and one click puts
everything back.

**Checks the system** — XMP/EXPO left off (DDR-generation aware: DDR5 sitting at 4800 MT/s is the same fault as DDR4 at 2133), single-channel memory, ReBAR, HAGS,
game on a mechanical drive, and so on, each with the reason and the fix. These
are reported, never applied silently.

## Current vs recommended

The first tab is a diff of your machine against the recommendation, and it is
where the app expects you to spend your time. For every setting that would
change it shows:

```
Shadow Quality          Ultra  →  Medium          +8 FPS, GPU -19.0% frame time, CPU -5.5%
  + The single largest frame rate lever here: Ultra to Low is routinely 15-25%
  + Also saves CPU, because shadow maps mean extra draw calls
  − Shadows cast around corners are genuine information, and Low removes some of them
  − Visible shadow shimmer and pop-in at range
  Why: ...
```

Changes are ranked by how much they are actually worth, so the top of the list
is where your frames are. Alongside them the tab lists what is **already
correct**, what is **left alone because it is yours** (mouse sensitivity, ADS
sensitivity, audio mix — the app never touches these), and what is **not stored
in the profile** and therefore cannot be compared.

The `User.cfg` half of the diff shows additions, changes, *and removals* —
because saving rewrites the whole file, so an existing line the app does not
manage would disappear. It says so rather than letting you find out later.

### How the impact numbers work

Every setting carries a cost curve in percent of frame time per option. A
configuration's frame time is the baseline plus the sum of its options' costs,
which is what makes it valid to compare two whole configurations rather than
just summing pairwise deltas — summing deltas double-counts and saturates as
soon as several expensive settings move at once.

The engine already sized the *recommended* settings against your real hardware,
so the current frame rate is derived from that same model by the frame time
ratio. One model, both numbers, no chance of the two disagreeing.

Two honest caveats, which the app states on screen as well:

- Per-change FPS figures are **marginal** — what that change is worth on its
  own. They will not sum to the total.
- A change showing **0 FPS** is one the other side of the bottleneck absorbs.
  On a CPU-limited machine, lowering post-processing genuinely does nothing, and
  the app would rather tell you that than pad the number.

## Backup and recovery

Every write is preceded by a **restore point**: a single timestamped folder
holding `User.cfg` and `PROFSAVE_profile` captured at the same moment, plus a
manifest. Because both files are in one snapshot, undoing is one action rather
than two that can end up half-applied.

- **Back up now** takes a restore point without changing anything.
- **Apply everything** takes one automatically, then writes both files.
- **Restore…** lists every snapshot with its timestamp and contents; pick one,
  confirm, done.

A restore point also records files that *did not exist* when it was taken, so
restoring deletes a `User.cfg` the app created rather than leaving it behind.
That is the difference between an undo and a partial one. Snapshots live in
`%APPDATA%\BF6Tuner\backups\` and the oldest are pruned past 40.

From the command line:

```
BF6Tuner-cli.exe --backup                # snapshot both files, change nothing
BF6Tuner-cli.exe --list-restore-points   # newest first
BF6Tuner-cli.exe --restore latest        # or --restore 20260906-113000
```

Applying from the CLI prints the exact `--restore` command that undoes it.

## Getting the executable

**Download it.** Every push builds on a real Windows runner, runs the test
suite, executes the built binary and uploads the result. Grab it from the GitHub
Actions run for this branch → artifact `BF6Tuner-windows-x64`. Drop it wherever
you keep tools — e.g. `C:\Users\<you>\claude\bf6\`.

The artifact contains **two executables**:

| File | Use it for |
|---|---|
| `BF6Tuner.exe` | The app. Double-click it. No console window appears. |
| `BF6Tuner-cli.exe` | The same application, for use from a terminal or a script. |

They are the same code and the same encrypted database. Windows decides whether
an executable is a GUI program or a console program when it is linked, and there
is no runtime switch: a GUI binary has no stdout at all, and a shell does not
even wait for it to finish. So a single executable cannot both double-click
cleanly *and* work in PowerShell. Hence one of each.

If you only ever double-click, you only need `BF6Tuner.exe`.

Both are portable: no installer, no registry writes. Restore points go to
`%APPDATA%\BF6Tuner\backups\`.

**Keep it up to date.** `UPDATE.bat` in the repo root is the one-shot: it pulls the
latest changes, pushes them to this repository (which starts the cloud build), runs
the tests, builds both executables and drops them in the repo folder itself, so
`BF6Tuner.exe` sits right next to the script. First run installs the Python
dependencies and takes a few minutes; after that it is about a minute.

The app also checks for you: on launch, and on demand via **Check for updates** in
the header, it asks GitHub's API whether `main` has moved on since the commit this
build was made from, and — if so — shows the commit messages for everything you'd
be getting, so it's an informed decision rather than a blind pull. There is no
auto-download (no formal release artifact to fetch without a browser — see "About
`encrypted`" and "Getting the executable" above), so the dialog links straight to
the GitHub Actions run that has the built executables attached. Never blocks
startup, and a failed or offline check just stays quiet.

**Or build it yourself** on any Windows machine with Python 3.11+:

```bat
git clone https://github.com/johanhoel/bf6.git
cd bf6
packaging\build.bat
```

That one script creates a virtual environment, installs dependencies, runs the
tests, builds both executables into `dist\`, and opens the folder. Add
`--obfuscate` to run PyArmor over the source as well.

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

Use `BF6Tuner-cli.exe` for this — the GUI build has no console to print to:

```
BF6Tuner-cli.exe --preset competitive --resolution 2560x1440 --refresh 165
BF6Tuner-cli.exe --preset esports --apply --apply-ingame
BF6Tuner-cli.exe --preset balanced --report report.txt --json report.json
BF6Tuner-cli.exe --print-cfg > User.cfg
BF6Tuner-cli.exe --backup
BF6Tuner-cli.exe --restore latest
```

The report and the JSON export both include the full current-vs-recommended
comparison, with the pros and cons for every change. `--no-compare` omits it.

`--allow-thread-overrides`, `--allow-frame-gen` and `--legacy` unlock the opt-in
behaviour described above. `--help` lists everything.

## Where the files go

| File | Location | Notes |
|---|---|---|
| `User.cfg` | The **install** folder, next to the game executable | Not Documents. This trips up almost everyone. |
| `PROFSAVE_profile` | `Documents\Battlefield 6\settings\` (`\steam\` or a per-account subfolder on some builds) | The app searches Documents, OneDrive-redirected Documents, `%LOCALAPPDATA%`, `%APPDATA%` and Saved Games, and picks the most recently written profile. If it still cannot find it, **Locate…** lets you point at it and remembers the path. |
| Restore points | `%APPDATA%\BF6Tuner\backups\` | One timestamped folder per snapshot, holding both files and a manifest. Restorable in one click. |

Close Battlefield 6 before writing either file — it rewrites its own settings on
exit and will discard anything written while it is running. The app checks and
refuses.

## The database

Five JSON datasets under `data/`, encrypted into one bundle at build time:

| File | Contents |
|---|---|
| `gpu_db.json` | 78 GPUs — VRAM, relative BF6 performance index, DLSS/FSR/XeSS support, frame-generation capability, ray tracing strength |
| `cpu_db.json` | 60 CPUs — core/thread counts, hybrid topology, X3D and chiplet layout, estimated CPU-limited FPS, plus a fallback heuristic for anything not listed |
| `cfg_commands.json` | 39 `User.cfg` commands, each with a **confidence level** (`documented` / `community` / `legacy`), a **risk level**, a hardware policy, an explanation, and pros/cons for the 19 that are actually emitted |
| `ingame_settings.json` | 39 in-game settings with per-preset values, VRAM gates, a per-option **cost curve** in percent of frame time, and **directional pros and cons** for raising or lowering each one. 4 are marked `confidence: unverified` — plausible additions (sharpening, view/LOD distance, reflection quality, weapon FOV) not yet confirmed against a real BF6 profile, so they carry no profile key and are never written automatically |
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
python -m pytest tests -q          # 158 tests
PYTHONPATH=src python -m bf6tuner --preset competitive   # CLI, runs on Linux too
```

Off Windows, hardware detection returns a clearly-labelled sample profile so the
engine and UI can be exercised anywhere. `python packaging/build.py --bundle-only`
builds just the encrypted database.

```
bf6/
├── data/                 five JSON datasets (the database)
├── src/bf6tuner/
│   ├── hardware.py       detection: CIM, registry, core topology, display modes
│   ├── paths.py          finding the game and its two config files
│   ├── prefs.py          remembered manual path overrides
│   ├── engine.py         matching, frame rate model, setting selection, cfg policy
│   ├── costs.py          per-option cost curves shared by engine and compare
│   ├── compare.py        current vs recommended, impact and trade-offs
│   ├── writer.py         rendering, restore points, PROFSAVE patching, reports
│   ├── crypto.py         AES-256-GCM bundle format
│   ├── database.py       encrypted-bundle-first loader
│   ├── update.py         checks GitHub for a newer build, never blocks or raises
│   ├── icon.py           the app icon, drawn in pure Python - shared by the build and the running app
│   ├── cli.py            headless mode
│   └── ui/               Qt window, comparison view, restore/locate/update dialogs, theme
├── packaging/            build.py, build.bat, PyInstaller spec, icon generator
└── tests/                engine policy, comparison, restore, crypto and update tests
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
