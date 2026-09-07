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
- **`icon.py`** — the app's reticle icon, drawn pixel-by-pixel in pure Python
  (no image library). Shared by `packaging/make_icon.py` (writes the .ico
  baked into the .exe) and `ui/app.py`'s `app_icon()` (renders the same
  artwork at runtime for the window/taskbar icon, so a source checkout looks
  the same as the shipped build).
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
