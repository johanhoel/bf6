"""Comparing the machine's current configuration against the recommendation.

Answers three questions for every single change, rather than handing over a
finished config and asking for trust:

* what is it now, and what would it become
* what does that cost or buy, in frames, VRAM, latency and visibility
* what are the arguments against it

The frame rate arithmetic works backwards from the recommendation. The engine
already estimated GPU-limited and CPU-limited frame rates for the *recommended*
settings; each setting carries a cost curve in percent of frame time, so summing
the deltas between current and recommended reconstructs what the current settings
are worth. That keeps one model behind both numbers instead of two that can
disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .database import Database
from .costs import cost_for as _cost_for, frame_time as _frame_time, zero as _zero
from .engine import PRESETS, Recommendation, SettingChoice, Target, recommend
from .hardware import HardwareProfile
from .writer import parse_profsave, render_user_cfg

# Quality order for settings whose values are not numeric.
UPSCALER_ORDER = ["ultra_performance", "performance", "balanced", "quality", "dlaa", "off"]


@dataclass
class SettingChange:
    setting_id: str
    label: str
    menu: str
    status: str           # "change" | "same" | "unknown" | "personal"
    current_value: Any = None
    current_display: str = "unknown"
    new_value: Any = None
    new_display: str = ""
    direction: str = "same"   # "raise" | "lower" | "same"
    gpu_delta_pct: float = 0.0
    cpu_delta_pct: float = 0.0
    vram_delta_gb: float = 0.0
    fps_delta: int = 0
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    reason: str = ""
    note: str = ""

    @property
    def impact_summary(self) -> str:
        parts: list[str] = []
        if self.fps_delta:
            parts.append(f"{self.fps_delta:+d} FPS")
        elif self.gpu_delta_pct or self.cpu_delta_pct:
            parts.append("no FPS change here")
        if abs(self.vram_delta_gb) >= 0.15:
            parts.append(f"{self.vram_delta_gb:+.1f} GB VRAM")
        if self.gpu_delta_pct:
            parts.append(f"GPU {self.gpu_delta_pct:+.1f}% frame time")
        if self.cpu_delta_pct:
            parts.append(f"CPU {self.cpu_delta_pct:+.1f}%")
        return ", ".join(parts) if parts else "no measurable frame cost"


@dataclass
class CfgChange:
    key: str
    action: str           # "add" | "change" | "remove" | "same"
    current: str | None
    new: str | None
    summary: str = ""
    detail: str = ""
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    risk: str = ""
    confidence: str = ""


@dataclass
class Comparison:
    available: bool
    reason_unavailable: str = ""
    profsave_path: Path | None = None
    user_cfg_path: Path | None = None
    changes: list[SettingChange] = field(default_factory=list)
    unchanged: list[SettingChange] = field(default_factory=list)
    unknown: list[SettingChange] = field(default_factory=list)
    personal: list[SettingChange] = field(default_factory=list)
    cfg_changes: list[CfgChange] = field(default_factory=list)
    current_gpu_fps: int = 0
    current_cpu_fps: int = 0
    current_predicted: int = 0
    new_predicted: int = 0
    fps_delta: int = 0
    vram_delta_gb: float = 0.0
    current_bottleneck: str = ""

    @property
    def cfg_additions(self) -> list[CfgChange]:
        return [c for c in self.cfg_changes if c.action == "add"]

    @property
    def cfg_removals(self) -> list[CfgChange]:
        return [c for c in self.cfg_changes if c.action == "remove"]

    @property
    def headline(self) -> str:
        if not self.available:
            return "Current configuration unavailable"
        count = len(self.changes)
        if not count and not [c for c in self.cfg_changes if c.action != "same"]:
            return "Your configuration already matches the recommendation."
        bits = [f"{count} in-game setting{'s' if count != 1 else ''} would change"]
        cfg_count = len([c for c in self.cfg_changes if c.action != "same"])
        if cfg_count:
            bits.append(f"{cfg_count} User.cfg line{'s' if cfg_count != 1 else ''}")
        delta = (
            f"{self.fps_delta:+d} FPS (about {self.current_predicted} -> {self.new_predicted})"
            if self.fps_delta else "no net frame rate change"
        )
        bits.append(delta)
        if abs(self.vram_delta_gb) >= 0.15:
            bits.append(f"{self.vram_delta_gb:+.1f} GB VRAM")
        return " | ".join(bits)


# --------------------------------------------------------------------------
# Cost curve evaluation
# --------------------------------------------------------------------------

def _ordinal(setting: dict[str, Any], value: Any) -> float | None:
    """A quality ranking for the value, so 'raise' and 'lower' can be decided."""
    if value is None:
        return None
    if setting["id"] == "upscaler":
        text = str(value)
        return float(UPSCALER_ORDER.index(text)) if text in UPSCALER_ORDER else None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Reading the current configuration
# --------------------------------------------------------------------------

def _coerce(setting: dict[str, Any], raw: str, scale: float) -> Any:
    """Turn a PROFSAVE string into the same units the app uses."""
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return raw.strip()
    if scale and scale != 1.0:
        number = number / scale
    if setting.get("type") in ("enum", "bool"):
        return int(round(number))
    if setting.get("type") == "slider":
        return int(round(number)) if abs(number - round(number)) < 0.01 else round(number, 2)
    return number


def read_current_settings(
    db: Database, profsave_text: str, choices: list[SettingChoice]
) -> dict[str, Any]:
    """Map the raw PROFSAVE contents onto setting ids, in the app's own units."""
    raw = parse_profsave(profsave_text)
    scales = {c.setting_id: (c.profsave_key, c.profsave_scale) for c in choices}
    current: dict[str, Any] = {}
    for setting in db.settings:
        key, scale = scales.get(setting["id"], (setting.get("profsave_key"), 1.0))
        if key and key in raw:
            current[setting["id"]] = _coerce(setting, raw[key], scale)
    return current


def read_current_cfg(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "//")):
            continue
        parts = stripped.split(None, 1)
        if len(parts) == 2:
            values[parts[0]] = parts[1].strip()
    return values


# --------------------------------------------------------------------------
# Building the comparison
# --------------------------------------------------------------------------

def _display(db: Database, setting_id: str, value: Any) -> str:
    from .engine import _label_for

    setting = db.setting(setting_id)
    return _label_for(setting, value) if setting else str(value)


def _build_cfg_changes(db: Database, rec: Recommendation, current_cfg: dict[str, str] | None) -> list[CfgChange]:
    proposed = {str(line.key): str(line.value) for line in rec.cfg if line.key}
    existing = current_cfg if current_cfg is not None else {}
    changes: list[CfgChange] = []

    for key, new_value in proposed.items():
        entry = db.command(key) or {}
        old = existing.get(key)
        if current_cfg is None:
            action = "add"
        elif old is None:
            action = "add"
        elif old.strip() == new_value:
            action = "same"
        else:
            action = "change"
        changes.append(CfgChange(
            key=key, action=action, current=old, new=new_value,
            summary=entry.get("summary", ""), detail=entry.get("detail", ""),
            pros=list(entry.get("pros", [])), cons=list(entry.get("cons", [])),
            risk=entry.get("risk", ""), confidence=entry.get("confidence", ""),
        ))

    # The app rewrites User.cfg wholesale, so anything already in the file that
    # is not in the new output disappears. Say so rather than surprising anyone.
    for key, old in existing.items():
        if key in proposed:
            continue
        entry = db.command(key) or {}
        changes.append(CfgChange(
            key=key, action="remove", current=old, new=None,
            summary=entry.get("summary", "Not a command this app manages."),
            detail=entry.get("detail", "This line is in your current User.cfg but not in the "
                                       "generated one, so saving would remove it. The backup keeps it."),
            pros=[], cons=[], risk=entry.get("risk", ""), confidence=entry.get("confidence", ""),
        ))

    order = {"change": 0, "add": 1, "remove": 2, "same": 3}
    changes.sort(key=lambda c: (order[c.action], c.key))
    return changes


def build(
    db: Database,
    rec: Recommendation,
    profsave_text: str | None,
    user_cfg_text: str | None,
    profsave_path: Path | None = None,
    user_cfg_path: Path | None = None,
) -> Comparison:
    """Compare the current configuration against ``rec``."""
    current_cfg = read_current_cfg(user_cfg_text) if user_cfg_text is not None else None
    cfg_changes = _build_cfg_changes(db, rec, current_cfg)

    if profsave_text is None:
        return Comparison(
            available=False,
            reason_unavailable=(
                "PROFSAVE_profile was not found, so there is nothing to compare the in-game "
                "settings against. Launch Battlefield 6 once, open the video settings and save "
                "them, then re-detect. The User.cfg comparison below works regardless."
            ),
            profsave_path=profsave_path, user_cfg_path=user_cfg_path,
            cfg_changes=cfg_changes,
            new_predicted=rec.predicted_fps,
        )

    current_values = read_current_settings(db, profsave_text, rec.settings)

    changes: list[SettingChange] = []
    unchanged: list[SettingChange] = []
    unknown: list[SettingChange] = []
    personal: list[SettingChange] = []

    cost_current = {"gpu": 0.0, "cpu": 0.0}
    cost_new = {"gpu": 0.0, "cpu": 0.0}
    total_vram = 0.0

    for choice in rec.settings:
        setting = db.setting(choice.setting_id)
        if setting is None:
            continue
        record = SettingChange(
            setting_id=choice.setting_id, label=choice.label, menu=choice.menu,
            status="same", new_value=choice.value, new_display=choice.display,
            reason=choice.reason, note=setting.get("note", ""),
        )

        if choice.value == "keep" or setting.get("never_write"):
            record.status = "personal"
            record.current_value = current_values.get(choice.setting_id)
            record.current_display = (
                _display(db, choice.setting_id, record.current_value)
                if record.current_value is not None else "unknown"
            )
            record.new_display = "left alone"
            personal.append(record)
            continue

        if choice.setting_id not in current_values:
            record.status = "unknown"
            record.current_display = "not stored in the profile"
            unknown.append(record)
            continue

        current = current_values[choice.setting_id]
        record.current_value = current
        record.current_display = _display(db, choice.setting_id, current)

        current_cost = _cost_for(setting, current)
        new_cost = _cost_for(setting, choice.value)
        record.gpu_delta_pct = round(new_cost["gpu"] - current_cost["gpu"], 2)
        record.cpu_delta_pct = round(new_cost["cpu"] - current_cost["cpu"], 2)
        record.vram_delta_gb = round(new_cost["vram"] - current_cost["vram"], 2)

        # Absolute costs of both configurations, accumulated for every setting
        # whether or not it changes, so the totals describe whole configs.
        cost_current["gpu"] += current_cost["gpu"]
        cost_current["cpu"] += current_cost["cpu"]
        cost_new["gpu"] += new_cost["gpu"]
        cost_new["cpu"] += new_cost["cpu"]

        same = current == choice.value or (
            isinstance(current, (int, float)) and isinstance(choice.value, (int, float))
            and abs(float(current) - float(choice.value)) < 0.01
        )
        if same:
            record.status = "same"
            unchanged.append(record)
            continue

        record.status = "change"
        current_rank = _ordinal(setting, current)
        new_rank = _ordinal(setting, choice.value)
        if current_rank is None or new_rank is None or current_rank == new_rank:
            record.direction = "raise" if record.gpu_delta_pct > 0 else "lower"
        else:
            record.direction = "raise" if new_rank > current_rank else "lower"

        tradeoff = setting.get("tradeoff", {}).get(record.direction, {})
        record.pros = list(tradeoff.get("pros", []))
        record.cons = list(tradeoff.get("cons", []))

        total_vram += record.vram_delta_gb
        changes.append(record)

    # Reconstruct the current frame rate from the recommended one. The engine
    # already sized the recommended settings against real hardware, so scaling by
    # the frame time ratio keeps one model behind both numbers.
    gpu_now, gpu_then = _frame_time(cost_current["gpu"]), _frame_time(cost_new["gpu"])
    cpu_now, cpu_then = _frame_time(cost_current["cpu"]), _frame_time(cost_new["cpu"])
    current_gpu_fps = rec.gpu_fps * gpu_then / gpu_now
    current_cpu_fps = rec.cpu_fps * cpu_then / cpu_now
    current_predicted = min(current_gpu_fps, current_cpu_fps)

    for record in changes:
        gpu_after = current_gpu_fps * gpu_now / _frame_time(cost_current["gpu"] + record.gpu_delta_pct)
        cpu_after = current_cpu_fps * cpu_now / _frame_time(cost_current["cpu"] + record.cpu_delta_pct)
        record.fps_delta = int(round(min(gpu_after, cpu_after) - current_predicted))

    changes.sort(key=lambda c: (-abs(c.fps_delta), -abs(c.vram_delta_gb), c.label))

    return Comparison(
        available=True,
        profsave_path=profsave_path, user_cfg_path=user_cfg_path,
        changes=changes, unchanged=unchanged, unknown=unknown, personal=personal,
        cfg_changes=cfg_changes,
        current_gpu_fps=int(round(current_gpu_fps)),
        current_cpu_fps=int(round(current_cpu_fps)),
        current_predicted=int(round(current_predicted)),
        new_predicted=rec.predicted_fps,
        fps_delta=int(round(rec.predicted_fps - current_predicted)),
        vram_delta_gb=round(total_vram, 2),
        current_bottleneck="CPU" if current_cpu_fps < current_gpu_fps * 0.97 else (
            "GPU" if current_gpu_fps < current_cpu_fps * 0.97 else "balanced"
        ),
    )


def from_paths(
    db: Database, rec: Recommendation, profsave: Path | None, user_cfg: Path | None
) -> Comparison:
    """Convenience wrapper that reads both files off disk if they exist."""
    def read(path: Path | None) -> str | None:
        if path and path.is_file():
            try:
                return path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return None
        return None

    return build(db, rec, read(profsave), read(user_cfg), profsave, user_cfg)


def closest_preset(
    db: Database, profile: HardwareProfile, base_target: Target,
    profsave: Path | None, user_cfg: Path | None,
    overrides: dict[str, Any] | None = None, cfg_overrides: dict[str, Any] | None = None,
) -> str | None:
    """Which preset needs the fewest changes against what is actually saved
    right now - a best-effort guess for a genuine first launch (no persisted
    target yet, see ``prefs.py``), so the app can start from something closer
    to reality than a hardcoded default. `base_target` supplies everything
    except the preset (resolution, refresh rate, VRR/HDR/etc.) - only the
    preset field varies across candidates.

    Returns ``None`` if there is nothing to compare against yet (no
    PROFSAVE_profile found) rather than guessing blind.
    """
    from dataclasses import replace

    best_preset: str | None = None
    best_changes: int | None = None
    for name in PRESETS:
        try:
            candidate = recommend(
                db, profile, replace(base_target, preset=name),
                overrides=overrides, cfg_overrides=cfg_overrides,
            )
            comparison = from_paths(db, candidate, profsave, user_cfg)
        except Exception:
            continue
        if not comparison.available:
            return None
        changes = len(comparison.changes)
        if best_changes is None or changes < best_changes:
            best_changes, best_preset = changes, name
    return best_preset
