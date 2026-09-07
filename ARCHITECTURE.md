# BF6 Tuner — Architecture & Working Notes

Internal, developer-facing companion to `README.md`. README explains *what the
app does and why* for a user; this file explains *how it is built* for whoever
(human or Claude) picks up work on it next. **Keep this updated as part of any
change** — see "Work log" at the bottom; add an entry every session that
changes behavior, not just code shape.

Read `README.md` first for product framing (the FPS model, the Thread.*
policy reasoning, backup/restore UX). This file assumes that context and goes
straight to the code.

---

## 1. High-level shape

```
hardware.detect() ──┐
paths.discover()  ──┼──▶ engine.recommend() ──▶ Recommendation ──┬──▶ compare.build/from_paths() ──▶ Comparison
database.load()  ───┘         (matching,            │            └──▶ writer.render_report/json()
                          FPS model, cfg,            │
                          per-setting overrides)      └──▶ writer.render_user_cfg() / write_user_cfg()
                                                        writer.write_profsave()
```

- **`hardware.py`** — one-shot Windows detection (CIM/PowerShell + two raw
  `ctypes` Win32 calls). Never raises; falls back to a labelled sample profile.
- **`paths.py`** — finds `User.cfg` (install folder) and `PROFSAVE_profile`
  (Documents/OneDrive/AppData, newest-mtime wins). User overrides from
  `prefs.py` always beat auto-detection.
- **`database.py`** — loads the 5 JSON datasets, either from the AES-256-GCM
  encrypted bundle (shipped builds, via `crypto.py`) or plain `data/*.json`
  (source checkouts).
- **`engine.py`** — the core: matches detected hardware to DB entries,
  predicts GPU-limited and CPU-limited FPS separately, names the bottleneck,
  picks in-game settings (respecting VRAM caps, quality steps, upscaler
  ladder), decides which `User.cfg` lines to emit (notably the CPU-topology-
  gated `Thread.*` policy), and applies per-setting *and* per-cfg-line user
  overrides on top — the former re-derives the FPS prediction and frame cap
  (they have a real cost curve); the latter are policy switches with no cost
  curve, so they're written as set but never move the FPS estimate, and the
  engine says so via a warning rather than leaving that implicit. See
  `LINKED_CFG_KEYS` — `GameTime.MaxVariableFps` is deliberately *not*
  independently overridable from the cfg side; it is owned by the
  `frame_limit` in-game setting so the file and the prediction can't disagree
  about the cap.
- **`costs.py`** — the single shared cost-curve model (`option_cost`,
  `cost_for`, `frame_time`) used identically by `engine.py` (implicitly, via
  the settings DB's `cost` field — see below) and `compare.py`, so the two
  can never disagree about what a setting is worth.
- **`compare.py`** — reads the *actual current* config off disk
  (`PROFSAVE_profile`, `User.cfg`) and diffs it against the `Recommendation`,
  producing per-setting pros/cons/FPS deltas, `User.cfg` add/change/remove
  lines, and a headline summary. Reconstructs "current FPS" by walking the
  cost deltas backward from the recommendation's own prediction — one model,
  both numbers.
- **`writer.py`** — all file-system side effects: rendering `User.cfg` text,
  writing it (`.cfg` extension enforced), patching `PROFSAVE_profile` in
  place (only keys already present are touched — the format isn't fully
  documented so nothing is invented), and the restore-point/backup subsystem
  (one snapshot folder covers both files, so undo is atomic).
- **`ui/app.py`** — PySide6 GUI. Live recompute on any change; nothing hits
  disk until a button is pressed. See §5.
- **`cli.py`** / **`__main__.py`** — headless driver; `__main__` picks GUI vs
  CLI based on `len(sys.argv)`.
- **`crypto.py`** / **`_keyring.py`** — AES-256-GCM bundle format; the key is
  build-generated and injected into `_keyring.py` (gitignored, never
  committed — see `packaging/build.py`).
- **`prefs.py`** — persisted user preferences: manual path overrides,
  per-setting overrides, and per-cfg-line overrides, all in
  `%APPDATA%\BF6Tuner\`.
- **`update.py`** / **`_build_info.py`** — checks the GitHub API for whether
  `main` has moved on since the commit this build was made from; never raises,
  never auto-downloads. `_build_info.py` is build-generated (gitignored, same
  pattern as `_keyring.py`) and holds the commit SHA baked into a shipped
  build; a source checkout falls back to `git rev-parse HEAD`.

---

## 2. Data flow in detail

### Detect → match → predict (`engine.py`)

1. `match_gpu` / `match_cpu`: longest-id-first substring match against the
   DB, with a VRAM tiebreak for split SKUs (e.g. 4060 Ti 8GB vs 16GB) and a
   `infer_generation` fallback that guesses microarchitecture from the model
   number so an unlisted current-gen part doesn't get scored as ancient.
2. `_choose_operating_point`: for every `(quality step, upscaler)` pair on
   the ladder, estimate GPU FPS (`estimate_gpu_fps`) and compare against
   `estimate_cpu_fps` capped `want` — never chases a target the CPU can't
   deliver. Evaluates *all* combinations (not first-hit) and picks the
   least-destructive one that clears the target, so results are monotonic
   across presets.
3. Per-setting selection loop over `db.settings`: mostly reads
   `setting["presets"][preset]`, with special-cased ids (`resolution`,
   `frame_limit`, `upscaler`, `texture_quality` — VRAM-capped via
   `_vram_cap_for_textures`, `ray_tracing`, `frame_generation` — opt-in and
   gated on predicted FPS ≥ 60, `low_latency`, `hdr`) and a generic
   "push below preset floor" branch for the chosen quality step.
4. **Overrides**: any `setting_id` in `overrides` (round-tripped from
   `prefs.json`, coerced back to the right type via `_coerce_override`)
   replaces the engine's choice, and its cost delta (`cost_for` before/after)
   shifts `gpu_fps`/`cpu_fps` via `frame_time()` ratios — so picking a heavier
   setting visibly costs frames in the prediction, not just in the UI label.
   The frame cap re-derives from the shifted prediction *unless* the user
   pinned `frame_limit` itself, in which case that value wins everywhere.
5. `_build_cfg`: builds the `User.cfg` line list. The interesting policy is
   `_thread_policy` — refuses `Thread.ProcessorCount`/family overrides on
   hybrid CPUs, CPUs under 8c/16t, and dual-CCD X3D parts (recommends CCD
   pinning instead), and requires the advanced opt-in otherwise. See the
   engine.py module docstring and README §"Why this is not just another
   copy-paste config" for the measured numbers behind this.
6. `_evaluate_tweaks`: rule-based system checks (`system_tweaks.json`
   triggers) — XMP/EXPO, single-channel RAM, HDD install, HAGS, etc.

### Compare (`compare.py`)

- `read_current_settings` maps raw `PROFSAVE` key/values onto setting ids via
  each `SettingChoice.profsave_key`/`profsave_scale`.
- `read_current_cfg` parses the existing `User.cfg` (`Key Value` lines).
- `build()` diffs both against the `Recommendation`, classifying every
  setting as `change`/`same`/`unknown` (not stored in the profile)/`personal`
  (never written — mouse sensitivity, audio mix, `never_write: true`).
- Per-change `fps_delta` is **marginal** (computed independently per
  setting) and will not sum to the aggregate — stated in the module
  docstring and rendered to the user, not just an internal caveat.
- `User.cfg` diff always treats an existing unmanaged key as `remove`,
  because saving rewrites the whole file — this is a full-file-regeneration
  artifact, not a real deletion the app decided on, and the app says so.

### Write (`writer.py`)

- Every destructive write goes through `create_restore_point` first (one
  timestamped folder, both files, `manifest.json` recording which files
  *did not exist* so restore can delete what the app created).
- `write_user_cfg` raises `ValueError` if the target extension isn't
  `.cfg` (the #1 real-world failure mode: Windows hiding `.cfg.txt`).
- `write_profsave`/`profsave_plan` only ever modify keys already present in
  the file — never invents new PROFSAVE keys.
- Files are written with explicit CRLF (`newline=""` + manual `\r\n` joins)
  — deliberate, not an oversight, for game compatibility.

---

## 3. The database (`data/*.json` → `bf6tuner.db`)

Five JSON files, each with a `"schema": "bf6tuner.<name>/N"` header, loaded
via `Database` (`database.py`):

| File | Shape | Consumed by |
|---|---|---|
| `gpu_db.json` | `aliases` (name-normalisation substitutions) + `gpus: [{id, name, vendor, arch, vram, score, dlss, fsr, xess, frame_gen, reflex, rt}]`. `score` normalised so RTX 4080 SUPER @ 1440p High = 100. | `engine.match_gpu` |
| `cpu_db.json` | `heuristic` (fallback scoring constants + generation-inference tables) + `cpus: [{id, vendor, cores, threads, score, hybrid, x3d, ccds, gen}]`. `score` = estimated 64p Conquest FPS ceiling. | `engine.match_cpu`, `_cpu_heuristic` |
| `cfg_commands.json` | `confidence_levels`/`risk_levels` glossaries + `groups` + `commands: [...]` (per-key `summary`, `pros`/`cons`, `risk`, `confidence`, `hardware_policy`). | `engine._build_cfg` via `Database.command()` |
| `ingame_settings.json` | `profsave` docs + `presets` (esports/competitive/balanced/quality) + `settings: [{id, label, menu, type, options, cost, impact, tradeoff, note, profsave_key, profsave_scale, never_write}]`. Largest file (~54KB). | `engine.recommend`, `costs.cost_for`, `ui/app.py` detail pane |
| `system_tweaks.json` | `tweaks: [{id, label, scope, trigger, severity, why, how, value}]`, `why`/`how` support `{placeholder}` templating. | `engine._evaluate_tweaks` |

**Note the two separate "cost" concepts**: `setting["cost"]` (numeric curve,
`costs.py`, drives the actual FPS math) vs `setting["impact"]` (a coarser
qualitative int shown in the UI's per-setting detail pane via `_cost_label`).
They are populated independently in the DB — if the FPS model and the
detail-pane label ever visibly disagree for a setting, check both fields.

Encryption: `packaging/build.py` packs all five files with a fresh random
32-byte key (`crypto.new_key()`) into `bf6tuner.db` and writes the key to
`src/bf6tuner/_keyring.py` (gitignored — regenerated every build). A shipped
build ships *only* the encrypted bundle (`bf6tuner.spec` explicitly excludes
plain `data/`), so it can never silently fall back to editable JSON.
`database.load()` treats "bundle present, no key" as a hard error rather than
undefined behavior — don't copy a `.db` next to a source checkout.

---

## 4. UI (`src/bf6tuner/ui/app.py`)

- `MainWindow`: sidebar (hardware readout, preset buttons, target controls)
  + main panel (prediction hero, then tabs: Current vs recommended /
  In-game settings / User.cfg / Warnings / System checks) + action bar.
- The **User.cfg tab** is itself two sub-tabs: "Commands" (an editable table,
  built the same build-once-then-update-values way as the in-game settings
  table — see `_sync_cfg_table`/`_make_cfg_editor`/`_update_cfg_detail`) and
  "Raw file preview" (the original read-only text view, unchanged). Editors
  are typed from `cfg_commands.json`'s `type`/`min`/`max` (bool → combo,
  int → spinbox, float → double-spinbox). The `GameTime.MaxVariableFps` row
  has no editor of its own — a "Frame limit →" button jumps to and selects
  that row on the In-game settings tab instead (`_goto_frame_limit`), so
  there is exactly one place that owns the frame cap.
- The header's **update button/banner** is populated by `UpdateCheckWorker`
  (same off-thread pattern as `DetectWorker`), fired once silently at startup
  and again on demand via "Check for updates"; `UpdateDialog`
  (`ui/update_dialog.py`) renders the changelog and links out to GitHub -
  there is no in-app download.
- Everything recomputes through one path: `refresh()` → `engine.recommend()`
  → `compare.from_paths()` → `_render()`, which pushes into every widget.
  `self._loading` guards against `refresh()` cascading from signal handlers
  during programmatic widget updates (e.g. after `redetect()`).
- Per-setting override editing: `_make_editor` builds a combo/spinbox per
  row; `_on_editor_changed` writes to `prefs.set_setting_override` and calls
  `refresh()`. Picking the value that equals the recommendation is treated
  as *resetting* the override, not as "override that happens to match" —
  this is intentional UX, pinned by
  `test_override_matching_the_recommendation_is_not_flagged` in
  `test_engine.py`.
- `DetectWorker(QThread)` keeps the slow PowerShell/CIM detection off the UI
  thread.
- `LocateDialog` / `RestoreDialog`: manual path override and restore-point
  management, each setting a `changed`/`restored` flag the caller checks to
  decide whether to re-run detection/refresh.
- `theme.py` is pure styling (dark stylesheet + color constants) — the single
  source of color semantics (amber = "you overrode this").

---

## 5. Packaging & build (`packaging/`)

`build.py` pipeline: pack encrypted DB (`prepare_bundle`, writes
`_keyring.py`) → generate Windows version resource → write `_build_info.py`
(`write_build_info`, the commit SHA `update.py` compares against GitHub) →
optional PyArmor obfuscation (`--obfuscate`) → PyInstaller via `bf6tuner.spec`. The spec
builds **two EXEs from one Analysis**: `BF6Tuner` (GUI subsystem, no
stdout — double-click) and `BF6Tuner-cli` (console subsystem) — Windows
fixes GUI-vs-console at link time, so one binary can't do both.
`make_icon.py` hand-renders the `.ico` with no image library dependency
(manual PNG/ICO byte encoding). `UPDATE.bat` at repo root pulls, pushes
(triggering the GitHub Actions cloud build), and rebuilds both local
executables.

---

## 6. Tests (`tests/`)

`test_engine.py` (largest) covers matching, thread policy, FPS model, cfg
rendering, and the full override system. `test_compare.py` covers the diff
logic and additive frame-time model. `test_paths.py` covers the file-finding
edge cases (per-account subfolders, Saved Games, override precedence).
`test_restore.py` covers the backup/restore atomicity guarantees.
`test_crypto.py` covers the bundle format's tamper/wrong-key rejection. All
manually add `src/` to `sys.path`; most share a `make_profile(**overrides)`
fixture (baseline: Ryzen 7 7800X3D / RTX 4070 SUPER / 32GB@6000MT/s /
2560×1440@165Hz).

Run: `pip install -r requirements.txt && python -m pytest tests -q` (117
tests as of the last README update).

---

## 7. Key design decisions worth remembering

- **One FPS model, never two.** Both "current" and "recommended" FPS in the
  compare tab derive from the same `frame_time()`/cost-curve math the engine
  used to size the recommendation — deltas are computed and then the
  baseline prediction is rescaled by the ratio, rather than modeling
  "current" independently. Never add a second frame-rate estimator.
- **`Thread.*` overrides are hardware-gated, not a blanket recommendation.**
  See `engine._thread_policy`. This is the app's core differentiator per the
  README — don't relax the gating without updating both the code comment and
  the README section that cites the measured numbers.
- **Never invent PROFSAVE keys.** `profsave_plan` only touches keys already
  present in the user's file, because the on-disk format isn't fully
  reverse-engineered.
- **`.cfg` extension is enforced at write time**, not just documented,
  because a hidden `.cfg.txt` extension is the most common real-world
  failure mode for these configs.
- **Marginal vs aggregate FPS deltas are explicitly different numbers** and
  both compare.py and the UI say so — don't "fix" the per-row numbers to sum
  to the total; that would misrepresent the interaction of simultaneous
  changes.
- **Shipped builds cannot fall back to plain JSON.** `bf6tuner.spec`
  excludes `data/` on purpose; `database.load()` errors loudly rather than
  silently degrading if a bundle exists without its key.
- **Restore points snapshot both files atomically** so undo is one action,
  and record files that *didn't exist* so restore can clean up files the app
  itself created.
- **Override honesty extends to what an override does and doesn't affect.**
  In-game setting overrides have a real cost curve and visibly move the FPS
  estimate; `User.cfg` overrides are policy switches with no cost curve and
  the app says outright that they don't move the estimate, rather than
  quietly reusing the settings' cost math for something it was never modelled
  for. Don't blur this distinction to make the UI "feel" more consistent —
  it would misrepresent what the numbers mean.
- **There is no auto-update / auto-download.** `update.py` only ever compares
  commits and opens a browser tab; nothing is fetched or executed
  automatically, matching the "download it yourself" distribution model in
  the README.

---

## 8. Work log

Add a dated entry for every session of work — what changed, why, and
anything the next session needs to know. Most recent first.

### 2026-09-07 (2) — Editable User.cfg + update checker
Two features, both requested directly:

- **User.cfg lines are now editable**, mirroring the in-game settings tab:
  - `engine.py`: `CfgLine` gained `recommended_value`/`overridden`;
    `Recommendation` gained `cfg_overrides`; `recommend()` takes a new
    `cfg_overrides` param and applies it after `_build_cfg`, clamping/coercing
    via the matching `cfg_commands.json` entry's `type`/`min`/`max`
    (`_coerce_cfg_override`). `GameTime.MaxVariableFps` is excluded on purpose
    (see `LINKED_CFG_KEYS`) — it's owned by the `frame_limit` in-game setting
    so the file and the FPS prediction can never disagree about the cap.
    Appends an info `Warning_` when any cfg line is overridden, explicitly
    stating the FPS estimate does *not* move (these are policy switches, not a
    cost curve — unlike in-game setting overrides). New
    `Recommendation.overridden_cfg_lines` convenience property.
  - `prefs.py`: `load_cfg_overrides`/`save_cfg_overrides`/`set_cfg_override`/
    `clear_cfg_overrides`, stored in `%APPDATA%\BF6Tuner\cfg_overrides.json`,
    same shape as the existing setting-override functions.
  - `writer.py`: `render_user_cfg` now tags an overridden line with
    `[you changed this - recommended: X]` in the rendered comment, same
    mechanism as the existing legacy/high-risk tags.
  - `ui/app.py`: the User.cfg tab is now two sub-tabs — "Commands" (new
    editable table + detail pane, `_sync_cfg_table`/`_make_cfg_editor`/
    `_update_cfg_detail`, styled identically to the settings table's amber
    override highlighting) and "Raw file preview" (the old read-only view,
    unchanged). The linked frame-cap row shows a "Frame limit →" button that
    jumps to that row on the In-game settings tab instead of an editor.
    `_cfg_change_widget` in the comparison tab now tags a line with "you
    overrode this in the User.cfg tab" when applicable.
  - Tests: 9 new cases in `tests/test_engine.py` covering override
    application, clamping, the FPS-estimate-does-not-move invariant, the
    frame-cap exclusion, `hardware_policy: never` commands staying
    non-overridable, reset-to-recommended equality, and cfg text rendering.
  - Verified live (not just unit tests): constructed the real `MainWindow`
    off-screen (`QT_QPA_PLATFORM=offscreen`), drove an override through
    `refresh()`/`reset_cfg_override()` end to end, and confirmed the warning
    text and rendered `User.cfg` output.

- **Update checker**: new `update.py` module compares the commit this build
  was made from against `origin/main` via the public GitHub REST API
  (`/compare/{sha}...main`, falling back to a plain recent-commits list if the
  local commit is unknown or the compare 404s — e.g. a rebased history).
  Never raises; returns `UpdateInfo(status="error"|"unknown"|"up_to_date"|
  "update_available")`. No release/tag mechanism exists in this repo (every
  push builds as a GitHub Actions artifact — see README), so "update" links
  out to that Actions run rather than downloading anything itself.
  - `packaging/build.py`: new `write_build_info()` writes
    `src/bf6tuner/_build_info.py` (gitignored, same pattern as `_keyring.py`)
    with the commit SHA and build timestamp; added to `bf6tuner.spec`
    hiddenimports.
  - `ui/app.py`: `UpdateCheckWorker` (off-thread, same shape as
    `DetectWorker`) fires once silently at startup and again on demand via a
    new header button; `ui/update_dialog.py`'s `UpdateDialog` renders the
    changelog and opens the Actions run / full diff in a browser.
  - Tests: `tests/test_update.py` covers the pure response-parsing helpers
    (`parse_compare`, `parse_commit_list`) with hand-built payloads shaped
    like the real API — no network access needed in CI.
  - Verified live against the real `johanhoel/bf6` repo (both the
    already-up-to-date case and an artificially-old comparison base showing
    an 11-commit changelog), and the full `MainWindow` startup path via an
    offscreen Qt event loop.

- Bumped `__version__` to `1.1.0` (no version string is depended on anywhere
  in code or tests, confirmed by grep before bumping).
- All 132 tests pass (`python -m pytest tests -q`); the uncommitted
  `engine.py` Thread.MinFreeProcessorCount fix from the previous entry was
  left untouched, as instructed.
- Not done, flagged for later: no persisted "skip this version" / dismiss
  preference for the update banner — it just re-checks every launch. No
  regression test exercises the full Qt signal path end-to-end in CI (the
  live verification above was manual); consider an offscreen-Qt smoke test in
  CI if this area gets touched again.

### 2026-09-07 (1) — Initial architecture documentation
- Read the full codebase (engine.py in full; every other module via a
  delegated pass) to produce this file. No functional changes made.
- **Found in-progress, uncommitted work** at session start: `engine.py`
  changed the `Thread.MinFreeProcessorCount` logic so it's skipped entirely
  when `Thread.ProcessorCount` is already capped (`apply_threads`), to avoid
  double-reserving threads (previously `MinFreeProcessorCount` headroom could
  stack on top of an already-reduced `Thread.ProcessorCount`, over-reserving
  by 4 threads instead of 2 when both a hardware-eligible thread cap and
  background-load headroom applied at once). Not yet committed — see
  `git diff -- src/bf6tuner/engine.py` at the time of writing. Next session:
  decide whether to commit this as-is, add a regression test in
  `test_engine.py` covering "thread cap + background_load together", and
  update `README.md`'s Thread.* explanation if the behavior is user-visible.
- Recent commit history (`git log`) shows an active run of settings-table UI
  polish: drag-resizable columns, auto-fit row heights, wider reset column,
  amber override highlighting fix, old-value display on overrides. No
  outstanding issues found in that area during this pass, but it wasn't
  re-tested — just read.
