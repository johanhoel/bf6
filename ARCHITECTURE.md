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
- **`database.py`** — loads the 5 JSON datasets, from the frozen resource
  root's bundled `data/` (shipped builds) or the repo's top-level `data/`
  (source checkouts). Plain JSON in both cases — see §3 for why.
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
- **`prefs.py`** — persisted user preferences: manual path overrides,
  per-setting overrides, and per-cfg-line overrides, all in
  `%APPDATA%\BF6Tuner\`.
- **`icon.py`** — the app's reticle icon, drawn pixel-by-pixel in pure Python
  (no image library). Shared by `packaging/make_icon.py` (writes the .ico
  baked into the .exe) and `ui/app.py`'s `app_icon()` (renders the same
  artwork at runtime for the window/taskbar icon, so a source checkout looks
  the same as the shipped build).
- **`benchmark.py`** — closes the loop on the FPS prediction with a real
  measurement: drives PresentMon (Intel/Microsoft's open-source ETW frame
  capture tool, the engine behind NVIDIA FrameView/CapFrameX) as an external
  process, parses its CSV output into average/1%-low/0.1%-low FPS, and
  auto-saves every capture (with the prediction it's being checked against)
  under `%APPDATA%\BF6Tuner\benchmarks\`. PresentMon itself is never
  bundled or auto-downloaded — same "point the app at your own copy" policy
  as everywhere else external (see `update.py`); the path is remembered via
  `prefs.py`'s existing generic path-override mechanism (role
  `"presentmon_exe"`), no new storage needed.
- **`update.py`** / **`_build_info.py`** — `check_for_update()` checks the
  GitHub API for whether `main` has moved on since the commit this build was
  made from; never raises. `_build_info.py` is build-generated (gitignored)
  and holds the commit SHA baked into a shipped build; a source checkout
  falls back to `git rev-parse HEAD`. Since 2026-09-08, the same module also
  has `download_update()`/`apply_update_and_relaunch()` — an actual
  self-updater, frozen-build-only, with its own `SelfUpdateError` (raises,
  unlike the passive check, since it's a button press). See §7's design
  decision and the module's own docstring for why it fetches from a GitHub
  **Release**, not the Actions artifact.

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

## 3. The database (`data/*.json`)

Five JSON files, each with a `"schema": "bf6tuner.<name>/N"` header, loaded
via `Database` (`database.py`):

| File | Shape | Consumed by |
|---|---|---|
| `gpu_db.json` | `aliases` (name-normalisation substitutions) + `gpus: [{id, name, vendor, arch, vram, score, dlss, fsr, xess, frame_gen, reflex, rt}]`. `score` normalised so RTX 4080 SUPER @ 1440p High = 100. | `engine.match_gpu` |
| `cpu_db.json` | `heuristic` (fallback scoring constants + generation-inference tables) + `cpus: [{id, vendor, cores, threads, score, hybrid, x3d, ccds, gen}]`. `score` = estimated 64p Conquest FPS ceiling. | `engine.match_cpu`, `_cpu_heuristic` |
| `cfg_commands.json` | `confidence_levels`/`risk_levels` glossaries + `groups` + `commands: [...]` (per-key `summary`, `pros`/`cons`, `risk`, `confidence`, `hardware_policy`). | `engine._build_cfg` via `Database.command()` |
| `ingame_settings.json` | `profsave` docs + `presets` (esports/competitive/balanced/quality) + `settings: [{id, label, menu, type, options, cost, impact, tradeoff, note, profsave_key, profsave_scale, never_write, confidence}]`. Largest file (~54KB). | `engine.recommend`, `costs.cost_for`, `ui/app.py` detail pane |
| `system_tweaks.json` | `tweaks: [{id, label, scope, trigger, severity, why, how, value}]`, `why`/`how` support `{placeholder}` templating. | `engine._evaluate_tweaks` |

**Note the two separate "cost" concepts**: `setting["cost"]` (numeric curve,
`costs.py`, drives the actual FPS math) vs `setting["impact"]` (a coarser
qualitative int shown in the UI's per-setting detail pane via `_cost_label`).
They are populated independently in the DB — if the FPS model and the
detail-pane label ever visibly disagree for a setting, check both fields.

**`confidence: "unverified"`** (new, `ingame_settings.json` only): marks a setting
proposed by BF6 Tuner's own tooling rather than sourced from a confirmed BF6
profile dump — see `unverified_settings_note` in the file. Every such entry
*deliberately* has no `profsave_key`, so `writer.profsave_plan`'s "only touch
keys already present" rule means it structurally can never be written to
PROFSAVE_profile until someone confirms the real key and removes the flag.
The UI surfaces this with an "(unverified)" label suffix and a badge in the
detail pane — never presented as researched fact.

**Not encrypted, deliberately.** This used to ship as an AES-256-GCM blob
(`crypto.py`, removed 2026-09-08) with a fresh random key baked into a
build-generated `_keyring.py` on *every* build. The data itself is public BF6
hardware/settings reference info, not a secret worth protecting, so that
bought "casual copy/tamper protection" for nothing sensitive — at the real
cost of guaranteeing a byte-different, never-before-seen file hash on every
single build (even a build with zero source changes), which is exactly the
kind of thing Windows SmartScreen/Smart App Control has no reputation for
and can block outright. `packaging/build.py` now just validates the five
JSON files parse (`verify_data`) and `bf6tuner.spec` bundles `data/*.json`
into the frozen app's resource root as-is; `database.load()` reads them the
same way whether frozen or not. See README "About the database" and
"Getting the executable" for the rest of that story — removing this did
**not** fully fix the SmartScreen block by itself (that's a signing/
reputation gap, not a "the file is encrypted" heuristic); it just removes
one guaranteed source of build-to-build hash churn and a chunk of code to
maintain.

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
- Both the **settings table and the User.cfg table** group their rows by
  category (`choice.menu` / `command["group"]`) with clickable, collapsible
  header rows (`_make_header_row`, shared), a search box that overrides
  collapse state and hides non-matching groups entirely
  (`_apply_settings_filter`/`_apply_cfg_filter`), a "jump to category" combo,
  and expand/collapse-all. Row structure (including header rows) is still
  built exactly once (same "rebuilding destroys the widget mid-signal"
  constraint as before) — a parallel `self._settings_row_kind` /
  `self._cfg_row_kind` list (`[("header", menu) | ("choice"/"line", id), ...]`)
  is what every row-index lookup (detail pane, `_goto_frame_limit`, reveal/
  jump) now goes through instead of indexing straight into `rec.settings`/
  `rec.cfg`, since header rows shift those indices around.
- The header's **update button/banner** is populated by `UpdateCheckWorker`
  (same off-thread pattern as `DetectWorker`), fired once silently at startup
  and again on demand via "Check for updates"; `UpdateDialog`
  (`ui/update_dialog.py`) renders the changelog and links out to GitHub -
  there is no in-app download.
- **Visual polish pass** (2026-09-07): action-bar and header buttons carry
  `QStyle` standard icons (no asset files - `self.style().standardIcon(...)`,
  themed by Qt's active style); the window/taskbar icon comes from
  `app_icon()` (see `icon.py` above); a single reference-counted
  `busy_indicator` (`_busy_start`/`_busy_stop`) covers both the startup
  hardware-detect and update-check workers, since they run concurrently and
  either one finishing must not hide the indicator while the other is still
  running; hardware labels get an italic/dimmed "detecting..." state while
  `DetectWorker` runs; a genuinely clean warnings/checks tab renders "✓ ALL
  CLEAR" instead of a neutral "INFO" badge; and both searchable tables show
  an explicit "No matches for ‹query›" hint (`_show_no_matches_hint`,
  repurposing the existing note label) instead of silently going blank.
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

`build.py` pipeline: validate the plain-JSON DB (`verify_data`) → generate
Windows version resource → write `_build_info.py`
(`write_build_info`, the commit SHA `update.py` compares against GitHub) →
optional PyArmor obfuscation (`--obfuscate`) → PyInstaller via `bf6tuner.spec`. The spec
builds **two EXEs from one Analysis**: `BF6Tuner` (GUI subsystem, no
stdout — double-click) and `BF6Tuner-cli` (console subsystem) — Windows
fixes GUI-vs-console at link time, so one binary can't do both.
`make_icon.py` hand-renders the `.ico` with no image library dependency
(manual PNG/ICO byte encoding). `UPDATE.bat` at repo root pulls, pushes
(triggering the GitHub Actions cloud build), and rebuilds both local
executables.

`.github/workflows/build.yml`'s `build` job also publishes/updates a single
rolling GitHub **Release** tagged `latest` on every push to `main` (`gh
release create`/`upload`, `contents: write` permission) — a public,
permanent, unauthenticated download URL, which is what
`bf6tuner.update.download_update()` fetches from. This is separate from,
and in addition to, the `actions/upload-artifact` step just above it: the
Actions artifact needs an authenticated API call and expires after 90 days,
neither of which the in-app self-updater can rely on.

---

## 6. Tests (`tests/`)

`test_engine.py` (largest) covers matching, thread policy, FPS model, cfg
rendering, and the full override system. `test_compare.py` covers the diff
logic and additive frame-time model. `test_paths.py` covers the file-finding
edge cases (per-account subfolders, Saved Games, override precedence).
`test_restore.py` covers the backup/restore atomicity guarantees. All
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
- **The database is plain JSON, not encrypted — and that's deliberate, not
  an oversight.** See §3. It's public reference data, not a secret; encrypting
  it only bought a fresh random key baked into every build, which guaranteed
  a never-before-seen file hash (and a SmartScreen/Smart App Control flag) on
  every single build. Don't re-add encryption "for consistency" without
  re-reading that rationale.
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
- **Checking for an update never auto-downloads; installing one, if the user
  explicitly clicks "Download and install now," does — and that's the one
  deliberate exception, not a quiet erosion of the policy.** `check_for_update()`
  still only ever compares commits; nothing happens from *that* automatically.
  Actually fetching and applying a build is real, user-initiated
  (`update.download_update`/`apply_update_and_relaunch`, added 2026-09-08),
  gated to a frozen build only, and always offered *alongside*, never instead
  of, the manual "Open GitHub Actions build" link — the user still always has
  the option to look at what they're getting first.

---

## 8. Work log

Add a dated entry for every session of work — what changed, why, and
anything the next session needs to know. Most recent first.

### 2026-09-08 (26) — Real self-update: download, replace, relaunch

User asked for the Update button to actually download the latest version,
and separately asked whether an installer would be better for managing
that plus settings/config. Answered the installer question first (settings
already persist in `%APPDATA%` regardless of install method; an installer
would add a Start Menu entry and uninstaller but doesn't dodge SmartScreen
and is real extra build/maintenance surface) — user picked portable
self-update over building a real installer.

- **The real prerequisite, not a detail**: GitHub Actions artifacts need an
  authenticated API call and expire after 90 days - unusable for a shipped
  app's own updater without embedding a credential. Added a `gh release
  create`/`upload` step to `build.yml` (`contents: write` permission) that
  publishes/updates a single rolling release tagged `latest` on every push
  to `main` - a public, permanent URL, alongside (not instead of) the
  existing Actions artifact upload. Matches the project's existing
  "every push is a build" model rather than introducing real version tags.
- `update.py`: new `SelfUpdateError` (raises - this is a button press, not
  the passive startup check, same reasoning as `BenchmarkError`), `find_asset_url`
  (pure, testable - looks up one release asset's URL, same split as
  `parse_compare`), `download_update()` (fetches the current exe's
  counterpart from the `latest` release into a temp path, sanity-checks the
  bytes look like a real PE executable - size + `MZ` magic - before
  accepting them, since a truncated download or a GitHub outage HTML page
  must not get "installed"), and `apply_update_and_relaunch()` (writes a
  tiny detached `.bat` that polls `tasklist` for this process's PID to
  disappear, then moves the new exe into place, relaunches, deletes
  itself - Windows won't let a running process overwrite its own file, so
  there is no cleaner way to do this from inside the process being
  replaced). Both refuse outright on a source checkout (`sys.frozen` gate)
  - a checkout has nothing for this to swap; `git pull`/`UPDATE.bat` is
  the right tool there, unchanged.
- `ui/update_dialog.py`: new "Download and install now" button, shown only
  when frozen, alongside the existing "Open GitHub Actions build" link -
  never instead of it, so the user can still always look before they leap.
  A `_DownloadWorker(QThread)` keeps the fetch off the UI thread; on success
  it triggers the swap-and-relaunch then calls `QApplication.instance().quit()`
  so the detached helper script's wait condition (this process's PID
  disappearing) is actually met.
- Verified what's verifiable without a real Windows relaunch: the dialog
  constructs cleanly offscreen both with and without `sys.frozen` set
  (confirming the button only appears when it should), `find_asset_url`
  against hand-built release payloads, and that both self-update functions
  correctly refuse a non-frozen run. **Not verified**: an actual live
  download + swap + relaunch end to end - that needs a real frozen exe on
  a real Windows machine, not this environment. Ask the user to try it
  once a `latest` release exists (first push after this lands) and report
  back before trusting this fully.
- All 201 tests pass (196 + 5 new).
- Also asked about, not started this session: full in-game settings
  coverage including keyboard/mouse keybindings. Real scope, and blocked on
  the same rule as the `unverified` settings mechanism - BF6's keybind
  storage format (profsave keys, if any) is not something to guess at; it
  needs a real reference (a profile dump showing bind entries, or
  documented schema) before any code gets written. Follow up once the user
  has that.

### 2026-09-08 (25) — Preset/target no longer hardcoded to Competitive + overlay-on every launch

User: "make sure the app detects the current config... every time it's
opened it opens with competitive settings and show fps turned on even if I
have completely other settings running." Real bug, not a misunderstanding —
`_build_sidebar` hardcoded `button.setChecked(name == "competitive")` and
the overlay checkbox's `default` literal was `True`, unconditionally, every
launch, no matter what was chosen last time or what's actually applied
in-game.

Root design question first: is this "detect from the real config" or
"remember what was chosen"? Answer differs per field:
- **Resolution/refresh/HDR were already correctly auto-detected** from real
  hardware (`_on_detected`) - not part of the bug, left alone.
- **VRR, "I stream/record", frame generation, Thread.* overrides, legacy
  keys, and the FPS overlay have no detectable signal at all** - there is no
  Windows API for "is VRR enabled," and the FPS overlay isn't even a BF6
  setting tracked in the save file (confirmed: no entry in
  `ingame_settings.json` mentions overlay/fps). For these, "replicate
  current config" can only honestly mean "remember what I chose last time,"
  not detection.
- **The preset is different** - the actual current in-game settings *can*
  be read (`compare.py` already does this for the comparison tab), so a
  genuine best-effort detection is possible: which preset needs the fewest
  changes against what's really saved.

Implemented both, cleanly separated:
- `prefs.py`: new `load_target()`/`save_target()` (a `target.json`, same
  pattern as the existing overrides files). Module docstring updated - this
  is now the one deliberate exception to "everything else is re-derived."
  `ui/app.py`: `_apply_persisted_target()` restores it during sidebar
  construction (still under `_loading = True`, so no premature refresh);
  `_save_target()` runs at the end of every `refresh()` (which already
  early-returns while `_loading`, so this only ever fires on a real change).
- `compare.py`: new `closest_preset(db, profile, base_target, profsave,
  user_cfg, overrides=, cfg_overrides=)` - tries every preset, keeps whichever
  needs the fewest `comparison.changes`, returns `None` if there's nothing to
  compare against (no PROFSAVE_profile found) rather than guessing blind.
  Deliberately placed in `compare.py`, not `ui/app.py`, so it is unit-testable
  without Qt (`ui/app.py`'s `_detect_closest_preset` is now a 3-line
  delegate). Wired into `_on_detected`, gated on `not self._has_persisted_target`
  - only a genuine first-ever launch guesses from the real profile; every
  launch after that uses the remembered choice instead.
- Verified the "fewest changes" heuristic actually behaves sensibly before
  trusting it: ran all 4 presets against `test_compare.py`'s `ULTRA_PROFSAVE`
  fixture (everything maxed) by hand - `quality` needs 8 changes,
  every other preset needs 16-18. Not a coincidence; that's exactly what
  "closest preset" should mean. Turned into
  `test_closest_preset_picks_the_preset_with_fewest_changes` (writes
  `ULTRA_PROFSAVE` to a real `tmp_path` file, since `closest_preset` goes
  through `from_paths` internally) plus a "no file -> None, don't guess"
  test. Also added `test_target_round_trip` (`tests/test_paths.py`, same
  `monkeypatch.setenv("APPDATA", ...)` pattern as the other prefs tests).
- All 196 tests pass (193 + 3 new). Could not verify the actual UI
  visually or interactively (same offscreen-`MainWindow` crash noted in
  entry (22) - unrelated, still unfixed, still doesn't affect the real app)
  - this is verified at the logic layer (`compare.closest_preset`,
  `prefs.load_target`/`save_target`) but not by clicking through the real
  window. Ask the user to confirm the preset/checkboxes actually stick
  across a real relaunch.
- Followed the standing build/release workflow.

### 2026-09-08 (24) — A stale local `_build_info.py` was lying to the update checker

User ran `run-from-source.bat` after this session's changes and got "Update
available (23 commits)" for code that *was* the tip of `main` — every commit
listed as "ahead" was one already made and pushed earlier this same session.

Root cause: `src/bf6tuner/_build_info.py` (gitignored, `packaging/build.py`-
generated) existed locally on disk, left over from a **local build run on
2026-09-07** — before the "always use the GitHub Actions artifact, never a
local build" workflow was settled ([[bf6-build-workflow]]). Nothing ever
deletes this file once written (it's gitignored, so `git` operations don't
touch it), and `update.py`'s `local_commit()` checked for it *before* falling
back to `git rev-parse HEAD` — unconditionally, not gated on `sys.frozen` —
so a source checkout run (`run-from-source.bat` / `python -m bf6tuner`)
picked up that old build's baked-in SHA instead of asking git directly, even
though the function's own docstring said it "falls back to asking git
directly." The docstring described the intent; the code didn't implement it.

Fixed properly, not just papered over:
- Deleted the stale `_build_info.py` (immediate fix for this machine).
- **Also fixed the actual bug**: `local_commit()` now checks `sys.frozen`
  *first* — only a genuinely frozen build reads `_build_info.py` at all; a
  source checkout always asks git directly, full stop, regardless of
  whether a stale generated file happens to be sitting there. Verified both
  ways: deleted the file and confirmed `local_commit()` matches
  `git rev-parse HEAD`; *recreated* the stale file with the old 2026-09-07
  SHA and confirmed `local_commit()` still returns the real current HEAD,
  not the stale one — proving the fix, not the deletion, is what matters.
- This will recur for any source checkout that has ever had a local
  `packaging/build.py` run against it, on any machine. Nobody needs to
  remember to delete `_build_info.py` by hand anymore — the code now does
  the right thing whether or not it's there.
- No test previously exercised `local_commit()`'s branching (only a smoke
  test that it returns a string, still passes); network-dependent
  `check_for_update()` is documented as exercised by hand, not in CI, and
  this was found and fixed the same way. All 193 tests still pass — this
  change touches no tested behavior directly, just which branch runs first.

### 2026-09-08 (23) — Audited the tuning data itself, fixed what an audit can safely fix

User asked "any other settings we could tweak" after the visual pass; picked
"the BF6 game settings the app recommends" over "the app's own look," then
picked all of: GPU/CPU audit, preset tuning, new commands/tweaks, promoting
`unverified` settings. Did the self-contained part (the audit); the other
three are blocked on the user (which preset feels off and how, what
command/tweak they have in mind, whether they have a real profile dump) —
asked, not yet answered as of this entry.

Audit method: structural checks across all 5 datasets (duplicate
ids/keys, GPU score-ordering inversions within a generation) rather than
trying to recall live hardware-market state from memory, which risks
asserting something that stopped being true after the model's training
cutoff. Two real findings, both fixed:

- **Dead CPU entry, deleted**: `cpu_db.json` had `"id": "ryzen 9 9950x3d2"`
  (score 228) sitting next to the real `"ryzen 9 9950x3d"` (score 212).
  `match_cpu` substring-matches against the detected CPU name string — no
  real Windows-reported name contains the literal substring `9950x3d2` (not
  a real AMD product), so this entry could never match anything. Confirmed
  via `db.cpus` inspection before deleting, not just eyeballing the id.
- **3 settings silently missing the `unverified` flag, now added**:
  `display_mode`, `texture_filtering`, `camera_shake` had no `profsave_key`
  *and* no `confidence` field at all — meaning `writer.profsave_plan` already
  correctly never wrote them (the "only touch keys already present" guard is
  structural, not gated on the flag), but the UI's `"(unverified)"` badge
  (`ui/app.py:1190`, gated strictly on `confidence == "unverified"`) never
  showed for them, so they looked exactly like fully-confirmed settings to a
  user despite being just as unconfirmed as `sharpening`/`view_distance`/
  `reflection_quality`/`weapon_fov`. Verified `resolution` and `upscaler`
  (also keyless) are legitimately different — they're the documented
  special-cased ids handled elsewhere in `engine.py`, not silently dropped —
  by grepping for their id strings in `engine.py`/`writer.py`/`compare.py`
  and finding real handling, then doing the same grep for the other three
  and finding *nothing*, confirming they were plain oversights, not
  intentional special-casing.
  - Added `"confidence": "unverified"` plus the standard disclaimer sentence
    to each entry's `note`, matching the existing four exactly.
  - New test `test_newly_flagged_unverified_settings` (parametrized, separate
    from `UNVERIFIED_IDS` because `display_mode`/`texture_filtering` only
    have 3 enum options, not 4, so the existing tests' `value=3` override
    shape doesn't fit them) covers all three: flagged, overridable, never
    reaches a `profsave_plan` write.
  - `unverified` count is now 7, not 4 — updated the one place that stated a
    count (README's database table).
- Confirmed **no duplicate ids/keys and no score-ordering inversions**
  anywhere else across `gpu_db` (95 GPUs), `cpu_db` (61 CPUs after the
  deletion), `cfg_commands` (39), `system_tweaks` (14), `ingame_settings`
  (39) — checked programmatically, not by eye.
- All 193 tests pass (190 + 3 new). Followed the standing build/release
  workflow.
- **Not done, waiting on the user**: preset target-curve tuning, new
  `cfg_commands`/`system_tweaks` entries, promoting the (now 7)
  `unverified` settings to confirmed — all need specifics only the user has
  (a felt preset complaint, a specific command/tweak, a real profile dump).
  Don't guess at any of these three from general knowledge; that's exactly
  the "propose without fabricating" line this file and the settings' own
  `unverified_settings_note` already draw.

### 2026-09-08 (22) — Visual polish pass: elevation, hover/pressed/focus states

User asked to "make the app look even better" (picked from the same
"what's next" menu as entry (21)'s PresentMon message fix). Scoped as a
`theme.py`/`app.py` styling pass, informed by the `dataviz` skill's stat-tile
and meter guidance (proportional hero figures, a meter's unfilled track
should be a lighter step of its own ramp, not flat black) rather than
guessing colors.

- **Card elevation, two parts**: `QFrame#Card` background is now a subtle
  vertical gradient (`BG_RAISED_TOP` → `BG_RAISED`) with a slightly brighter
  `BORDER_LIGHT` instead of flat fill + the same border as everything else,
  *and* `card()` (the shared factory in `app.py`) now applies a real
  `QGraphicsDropShadowEffect` per card — QSS has no `box-shadow` equivalent,
  so the depth had to be done in Python, not the stylesheet.
- **Interactive states that were simply missing before**: buttons had a
  hover state (border-color only, no background change) and *no* pressed
  state at all; inputs (`QComboBox`/`QSpinBox`/`QLineEdit`) had no `:focus`
  styling whatsoever, so keyboard/tab focus fell back to Qt's default
  platform focus rect, which doesn't match a custom dark theme. Added
  `BG_HOVER`/`BG_PRESSED` fills for buttons (including `Primary` and
  `Preset`), and an `ACCENT` focus border for every input, plus a checkbox
  indicator hover state.
- **Tabs**: selected tab now gets a 2px `ACCENT` bottom underline (was a
  flat background swap only) — reads more like an active-state indicator,
  less like just "a different panel."
- **Meter track**: `QProgressBar`'s unfilled track was `BG_SUNKEN` (flat,
  near-black) — changed to `ACCENT_TRACK`, a lighter step of the accent
  ramp, so a partially-filled bar reads as "on the same ramp" per the
  meter contract in the `dataviz` skill rather than "blue chunk on black."
- **Hero figures** (`QLabel#Hero` — the four big FPS numbers): 34px → 37px
  with `-0.5px` letter-spacing for a tighter, more confident numeral read.
  Kept proportional (not tabular) figures, which was already correct — Qt
  style sheets don't support `font-variant-numeric` and the default is
  proportional anyway, matching the skill's guidance for a standalone value.
- **Verification, and its limits**: could not construct the real
  `MainWindow` under `QT_QPA_PLATFORM=offscreen` to visually smoke-test —
  it crashes on construction with no stderr output and a bare exit code 127,
  confirmed **pre-existing** (identical on the last commit before this
  session's edits, via `git stash`) and NOT something introduced by these
  changes. Worse than the already-known "offscreen can't verify visuals"
  limitation ([[bf6-architecture]] / this file) - this is a hard crash, not
  just unreliable rendering. Not investigated further this session (out of
  scope for a styling pass, and doesn't affect the real shipped app - it
  only happens under headless/offscreen, which no real user hits). **Flagged
  for whoever next needs offscreen Qt testing in this repo**: don't assume
  `MainWindow(db)` is constructible offscreen without checking first.
  Instead verified narrowly: `theme.STYLESHEET` parses with no Qt errors
  under offscreen, and `card()` (with its new drop-shadow effect) constructs
  and shows cleanly standalone. **Real visual judgment still needs an actual
  screenshot from the user** — same caveat as every previous polish pass in
  this file.
- All 190 tests pass (no test exercises `theme.py`/styling directly).
  Followed the standing build/release workflow (GitHub Actions artifact).

### 2026-09-08 (18) — Removed database encryption

- User asked to "re-think" the app and make it unencrypted so Windows would
  stop blocking it. Re-checked the actual root cause first rather than just
  doing it: the SmartScreen/Smart App Control block is a pure reputation gap
  (confirmed on 2026-09-07 — see entry (17) — Defender's own detection
  history showed nothing, cloud protection works fine, there's just no trust
  for a hash nobody else has run). Encryption wasn't the cause of *that*
  specifically, but it was one guaranteed source of the underlying problem:
  `packaging/build.py` baked a fresh random AES key into `_keyring.py` on
  every single build, so even a build with zero source changes produced a
  byte-different file. Asked the user whether to proceed given that removing
  it wouldn't fully fix the block by itself (they'd still need
  `run-from-source.bat` or a code-signing cert for a clean double-click) —
  they chose to remove it anyway, on the merits (the data is public BF6
  reference tables, not a secret; less code to maintain).
- Deleted `src/bf6tuner/crypto.py` and `tests/test_crypto.py`. `database.py`
  now always reads plain JSON — from the frozen resource root's bundled
  `data/` in a shipped build, or the repo's `data/` in a source checkout —
  no bundle, no key, no `BundleError`/`crypto` import (new `DatabaseError`
  replaces it).
- `packaging/build.py`: `prepare_bundle()` replaced with `verify_data()`
  (just checks the five JSON files parse); no more `_keyring.py` generation.
  Dropped the now-meaningless `--bundle-only` flag.
- `packaging/bf6tuner.spec`: `datas` now lists the five `data/*.json` files
  directly (bundled under `data/` in the frozen app) instead of
  `packaging/_build/bf6tuner.db`; dropped `bf6tuner._keyring` from
  `hiddenimports`.
- Dropped the `cryptography` dependency from `requirements.txt` and the CI
  workflow's test-dependency install; dropped the now-pointless "Verify the
  encrypted bundle builds" CI step.
- Updated `.gitignore` (removed the `_keyring.py` line), README ("About the
  database" section rewritten, no more "About encrypted"), and this file
  (§1 module map, §3, §5, §6, §7 design-decisions list).
- All tests pass locally (`PYTHONPATH=src python -m pytest tests -q`,
  5 fewer than before since `test_crypto.py` is gone).
- Next: produce a fresh exe via the GitHub Actions artifact per the usual
  workflow, and confirm with the user whether it's still SmartScreen-blocked
  on first run (expected — see the entry above and README) or whether they
  want to scope a code-signing certificate next.
- Built and fetched the fresh artifact same session; separately, while
  testing, found and fixed a stale `presentmon_exe` pref pointing at
  `dist/BF6Tuner.exe` itself instead of PresentMon (user-side misconfig, not
  a code bug — surfaced as "usage: bf6tuner" in the "Recording failed"
  dialog, which is this app's own `argparse` prog name, proving the
  "PresentMon" it launched was actually itself). Re-pointed it at the real
  `PresentMon-2.5.1-x64.exe` the user had in Downloads.

### 2026-09-08 (19) — PresentMon "access denied" isolated to the machine, not the app

Continuing the benchmark-elevation saga from entry (15)/(14): with the
`presentmon_exe` pref fixed (above), recording still failed with "PresentMon
still needs administrator rights even targeting PID directly" — same
symptom as before, but this time cleanly isolated:

- Confirmed BF6 Tuner was already running elevated (Administrator) and still
  failed — reproducing the entry-(15) anomaly (child process should inherit
  the parent's elevated token; it didn't help).
- **Isolating test**: ran PresentMon *directly* from an elevated PowerShell,
  completely bypassing BF6 Tuner's subprocess launch —
  `& "PresentMon-2.5.1-x64.exe" --process_id <pid> --output_file ... --timed 10 ...`
  — got the identical `error: failed to start trace session: access denied.`
  **This rules out a bug in `benchmark.py`'s subprocess launch** — the
  problem is not how BF6 Tuner invokes the child process.
- Checked `whoami /priv | findstr SystemProfile` in that same elevated
  shell: `SeSystemProfilePrivilege` **is present** in the token, just shown
  `Disabled` — which is normal/expected (most privileges sit disabled until
  the calling process enables them) and does *not* indicate the right was
  stripped by Group Policy (if it had been, the privilege wouldn't appear in
  the list at all).
- **Correction, same session: this is a personal PC, not IT-managed.** The
  Windows 11 *Enterprise* edition led to a wrong first guess (corporate
  EDR/Group Policy blocking ETW regardless of local admin) — the user
  corrected this directly. Retracted; don't assume "Enterprise edition"
  implies "corporate-managed machine" again, here or elsewhere.
- **Revised hypothesis, not yet tested**: on a personal PC with an RTX 5090,
  the more likely explanation is a *competing ETW consumer* already holding
  the same present/frame-time provider — NVIDIA's app overlay (Instant
  Replay/Performance Overlay), GeForce Experience, Xbox Game Bar, or
  RTSS/MSI Afterburner all hook the same present events, and a session
  already owned by one of those (possibly running as a service under a
  different account) can produce `access denied` for a second consumer even
  from a fully elevated caller — `--stop_existing_session` only helps for a
  *same-named, same-owner* stale session, not a live foreign one. Secondary
  possibility: third-party antivirus (not just Defender) blocking
  ETW/performance-tracing APIs as a heuristic, same mechanism as the
  corporate-EDR guess just without the corporate part. A stuck leftover
  session from an earlier crashed capture (clears on reboot) is a simpler
  third possibility.
- **Root cause found and confirmed, later the same day (resumed session, ran
  directly in an elevated shell instead of relaying commands)**: `logman
  query -ets` showed a `PMService` trace session running, and
  `Get-Service`/registry lookup identified it as belonging to a fully
  *installed* **Intel(R) PresentMon 2.5.1.0** app (Windows Installer product
  `{1CF8EE31-DBD1-4E97-8C61-AF09822459B6}`), not just the standalone console
  exe the user thought they were using — the MSI in Downloads had actually
  been run, installing a `PresentMonSharedService` Windows service plus a
  `PMService` ETW Data Collector Set. That's the "competing ETW consumer"
  guessed above, confirmed.
  - Stopping the service (`Stop-Service PresentMonSharedService`) did **not**
    stop the session — `PMService` stayed `Running` in `logman query -ets`
    after the service was `Stopped`. The session outlives its owning process.
  - `logman stop "PMService" -ets` → **"Access is denied. Try running this
    command as an administrator"** — from a shell independently verified
    elevated (`([Security.Principal.WindowsPrincipal]...).IsInRole(...Administrator)`
    → `True`). So the session's ACL denies control to the Administrators
    group itself, not just to a non-elevated caller — the same shape of
    "elevated but still denied" anomaly as the original PresentMon failure,
    now reproduced one layer down on the session itself.
  - Fully uninstalled the package (`msiexec /x {1CF8EE31-...} /qn`, exit code
    0) at the user's confirmation. Service and its uninstall-registry entry
    both gone afterward. **`PMService` was still `Running` in `logman query
    -ets` even after the uninstall** — genuinely orphaned, not just
    service-tied.
  - Checked and ruled out two persistence mechanisms that would explain a
    session surviving this long: no `HKLM:\SYSTEM\CurrentControlSet\Control\
    WMI\Autologger\PMService` key (not a boot-time autologger) and no
    matching `Get-ScheduledTask` entry. So nothing will resurrect or
    re-arm it — it's just a live kernel session nobody left with rights to
    stop.
  - **Conclusion: only a reboot clears it.** That's the only remaining lever
    for a session with no autologger/task tie and an ACL nothing here can
    touch. Told the user to reboot, then retest recording with the standalone
    `PresentMon-2.5.1-x64.exe` (unaffected by the uninstall — separate file).
    **Not yet confirmed working post-reboot** — that's the next thing to
    verify, not a fresh investigation.
  - Lesson for next time this class of bug shows up in this app: `run_capture`
    could plausibly detect "access denied" + a same-named session already
    `Running` via `logman query -ets` itself and say "a PresentMon-related
    Windows service/trace session is already active — check for a fully
    installed PresentMon app, not just the standalone exe" rather than only
    ever suggesting elevation. Not built; flagged as a possible follow-up if
    this recurs for another user.

### 2026-09-08 (20) — PresentMon still denied post-reboot; likely cause is HVCI, not this app

The orphaned-`PMService`-session theory from entry (19) turned out to be
real but **not the whole story**. After the user rebooted:

- Confirmed via `logman query -ets` that `PMService`/`PresentMonSharedService`
  were genuinely gone (uptime check showed the reboot was ~3 minutes old) —
  that specific leftover-session cause is resolved and won't recur.
- The exact same "access denied" still happened with a fresh PID. Verified,
  this time with actual token inspection (not just `IsInRole`) via a small
  P/Invoke helper (`OpenProcessToken`/`GetTokenInformation` with
  `TokenElevation`): **BF6 Tuner's own process token really is elevated**
  (confirmed `True` for both the PyInstaller bootloader and the unpacked
  child process) — so this still isn't a "not actually elevated" mistake.
- With the game running live (PID from Task Manager), reproduced the
  failure directly from a verified-elevated PowerShell, bypassing BF6 Tuner
  entirely, exit code 6, identical message — confirms again this is not
  `benchmark.py`'s launch code.
- **Explicitly enabled `SeSystemProfilePrivilege`** in that same shell's own
  token via `AdjustTokenPrivileges` (P/Invoke) — `whoami /priv` then showed
  it `Enabled`, not `Disabled` — and launched PresentMon as a child of that
  same process (which inherits the modified token). **Identical failure
  anyway.** This rules out "the privilege just needed enabling" as the
  cause; something is denying the trace session at a level the privilege
  bit doesn't reach.
- Checked for third-party AV/EDR (`root\SecurityCenter2` +
  process-name matching against every common vendor): **none found, only
  Windows Defender** — ruling out a third-party security product.
- `Get-MpComputerStatus` / registry checks turned up two active non-default
  security layers on this machine: **Smart App Control enforced**
  (`VerifiedAndReputablePolicyState = 1`, already known from entry (17)) and
  **Core Isolation / Memory Integrity (HVCI) enabled**
  (`HKLM:\SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\
  HypervisorEnforcedCodeIntegrity` → `Enabled = 1`).
- **Leading hypothesis, presented to the user, not tested**: HVCI is an
  independently-documented cause of exactly this failure mode for
  PresentMon/RTSS/CapFrameX-style ETW frame-capture tools, unrelated to
  local admin rights. The user was asked whether to test by temporarily
  disabling Memory Integrity (Windows Security → Device security → Core
  isolation, needs a reboot) and **declined** — decided the security
  trade-off isn't worth it for this feature. **So this remains an unverified
  but well-supported hypothesis, not a confirmed root cause** — do not state
  it as fact if this comes up again; it's the best explanation left after
  elevation, orphaned-session, missing-privilege, and third-party-AV were
  all directly ruled out on this machine.
- **Outcome for this app**: the Benchmark tab (real-measurement path) does
  not work on this machine as long as Memory Integrity stays on, and that's
  the user's informed choice, not a bug to keep chasing. The FPS
  *prediction* (the non-PresentMon path) is unaffected and still works
  normally.
- **Follow-up done same day, see entry (21)**: `run_capture`'s PID-targeted
  "still needs administrator rights" message now mentions both confirmed
  real-world causes (lingering PresentMon service/session, Core
  Isolation/Memory Integrity) directly, not just elevation.

### 2026-09-08 (21) — Taught the PID-targeted access-denied message the two real causes found above

Small, direct follow-up to entries (19)/(20), picked by the user from a
"what's next for this app" menu over scoping a code-signing cert.

- `benchmark.run_capture`'s PID-targeted branch (fires when `--process_id`
  targeting *still* gets "access denied" — i.e. the user already tried the
  obvious things) now says outright that elevation may not be the real
  cause, and names both confirmed culprits: a full PresentMon app install
  leaving its `PresentMonSharedService`/`PMService` ETW session running even
  after the service is stopped or the app uninstalled (check `logman query
  -ets`, reboot if found), and Core Isolation/Memory Integrity blocking ETW
  capture regardless of admin rights. Previously this branch only suggested
  relaunching elevated, the game needing elevation too, or the 'Performance
  Log Users' group — none of which turned out to be the real cause in
  practice.
- Deliberately left the by-name (non-PID) branch's message alone — it's the
  first thing a user sees, before they've tried PID targeting, and the
  existing "relaunch elevated" guidance is still the right first step there.
- `tests/test_benchmark.py`'s existing PID-branch test only asserts
  `"still needs administrator"`, `"not by name"`, and the PID number appear
  — didn't need updating, still passes. All 190 tests pass
  (`PYTHONPATH=src python -m pytest tests -q`).
- Followed the standing build/release workflow (GitHub Actions artifact,
  not a local build).

### 2026-09-07 (17) — Smart App Control has no override on this machine at all
User's build kept getting blocked, and this time "Unblock" (which only
clears Mark-of-the-Web, unrelated to Smart App Control) didn't help either,
and Windows Security's Protection History didn't show an actionable entry.
Pulled the actual Smart App Control block event's structured data directly
(`Get-WinEvent ... | .ToXml()` on `Microsoft-Windows-CodeIntegrity/Operational`
event ID 3118) rather than keep guessing from the GUI:
- Confirmed genuinely not malware (`DefenderThreatName` empty, as before).
- `DefenderMadeCloudCall: false` looked like a possible smoking gun (cloud
  reputation check requested but never completed) - checked
  `Get-MpComputerStatus`/`Get-MpPreference` (MAPSReporting=2/Advanced,
  connectivity to `wdcp.microsoft.com:443` succeeds) and cloud protection
  is properly configured and reachable. So this isn't a fixable
  misconfiguration - it's just confirming there's no reputation available
  for a file that exists nowhere else, exactly as the original theory said.
- **Conclusion, stated plainly**: on this machine, with Smart App Control
  enforced and no code-signing certificate, there is no click-through
  override for the compiled .exe. The two previously-given options (turn
  off Smart App Control - one-way; or get a certificate) still stand, but
  there's a third, better one for a personal dev machine that already has
  Python: **run from source**. Smart App Control evaluates standalone
  executables, not scripts interpreted by an already-trusted `python.exe`,
  so this sidesteps the entire problem with no cert, no OS setting changes,
  and no repeated prompts.
- New `run-from-source.bat` (repo root): sets `PYTHONPATH` and runs
  `python -m bf6tuner`, forwarding all arguments - `run-from-source.bat`
  alone launches the GUI, `run-from-source.bat --preset competitive ...`
  behaves like `BF6Tuner-cli.exe`. Verified directly (not just written and
  assumed): ran it with `--preset competitive --print-cfg` and got a
  correctly-rendered `User.cfg` against this machine's *real* detected
  hardware (Ryzen 9850X3D / RTX 5090, matching the user's earlier
  screenshots) - confirms real hardware detection, not the off-Windows
  sample profile, and that argument forwarding works.
- Documented in README's "Running it" section. No version bump - this
  doesn't touch `src/bf6tuner/` or the built executable at all, so nothing
  needed rebuilding/pulling via the standing workflow this time.

### 2026-09-07 (16) — Diagnosing blind: make targeting mode visible up front
User tried the PID-targeting build (elevated BF6 Tuner, as before) and got
the *identical* error message. Rather than guess again whether PID
targeting actually engaged, made the app show which mode it's using
immediately, not just inferable from an eventual error 60 seconds later:

- Status bar now says `"Recording for {duration}s (PID {pid})"` or
  `"... (process name '{name}' - no PID found)"` the moment recording
  starts - visible well before any result comes back.
- `run_capture`'s error messages all state which targeting mode was used
  (`target_desc`), and the elevation-denied message is now two different
  texts depending on whether a PID was already in use: if PID targeting
  *still* gets access-denied, the message says so plainly ("this isn't
  just a name-resolution requirement") instead of repeating the by-name
  explanation verbatim, which would actively mislead if that's not what
  actually happened.
- 3 new tests (195 total): the PID-still-denied message text, and that
  both non-elevation error paths correctly state "PID {n}" vs "no PID was
  found" depending on which was used.
- This is a diagnostic-visibility change, not a claimed fix - still
  waiting on the user's next attempt to find out whether `find_running_game_pid()`
  is actually finding a PID at all (most likely culprit if the same exact
  message reappeared) or whether PID targeting also hits the elevation
  wall, which would be a materially different and more surprising finding.
- Followed the standing build/release workflow.

### 2026-09-07 (15) — PID targeting, since admin-elevating the parent didn't help
User tried the previous entry's fix (ran BF6 Tuner itself as Administrator)
and it made **no difference** - same "access denied" from PresentMon. That
contradicts the normal Windows rule that a child process inherits its
parent's elevated token, so something about PresentMon's own privilege
check apparently isn't satisfied by that alone. Rather than keep guessing
at the elevation angle, went after what PresentMon's own warning actually
said the problem was: resolving a process **by name** needs elevation for
short-lived processes or ones "started on another account" - a known PID
sidesteps that resolution step entirely.

- New `paths.find_running_game_pid()`: same `tasklist.exe /FO CSV /NH`
  approach as the existing `is_game_running()`, parsed properly with
  `csv.reader` (not substring matching) to pull out the PID column: never
  raises, returns `None` on any failure or off Windows.
- `benchmark.py`: `build_args`/`run_capture` gained an optional `pid`
  param. New `DEFAULT_PID_ARGS_TEMPLATE` (`--process_id {pid}` instead of
  `--process_name {process}`); `build_args` picks it automatically when a
  `pid` is supplied and no explicit template overrides it.
  `BenchmarkWorker`/`start_benchmark()` now look up the running game's PID
  via the new `paths` function and pass it through - falling back to
  name-based targeting only if the PID lookup itself fails.
- 7 new tests (193 total): `build_args`'s PID-vs-name template selection
  (including that an explicit template still wins over the pid default),
  and `find_running_game_pid`'s CSV parsing/not-found/off-Windows/
  broken-tasklist cases - `IS_WINDOWS` monkeypatched rather than relying on
  the actual OS, so these run identically in Linux CI and on Windows (the
  same lesson from two entries ago, applied on the first attempt this
  time).
- Verified the actual wiring end-to-end, off-screen: patched
  `paths.find_running_game_pid`/`is_game_running` and spied on
  `benchmark.run_capture` to confirm `start_benchmark()` really does thread
  a real PID through to a `--process_id 5555 ...` argument list, not just
  that the isolated function works.
- **Still unverified**: whether PID targeting actually clears the access-
  denied error against a real running game - that requires the user to
  test it. If it doesn't, the elevation requirement may be more
  fundamental than name-resolution (e.g. genuinely needs the *game*
  process itself, not just PresentMon, to be running elevated, or needs
  SYSTEM rather than plain Administrator) - worth asking the user to try
  running PresentMon directly from an elevated cmd prompt (no BF6 Tuner
  involved) targeting the game's real PID, as the next isolating test if
  this doesn't resolve it.
- Followed the standing build/release workflow.

### 2026-09-07 (14) — PresentMon needs elevation even as the standalone tool
User got the *right* PresentMon this time (standalone console tool, no
WinError 740) but a new, different failure: PresentMon itself exits with
code 6 and explains that it needs elevated privilege to reliably identify
and target another process by name via `--process_name` - a distinct
requirement from the previous entry's "wrong executable" problem. This is
apparently true of the standalone tool in general, not just the installed
GUI app.

- `run_capture` now recognises this by scanning the non-zero-exit output
  for "access denied" / "elevat" and gives the actual fix directly: close
  BF6 Tuner and relaunch it as Administrator (elevation on the parent
  process is inherited by the PresentMon child it spawns), rather than
  just surfacing PresentMon's raw stderr with no guidance. Only the
  Benchmark tab needs this - nothing else in the app touches anything that
  requires admin.
- Considered but did not build: auto-relaunching the app elevated via
  `ShellExecuteW(..., "runas", ...)` when this is detected. Real UX win if
  it works, but real edge cases too (frozen .exe vs. dev `python -m
  bf6tuner` have different relaunch commands, and a wrong self-relaunch
  could confuse more than it helps) - flagged as a possible follow-up, not
  attempted this round.
- 1 new test using the verbatim (trimmed) PresentMon stderr from the real
  failure, via a plain `subprocess.CompletedProcess` (no OSError/.winerror
  involved this time, so no repeat of the prior entry's Linux-CI gotcha).
  186 tests total.
- Followed the standing build/release workflow.

### 2026-09-07 (13) — The elevation test itself only worked on Windows
The previous commit's two new tests passed locally (this machine is
Windows) but **failed CI's "Tests (Linux)" job**: `OSError(22, "...", None,
740)` (the 4-arg constructor form) only populates `.winerror` on real
Windows OS failures - passing it manually on Linux is accepted (no
TypeError) but silently does *not* set `.winerror`, so the test's fake
error fell through to the plain fallback message instead of the
elevation-specific one, and the regex match failed.
- Fixed by setting `.winerror` as a plain attribute assignment after
  construction (`exc = OSError(message); exc.winerror = 740`) instead of
  via the constructor's positional args - confirmed by direct experiment
  that this works identically on any platform, since it doesn't go through
  OS-specific population logic at all, just a normal instance attribute.
- **Process note**: this is caught CI doing its job, not a near-miss -
  every prior commit this session ran `pytest tests -q` locally before
  pushing and that's real coverage, but "passes on this machine" was never
  sufficient for anything touching a platform-conditional stdlib feature
  like `OSError.winerror`, and this repo's CI runs the test suite on Linux
  specifically to catch exactly this class of thing before the Windows
  build job even starts (see `build.yml`'s `test` job gating `build`).
  Worth remembering for any future Windows-specific error-code handling:
  don't trust a constructor argument to set a platform-only attribute:
  set it directly, or skip the test outside Windows.
- 185 tests unchanged in count, both fixed to pass for the right reason on
  every platform. Followed the standing build/release workflow.

### 2026-09-07 (12) — First real-world benchmark error: elevation required
User's first actual capture attempt (against a real PresentMon install)
failed with `WinError 740: The requested operation requires elevation`.
Root cause: they'd pointed "Locate PresentMon..." at
`C:\Program Files\Intel\PresentMon\PresentMonApplication\PresentMon.exe` -
the **installed GUI application**, whose exe is manifested
`requireAdministrator` (it manages the privileged background service) -
rather than the standalone console tool from the Releases page assets,
which doesn't need elevation.

- `run_capture` now catches this specific `OSError` (`.winerror == 740`,
  `_ERROR_ELEVATION_REQUIRED`) and explains exactly what's likely wrong and
  the fix, instead of the previous generic "Could not run PresentMon at
  {path}: {exc}" message that gave no hint what to do about it.
- Chose not to add UAC-elevation support (`ShellExecute` "runas") as an
  alternative fix: capturing stdout/stderr from an elevated child process
  requires real extra plumbing (named pipes or similar - `subprocess.run`'s
  simple output capture doesn't work across an elevation boundary), a UAC
  prompt would appear on every single recording, and the actual fix (use
  the right executable) is simple and already what the docs recommend -
  not worth the complexity for a problem with an easy correct answer.
- **Test-writing note worth remembering**: the first version of the new
  test used `OSError(740, "message")` (2-arg form) and would have passed
  even if the elevation-detection code were broken, because that
  constructor form does **not** set `.winerror` - only the 4-arg Windows
  form does (`OSError(errno, strerror, filename, winerror)`), confirmed by
  direct experiment before trusting the test. A test that passes for the
  wrong reason is worse than no test.
- 2 new tests (185 total): the elevation case matches the real constructor
  shape verified above, plus a control test confirming an unrelated
  `OSError` still gets the plain fallback message, not the elevation one.
- Followed the standing build/release workflow.

### 2026-09-07 (11) — Verified PresentMon's actual current flags; fixed the guess
User asked how to install PresentMon. Rather than answer from possibly-stale
knowledge, fetched the actual current repo (`README-ConsoleApplication.md`
via WebFetch) - and the previous entry's default flags were wrong.

- Confirmed: current Intel PresentMon (2.x) ships three components (service,
  GUI, and a **standalone console application** - a single self-contained
  `PresentMon-<version>-x64.exe`, no service required, matching this
  module's subprocess design). The console app's real flags are
  double-dash: `--process_name`, `--output_file`, `--timed`,
  `--stop_existing_session`, `--no_console_stats`. The previous commit's
  `DEFAULT_ARGS_TEMPLATE` used single-dash 1.x-style flags
  (`-session_name`, `-no_top`, `-terminate_after_timed`) that don't exist
  in the current version - would have failed on first real use.
  `MsBetweenPresents` (the frame-time column `_find_column` already
  prioritised) is confirmed present in 2.x's CSV output, so `parse_csv`
  needed no change.
- Updated `DEFAULT_ARGS_TEMPLATE` to the verified flags and the module
  docstring to record what's now confirmed vs. still assumed.
- 183 tests still pass unchanged - they check argument *substitution*
  (values present in the built arg list), not the literal flag spelling,
  so they didn't need updating, but also couldn't have caught this bug
  themselves. **Worth remembering**: this class of bug - correct code
  shape, wrong external-tool CLI syntax - is exactly what unit tests
  against mocked subprocess calls cannot catch; only checking the real
  tool's own docs (or a live capture) can. If a user reports a benchmark
  capture failing with an "unrecognized argument"-shaped PresentMon stderr,
  the fix is almost certainly here, not in the parsing logic.
- Followed the standing build/release workflow.

### 2026-09-07 (10) — Real performance logging via PresentMon
User asked for auto-logged performance testing to check the FPS prediction
against reality - scoped via AskUserQuestion to "full in-app recording"
(vs. a lightweight external-tool pointer, or trying BF6's own unconfirmed
legacy frame-log cvar first).

- **New `benchmark.py`**: `find_presentmon()` (PATH + the NVIDIA FrameView
  install location + an explicit override, never bundled/downloaded -
  matches `update.py`'s policy on external things), `build_args()` /
  `run_capture()` (subprocess wrapper, `BenchmarkError` on non-zero exit /
  missing output CSV / timeout - PresentMon's own stderr surfaces to the
  user rather than a guess), `parse_csv()` (flexible column-name matching -
  PresentMon's CLI flags and CSV headers have changed across its 1.x/2.x
  generations, so this matches `MsBetweenPresents` case-insensitively with
  fuzzy fallbacks rather than one hardcoded name), `FrameStats`
  (avg/1%-low/0.1%-low FPS via the standard "average of the slowest N%
  of frames" definition), `Recording` (a stats snapshot plus the
  prediction it's checked against - preset, predicted/gpu/cpu FPS,
  bottleneck, hardware), and `save_recording`/`list_recordings`/
  `delete_recording` (`%APPDATA%\BF6Tuner\benchmarks\*.json`, corrupt files
  skipped not fatal - same tolerance as `writer.list_restore_points()`).
- **New "Benchmark" tab**: Locate PresentMon (reuses `prefs.set_override`
  with a new role, `"presentmon_exe"` - no prefs.py changes needed),
  a duration spinbox, Start Recording (guarded on both PresentMon being
  found *and* `paths.is_game_running()` - the opposite precondition from
  every config-writing action, which all require the game to be *closed*),
  a countdown progress bar, and an auto-populated history list of every
  past capture with a measured-vs-predicted delta (colour-coded: green at
  or above prediction, amber within 10 FPS under, red beyond that).
  `BenchmarkWorker` (new `QThread`, alongside `DetectWorker`/
  `UpdateCheckWorker`) runs the capture off the UI thread since it blocks
  for the whole recording duration.
- 25 new tests in `test_benchmark.py` (183 total) - CSV parsing (multiple
  column-name variants, non-numeric/zero rows skipped not fatal, a
  no-usable-data file raises a clear error), the 1%/0.1% low percentile
  math against known synthetic distributions, arg construction, `Recording`
  round-tripping, and `run_capture`'s error paths via a mocked
  `subprocess.run` (non-zero exit, missing output file, timeout) - actually
  launching PresentMon can't be exercised in CI (no game, no ETW capture
  rights on a GitHub Actions runner, PresentMon isn't installed there), so
  the module is structured to make everything *except* the literal
  subprocess call fully unit-testable.
- Verified the UI wiring by hand, off-screen: PresentMon detection/status
  text, the Start button's enabled state reacting to both preconditions,
  the full worker lifecycle via a fake (non-executable) "PresentMon.exe" -
  confirmed `_busy_count`/button-disabled state through a real
  `BenchmarkWorker` run that fails fast (invalid exe), a simulated
  successful finish saving and listing a recording with the tab count
  updating, and delete removing it again. Also hardened
  `_end_benchmark_run` to tolerate being called before the timer exists
  (found by a test calling the finish-handler directly, not reachable via
  the real UI flow, but cheap and consistent with this codebase's general
  defensive style).
- **Not verified**: an actual PresentMon capture against a running game -
  I have no display and no game to test against. The CLI flags in
  `DEFAULT_ARGS_TEMPLATE` target the long-stable common subset across
  PresentMon's versions but are unconfirmed against whatever version the
  user ends up with; a mismatch surfaces as a clear PresentMon-stderr error
  rather than a silent bad capture, and there's no user-facing way yet to
  override the template if the default flags are wrong for their build -
  worth adding an "Advanced" template override field if the defaults
  don't work first try.
- Followed the standing build/release workflow.

### 2026-09-07 (9) — Row height, round 3: 26px was too tight for combo/spin chrome
User flagged (screenshot, "check the column in red") that the Value
column's combo/spin editors looked cramped/hard to read at the 26px
(settings) / 25px (cfg) rows from round 2's "match the font" pass.

- Measured precisely rather than guessing: a settings-table QComboBox's
  `sizeHint().height()` was 22px, a spinbox 23px - both technically fit
  under 26px. The takeaway: Qt's *reported* size hint fitting is not the
  same as *comfortable* rendering - combo/spin box chrome (the dropdown
  arrow, spinner buttons) generally wants more like 28-30px before it
  stops looking squeezed, independent of whether the text itself fits.
  Tried to get a visual screenshot via `widget.grab()` under
  `QT_QPA_PLATFORM=offscreen` first to check directly rather than guess
  again - the offscreen platform doesn't load real fonts or apply the
  stylesheet, so it renders as unstyled tofu boxes and was useless for
  this. Worth remembering: **offscreen Qt smoke tests are for behavior/
  wiring checks, not visual verification** - there is no way to actually
  see rendering output from this environment.
- Settled on 30px (settings) / 29px (cfg) / 29px (category headers) - still
  far more compact than the original 34/32/48/44, but with 6-10px of
  genuine margin over every measured widget's size hint (combobox 22,
  spinbox 23, TableButton 20) instead of a hairline fit.
- 158 tests unchanged (UI-only). Followed the standing build/release
  workflow.

### 2026-09-07 (8) — Named custom profiles
User picked this from a shortlist of feature options (also considered: CLI
override parity, export/import overrides, quick-access folder buttons -
none built yet, still open if wanted later).

- A profile is a saved snapshot of everything the sidebar already
  represents at once: preset, resolution/refresh/every checkbox, and both
  override dicts. New `prefs.py` functions (`load_profiles`/`save_profiles`/
  `save_profile`/`delete_profile`/`rename_profile`, `profiles.json`) mirror
  the existing setting/cfg-override storage pattern exactly. **Zero engine
  changes** - a profile is purely a UI-layer bundle of the same `Target`
  fields and override dicts `recommend()` already accepts; `load_profiles()`
  just replays them onto the sidebar widgets and the live override state.
- New sidebar "Profiles" card (between "Your machine" and "Preset"): a combo
  box plus Load/Save as.../Update/Delete buttons.
- **Design iteration worth remembering**: the first cut auto-applied a
  profile on `currentIndexChanged`. A smoke test caught a real bug this
  causes: Qt only emits that signal when the index *changes*, so re-picking
  a profile you'd already drifted away from (edited settings without
  touching the combo) silently did nothing - exactly the moment you'd most
  want a reload. Fixed by making Load an explicit button and dropping the
  separate "currently loaded" tracking variable entirely - Load/Update/
  Delete now all act on whatever `_selected_profile_name()` (the combo's own
  current text) says, so "loaded" and "selected" can never drift apart from
  each other by construction.
- 5 new tests in `test_paths.py` (prefs.py's existing home) - round trip,
  rename (including the no-op-if-missing case), delete-nonexistent is safe,
  corrupt-file tolerance. 158 tests total.
- Verified the full flow by hand, off-screen, end to end: save while esports
  + HDR + a shadow_quality override, switch away to quality with no
  overrides *without touching the combo*, confirm the combo still shows the
  saved name, hit Load, confirm every field and both override stores came
  back exactly (including the persisted-to-disk copies) - this is the
  scenario that broke with auto-apply and is what proves the fix. Then
  Update (adds a second override, confirmed saved) and Delete (confirmed
  removed, buttons correctly disabled after).
- Followed the standing build/release workflow.

### 2026-09-07 (7) — Row height, round 2: buttons were clipped, tightened further
The previous fix (removing word-wrap + resizeRowsToContents) solved the
giant-row bug but exposed a second one: at the new fixed row heights, the
per-row "Reset"/"Frame limit ->" `QPushButton`s rendered as unreadable
dots instead of text — a screenshot caught it immediately. Root cause:
those buttons inherited the general `QPushButton` rule's `padding: 8px 15px`
(added in the earlier visual-polish pass for the action bar, which sits
outside any table), giving them a natural height taller than the row: Qt
elides button text defensively when it can't fit, including vertically,
not just the more familiar horizontal ellipsis case.

- New `QPushButton#TableButton` style (`padding: 1px 8px`, smaller
  border-radius, 12px font) applied to all three in-table buttons (settings
  reset, cfg reset, cfg's linked "Frame limit ->"). Verified their
  `sizeHint().height()` is 20px against 25-26px rows - comfortable margin,
  not a hairline fit.
- User also asked directly to tighten further ("match the height of the
  font"): `QTableWidget::item` padding 7px→4px vertical, row heights
  34→26 (settings) / 32→25 (cfg) / 30→26 (category headers).
- Verified end to end, off-screen: row heights read back correctly from
  `verticalHeader().defaultSectionSize()`; a sampled reset button's actual
  `.text()` was genuinely `"Reset"` all along (confirming round 1's fix
  didn't corrupt data, only round 2's button padding was rendering it
  illegibly); editor widgets' size hints (22px) also fit inside the new
  row height without clipping.
- 153 tests unchanged (UI-only). Followed the standing build/release
  workflow.

### 2026-09-07 (6) — Fixed a real row-height bug; menu bar; About dialog
User reported (with a screenshot) a settings-table row rendering ~300px
tall - not a subjective "make it nicer" complaint, an actual bug.

- **Root cause**: `_make_table()` had `setWordWrap(True)`, and both
  `_sync_settings_table`/`_sync_cfg_table` called `resizeRowsToContents()`
  at the end of every render. `resizeRowsToContents()` sizes each row from
  its cells' content, including word-wrapped text in the stretch-resized
  "Why" column - and that computation runs against whatever the column's
  width happens to be *at that instant*, which is not guaranteed to already
  match its final laid-out width (this predates my session's changes -
  `resizeRowsToContents()` was already there per an earlier human commit,
  "Auto-fit row heights in settings table" - my grouped-table refactor
  didn't cause it, but made a marginal timing issue into a very visible one).
  **Fix**: word wrap off, `resizeRowsToContents()` calls removed entirely.
  Rows are now a fixed, predictable height (`setDefaultSectionSize`: 34
  settings / 32 cfg / 30 for category headers) and Qt's default text
  elision ("...") handles anything too long for its column - full text was
  always available via the row's tooltip and the detail pane below anyway,
  so nothing is lost. Verified directly: printed `rowHeight()` for the first
  6 rows post-fix, all exactly 34 (30 for the one header row) - no outliers.
- **Menu bar** (`_build_menu_bar`, called from `__init__`): File (Locate,
  Back up, Restore, Export, Save, Apply, Exit), View (Re-detect, Focus
  search, expand/collapse categories, reset overrides), Help (Check for
  updates, GitHub link, About). Every action already existed as a button;
  this adds keyboard shortcuts (Ctrl+L/B/S/E, Ctrl+Shift+R, Ctrl+Return,
  F5, Ctrl+F, Ctrl+Q) and standard-convention discoverability on top - it
  does not replace the action bar.
- **Header bar**: wrapped in a `QFrame#HeaderBar` with a bottom border (it
  used to float directly against the body with no separation), added the
  app icon next to the title, and fixed the subtitle wrapping onto two
  lines for no reason (`dim()` defaults to word-wrap on; overridden off for
  this one label - there is always room for one short line).
- **About dialog** (`show_about_dialog`, `Help > About`): `QMessageBox.about`
  showing version, build commit (`update.local_commit()`), the loaded
  database's version/source, and a GitHub link - the app previously had no
  in-UI way to see any of this beyond the OS title bar text.
- Verified everything by hand, off-screen: row heights (above), menu
  contents for all three menus, About dialog invocation (patched
  `QMessageBox.about` to avoid blocking in a headless script), and
  Ctrl+F/`_focus_search` correctly focusing the active tab's search box.
- 153 tests still pass unchanged - this was all UI wiring/layout, no
  engine/data logic touched. Followed the standing build/release workflow.

### 2026-09-07 (5) — Visual polish pass
Follow-up to the earlier "make the UI much better" request - that session
covered navigation/findability; this one covers the deprioritised half
(icons, spacing/typography, empty/loading states, more deliberate color use).

- **Icons**: relocated the pure-Python reticle-icon renderer from
  `packaging/make_icon.py` into `src/bf6tuner/icon.py` (`render_png(size)`,
  `build_ico(destination)`), with `make_icon.py` now a ~20-line wrapper so
  `python packaging/make_icon.py` and `build.py`'s import keep working
  unchanged. New `ui/app.py::app_icon()` renders 3 sizes (256/64/32 - the
  other three in `icon.SIZES` are for the .ico only, not worth the extra
  render cost for a QIcon Qt will scale anyway) and is set as both the
  `QApplication` and `MainWindow` icon, so a source checkout's taskbar entry
  matches the shipped .exe instead of showing a generic Python icon.
  Measured construction overhead: negligible in practice (see verification
  below). Action-bar and header buttons got `QStyle` standard icons
  (`SP_DirOpenIcon`, `SP_DriveHDIcon`, `SP_DialogResetButton`,
  `SP_DialogSaveButton` ×2, `SP_DialogApplyButton`, `SP_BrowserReload`,
  `SP_ArrowUp`) - zero asset files, themed automatically by Qt's active
  style.
- **Loading state**: a single `busy_indicator` (indeterminate `QProgressBar`
  in the header) is reference-counted (`_busy_start`/`_busy_stop`,
  `self._busy_count`) rather than a plain show/hide flag, because
  `MainWindow.__init__` kicks off `DetectWorker` *and* `UpdateCheckWorker`
  concurrently at startup - a naive show/hide would have whichever job
  finishes first hide the indicator while the other is still running. Also:
  hardware sidebar labels switch to italic/dimmed "detecting cpu..." etc.
  text while `DetectWorker` runs, reverting on completion.
- **Empty states**: the warnings/checks tabs' synthetic "nothing to
  flag/check" entries now use a new `"ok"` severity that renders as
  "✓ ALL CLEAR" in green rather than a neutral "INFO" badge (`_fill_scroll`).
  Both searchable tables (settings, User.cfg) now show "No matches for
  ‹query›. Clear the search to see everything." in the existing note label
  when a filter matches zero rows, instead of just going blank
  (`_show_no_matches_hint`, shared by `_apply_settings_filter`/
  `_apply_cfg_filter`; the note's default text is now saved as
  `_settings_note_default`/`_cfg_note_default` to restore afterward).
- **Spacing/typography**: `card()`'s margins/spacing bumped slightly
  (16/14/16/16→18/16/18/18, spacing 9→10); `QLabel#Title` 18px→19px with a
  touch of letter-spacing; `QPushButton` padding 7px 14px→8px 15px; new
  `QProgressBar`/`QProgressBar::chunk` rules (previously unstyled, would have
  used the raw OS widget against the dark theme).
- 4 new tests (`test_icon.py`, 153 total) parsing the PNG/ICO byte structure
  directly (no image library, matching the module's own policy) rather than
  just trusting it doesn't crash.
- Verified by hand, off-screen: every button's `.icon()` is non-null; window
  icon non-null; `MainWindow` construction including icon rendering measured
  at ~0.13s (negligible); no-match search hint text appears and clears
  correctly on both tables; the busy-indicator ref-counting - **caught a
  test-harness false alarm here**: a plain script driving `MainWindow`
  without calling `app.exec()` never lets queued cross-thread signals
  (`DetectWorker`/`UpdateCheckWorker` finishing) actually invoke their slots,
  so `_busy_count` looked stuck. Confirmed it was the test, not the app, by
  pumping `app.processEvents()` in a loop until the startup workers settled,
  then re-running the start/stop sequence in isolation - correct in both the
  pumped-manually and the real `app.exec()` case. Worth remembering next
  time an offscreen smoke test involving `QThread` signals looks wrong.
- Followed the standing build/release workflow; bumped `__version__`.

### 2026-09-07 (4) — Friendlier message for the shared GitHub rate limit
The user hit "HTTP Error 403: rate limit exceeded" clicking **Check for
updates** in the real app. Root cause: unauthenticated GitHub API requests
share a 60/hour cap *per IP*, and this session's own build-monitoring
(polling `actions/runs` every 15s while watching two CI builds) had burned
through the household's quota from the same network the user was on -
confirmed via `GET https://api.github.com/rate_limit` (0/60 remaining,
~10 min to reset) and reproduced live with `update.check_for_update()`.
- `update.py`: new `_friendly_error(exc)` recognises a 403 specifically
  carrying `X-RateLimit-Remaining: 0` (vs. some other 403, e.g. an actual
  permissions problem) and replaces the raw exception text with a plain-
  language explanation plus the exact reset time read from
  `X-RateLimit-Reset`, ending "this clears on its own; no action needed."
  Other errors still fall back to `str(exc)` unchanged.
- 3 new tests in `test_update.py` build a real `urllib.error.HTTPError` with
  crafted headers rather than hitting the network, covering: recognised
  (remaining=0), a 403 that isn't the rate limit (quota still available),
  and a non-HTTP exception falling back to plain `str()`.
- Verified live against the actual rate-limited state at the time (not just
  the unit tests) - `check_for_update()` returned the new friendly message
  with the correct reset time.
- This is a design tradeoff inherent to the feature (no auth token shipped,
  see update.py's module docstring), not something to "fix" further by
  adding a token - 60/hour is plenty for normal use (one check per launch +
  occasional manual clicks); it only bites when something else on the same
  IP is also hammering the API unauthenticated, as happened here from my own
  testing. If this recurs without an obvious cause, that's the first thing
  to check, not a regression in the app.
- 149 tests total. Followed the standing build/release workflow again;
  updated [[bf6-build-workflow]] with the "poll authenticated" lesson from
  today's rate-limit false alarm during monitoring.

### 2026-09-07 (3) — 4 new unverified settings + grouped/searchable tables
User asked for "additional settings that could be modified" and "much better
UI". Clarified scope first (AskUserQuestion) rather than guessing: new
settings would be proposed-and-flagged-unverified (not fabricated as fact),
and the UI work would prioritise navigation/findability over visual polish.

- **4 new in-game settings**, `data/ingame_settings.json` v2026.09.07.1:
  `sharpening` (slider), `view_distance` (enum, Video > Advanced — argued in
  its own `note` why esports/competitive keep it *high*, unlike every other
  quality slider, since late mesh/LOD pop-in cuts both ways in a shooter),
  `reflection_quality` (enum), `weapon_fov` (slider, `personal: true`, Video >
  Basic). All four carry the new `"confidence": "unverified"` field and
  **no `profsave_key`** — the safety net is structural, not a promise: with no
  key, `writer.profsave_plan`'s existing "never invent PROFSAVE keys" rule
  means they can *never* reach a real profile write no matter what override is
  set, and `compare.py`'s existing "no profsave_key → unknown bucket" logic
  already classifies them correctly with zero code changes. Verified both
  invariants by hand (see test names below) before considering this safe.
  Added `unverified_settings_note` documenting the convention at the top of
  the JSON file, mirroring `cfg_commands.json`'s `confidence_levels` glossary.
  No `engine.py` changes were needed at all — the generic preset/override/
  cost-curve machinery already handles a plain enum/slider setting with no
  special-casing required.
- 14 new tests in `test_engine.py` (146 total): each of the 4 settings is
  recommended+flagged+overridable, `test_..._never_reach_a_profsave_write`
  proves the structural safety net with a deliberately-planted fake key,
  `test_raising_view_distance_costs_frames` checks the cost curve moves the
  FPS estimate in the right direction, and one checks `compare.py` buckets
  all four as `unknown`.
- **UI: grouped, collapsible, searchable tables** for both the in-game
  settings tab and the User.cfg tab (previously a single flat scroll of 39 /
  17 rows each). See §4 above for the row-kind mechanism; `_build_table_nav_row`
  is the one genuinely shared widget-construction helper between the two tabs
  (the row-sync/detail-pane methods stay parallel-but-separate, matching this
  file's existing per-tab-duplication style rather than a forced abstraction).
  Unverified settings get an "(unverified)" label suffix, muted row colour,
  and a warning-coloured badge + explanation in the detail pane.
- Verified everything by constructing the real `MainWindow` off-screen
  (`QT_QPA_PLATFORM=offscreen`) and driving it directly: row/group counts,
  jump-combo contents, search filtering (hides non-matching groups
  entirely), collapse/expand-all, header-row click-to-toggle, and
  `_reveal_settings_row` un-collapsing + clearing the search box + scrolling
  + selecting the right row when the linked `GameTime.MaxVariableFps` cfg row
  jumps to "Frame limit". All matched expectations exactly.
- Not done / flagged for later: only 4 settings added this round (kept
  deliberately small given the "don't fabricate" constraint) — if the user
  supplies a real BF6 profile dump or menu reference, promote these out of
  `unverified` (add the real `profsave_key`, drop the `confidence` field) and
  extend with real data rather than more guesses. UI-wise, "visual polish"
  (icons, spacing, typography) was explicitly deprioritised in favour of
  navigation this round and is still open if wanted later.
- Followed the now-standing build/release rule ([[bf6-build-workflow]]
  memory): tests → commit → push straight to `main` → poll Actions → pull the
  artifact into `dist/` via the cached Git Credential Manager token.

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
