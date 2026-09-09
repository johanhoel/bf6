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
expand/collapse-all — the settings list alone is 54 rows and only getting longer.
The in-game settings table also has an **Impact column**: each row is tagged
with which hardware resource(s) it meaningfully costs — GPU (teal), CPU (pink),
VRAM (purple) — colour-matched to a legend above the table, so scanning down
the column shows at a glance which settings are worth lowering first on a
CPU-limited machine versus a GPU-limited one (the same colours and cost labels
reappear when you click a row for the full detail pane).

**Compares** — reads your *current* settings out of `PROFSAVE_profile` and your
existing `User.cfg`, then shows every single change side by side with what it
costs, what it buys and what the argument against it is. Nothing is applied
before you have seen that list. See below.

**Remembers more than one setup** — the 4 presets are a starting point; the
sidebar's "Profiles" card lets you save the current preset, target and every
override under a name ("Tournament", "Chill") and switch between as many as
you want, not just tweak the same one slot.

**Checks its own homework** — the Benchmark tab drives
[PresentMon](https://github.com/GameTechDev/PresentMon) (the same open-source
capture engine behind NVIDIA FrameView and CapFrameX) while you actually play,
then shows the measured average / 1% low / 0.1% low FPS next to what the app
predicted for those exact settings. Every capture is saved automatically.
PresentMon itself is not bundled — point the app at your own copy once.

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

They are the same code and the same database. Windows decides whether
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
be getting, so it's an informed decision rather than a blind pull. Never blocks
startup, and a failed or offline check just stays quiet.

Two ways to actually get the update from that dialog:
- **Download and install now** — downloads the new build from GitHub's rolling
  `latest` release, replaces this exe, and relaunches automatically. Only shown
  for the compiled `.exe` (a source checkout has nothing for this to replace —
  use `git pull` / `UPDATE.bat` instead).
- **Open GitHub Actions build →** — the original path, always available: opens
  the Actions run that has the built executables attached, same as before.
- **Skip this version** — stops the sidebar banner and startup status message
  from nagging about that specific commit again. It comes back on its own once
  `main` moves past it. An explicit **Check for updates** click always shows
  the real state regardless of what was skipped before.

**Or build it yourself** on any Windows machine with Python 3.11+:

```bat
git clone https://github.com/johanhoel/bf6.git
cd bf6
packaging\build.bat
```

That one script creates a virtual environment, installs dependencies, runs the
tests, builds both executables into `dist\`, and opens the folder. Add
`--obfuscate` to run PyArmor over the source as well.

## About the database

Stated plainly, because this used to work differently and it's worth being
precise about the change:

- The settings database (`data/*.json` — GPU/CPU benchmark scores and BF6
  settings tables) ships as **plain JSON**, bundled read-only alongside the
  executable. It is public reference data, not a secret, so there is nothing
  here worth encrypting.
- It used to ship AES-256-GCM encrypted, with a fresh random key baked into
  the binary on every single build. That bought "casual copy/tamper
  protection" for data that didn't need it, at a real cost: a build with zero
  source changes still produced a byte-different, never-before-seen file
  every time, which is exactly what Windows SmartScreen / Smart App Control
  has no reputation for and can block. That trade wasn't worth it, so the
  encryption is gone (removed 2026-09-08).
- Optional **PyArmor obfuscation** (`--obfuscate`) still compiles the Python
  source itself, for anyone who wants a bar against casually reading the
  code — unrelated to the database.

To be clear about what removing this did and didn't fix: it removes one
guaranteed source of build-to-build hash churn (the random key), but a real
code change still legitimately produces a new binary every time, so this
alone does not make SmartScreen/Smart App Control stop flagging fresh
builds. See "If Windows won't run the built .exe at all" below for what
actually works today.

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

**If Windows won't run the built .exe at all** — Smart App Control (Windows
11's stricter, less overridable successor to classic SmartScreen) can block a
freshly-built, unsigned executable outright with no "Run anyway" option, and
every real code change here produces a fresh, never-before-seen hash, so it
never accumulates reputation (see "About the database" above — this isn't an
encryption thing, it's a signing/reputation gap). If you hit this and don't
have a code-signing certificate, run it from source instead — Smart App Control evaluates
standalone executables, not scripts interpreted by an already-trusted
`python.exe`, so this sidesteps it entirely:

```bat
pip install -r requirements.txt
run-from-source.bat                          REM GUI
run-from-source.bat --preset competitive     REM CLI, same arguments as BF6Tuner-cli.exe
```

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

Five JSON datasets under `data/`, bundled as plain files at build time (see
"About the database" above for why they aren't encrypted):

| File | Contents |
|---|---|
| `gpu_db.json` | 78 GPUs — VRAM, relative BF6 performance index, DLSS/FSR/XeSS support, frame-generation capability, ray tracing strength |
| `cpu_db.json` | 60 CPUs — core/thread counts, hybrid topology, X3D and chiplet layout, estimated CPU-limited FPS, plus a fallback heuristic for anything not listed |
| `cfg_commands.json` | 45 `User.cfg` commands, each with a **confidence level** (`documented` / `community` / `legacy`), a **risk level**, a hardware policy, an explanation, and pros/cons for the ones actually emitted |
| `ingame_settings.json` | 54 in-game settings with per-preset values, VRAM gates, a per-option **cost curve** in percent of frame time, and **directional pros and cons** for raising or lowering each one. 4 are marked `confidence: unverified` — view/LOD distance, weapon FOV, display mode, and camera shake are plausible additions never sourced from a confirmed profile, so none carry a profile key and none are ever written automatically. Sharpening, Reflection Quality, and Texture Filtering used to be on that list too, until a real profile confirmed their keys (`SharpnessSlider`/`ReflectionQuality`/`TextureFiltering`) and they were promoted to actually write. Also confirmed from a real profile: Undergrowth Quality and Significance Quality (CPU/GPU-saving additions), the background/menu frame-rate-limiter family (recommended on unconditionally since it only ever throttles the game while tabbed out or in a menu), a separate Screen Space Reflections toggle, Vehicle Field of View, two never-written FOV-scale toggles, and a Dynamic Resolution Scaling family (Resolution Scale, DRS enable/floor/target — the target is computed to always match Frame Rate Limit) |
| `system_tweaks.json` | 14 OS/BIOS/driver checks with trigger conditions |
| `keybind_concepts.json` | Key-binding decode data — see "Key bindings" below |

Commands are marked `legacy` when they are real Frostbite console variables from
BF3/BF4/BFV that may be silent no-ops in BF6. Those are excluded by default
rather than padded into the output to make it look impressive. A few commands are
in the database purely so they can be documented as *never* emitted — forcing the
DX12 backend (a known cause of `DXGI_ERROR_DEVICE_HUNG` on GTX 10-series), and
anything that removes the skybox or the HUD, which is a visibility exploit and
exactly the shape of change anti-cheat and tournament rules care about.

## Key bindings

The **Key Bindings** tab shows your real, currently-saved keyboard bindings —
read-only, straight from `PROFSAVE_profile`'s `GstKeyBinding.<category>.<concept>.<slot>.*`
entries. Nothing is ever written here.

Why read-only, stated plainly: Battlefield 6's own encoding for *keyboard*
bindings is confirmed — cross-referenced directly against a live install's
Edit Key Bindings menu (five independent matches) against the public,
standard DirectInput `DIK_*` scan-code table. That table is a decades-old
Microsoft API standard, not something Frostbite- or BF6-specific, which is
also why it was safe to trust once confirmed. *Mouse* and *controller*
bindings are a different story — the numbers don't fit a simple index and
aren't decoded, so they show their raw stored values rather than a guess.
Getting a **read** wrong here just means an incomplete label; getting a
**write** wrong means silently corrupting your real controls, which is a
much higher bar this app hasn't cleared yet. If you use it and something
looks off, that's exactly the kind of thing to flag.

One concrete lesson already learned building this: Frostbite only writes a
binding to your profile once you've opened that specific keybind *page* in
the in-game menu — an untouched page's default binding exists in the UI but
not in the file at all. So a concept missing from the Key Bindings tab
usually means "not customized yet," not "doesn't exist."

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
python -m pytest tests -q          # 233 tests
PYTHONPATH=src python -m bf6tuner --preset competitive   # CLI, runs on Linux too
```

Off Windows, hardware detection returns a clearly-labelled sample profile so the
engine and UI can be exercised anywhere.

```
bf6/
├── data/                 six JSON datasets (the database + keybind concepts)
├── examples/             real BF6 PROFSAVE_profile / User.cfg reference dumps
├── src/bf6tuner/
│   ├── hardware.py       detection: CIM, registry, core topology, display modes
│   ├── paths.py          finding the game and its two config files
│   ├── prefs.py          remembered manual path overrides
│   ├── engine.py         matching, frame rate model, setting selection, cfg policy
│   ├── costs.py          per-option cost curves shared by engine and compare
│   ├── compare.py        current vs recommended, impact and trade-offs
│   ├── writer.py         rendering, restore points, PROFSAVE patching, reports
│   ├── database.py       plain-JSON loader (bundled data/, frozen or not)
│   ├── keybinds.py       read-only key-binding decoder (see "Key bindings" below)
│   ├── update.py         checks GitHub for a newer build, never blocks or raises
│   ├── icon.py           the app icon, drawn in pure Python - shared by the build and the running app
│   ├── benchmark.py      real frame-time capture via PresentMon, checked against the prediction
│   ├── cli.py            headless mode
│   └── ui/               Qt window, comparison view, restore/locate/update dialogs, theme
├── packaging/            build.py, build.bat, PyInstaller spec, icon generator
└── tests/                engine policy, comparison, restore and update tests
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
