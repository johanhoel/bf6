"""The recommendation engine.

Takes a detected machine plus a target (preset, resolution, refresh rate) and
produces three things: the in-game settings to use, the User.cfg to write, and
the warnings that explain why some popular tweaks were deliberately left out.

Two design decisions are worth stating up front, because they are where this
differs from a copy-pasted config off a forum:

1. Frame rate is *predicted* before settings are chosen. The engine estimates a
   GPU-limited and a CPU-limited frame rate separately, then only spends image
   quality where the prediction says it is affordable. It also names the
   bottleneck, because "turn everything down" is the wrong advice on a machine
   that is CPU-limited.

2. The ``Thread.*`` overrides that dominate every Battlefield User.cfg guide are
   gated on CPU topology. On a hybrid Intel part they park the E-cores and cost
   roughly a fifth of the 1% lows; on a single-CCD AMD part they do very little.
   The engine emits them only where they can plausibly help, and says so either
   way rather than leaving the user to wonder.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from .database import Database
from .hardware import HardwareProfile

PRESETS = ("esports", "competitive", "balanced", "quality")

# Frame rate model constants. Anchored so that a GPU with score 100 renders
# 165 FPS at 2560x1440 on the 'balanced' preset with no upscaling.
_FPS_ANCHOR = 1.65
_REFERENCE_PIXELS = 2560 * 1440
_RES_EXPONENT = 0.92

_PRESET_GPU_FACTOR = {"esports": 1.62, "competitive": 1.34, "balanced": 1.00, "quality": 0.80}
_PRESET_CPU_FACTOR = {"esports": 1.10, "competitive": 1.05, "balanced": 1.00, "quality": 0.97}

# Effective pixel cost of each upscaler mode, including reconstruction overhead.
UPSCALE_COST = {
    "off": 1.00,
    "dlaa": 1.05,
    "quality": 0.58,
    "balanced": 0.48,
    "performance": 0.40,
    "ultra_performance": 0.28,
}
UPSCALE_LADDER = ["off", "quality", "balanced", "performance"]

# How much image quality each lever costs, in comparable units. Upscaling at
# Quality is treated as roughly one preset step, because at 1440p and above a
# reconstructed image at 67% render scale generally reads better than dropping
# shadows and effects a notch. Below Quality the cost climbs steeply, since that
# is where distant players start smearing during strafing.
_UPSCALE_DEGRADATION = {"off": 0.0, "dlaa": 0.1, "quality": 1.0, "balanced": 2.2,
                        "performance": 3.6, "ultra_performance": 6.0}
_STEP_DEGRADATION = 1.0

# How far below the preset's own values the engine may push each setting when
# the target frame rate is out of reach. Index 0 is the preset itself.
QUALITY_STEPS = [0, -1, -2, -3]


@dataclass
class Target:
    preset: str = "balanced"
    width: int = 2560
    height: int = 1440
    refresh_hz: int = 165
    vrr: bool = True
    background_load: bool = False
    hdr_display: bool = False
    allow_frame_generation: bool = False
    allow_thread_overrides: bool = False
    include_legacy_commands: bool = False
    show_fps_overlay: bool = True


@dataclass
class SettingChoice:
    setting_id: str
    label: str
    menu: str
    value: Any
    display: str
    reason: str
    profsave_key: str | None = None
    personal: bool = False
    # Some values are stored in the profile in different units to the ones shown
    # (motion blur and brightness are 0.0-1.0 on disk, percentages in the UI).
    profsave_scale: float = 1.0


@dataclass
class CfgLine:
    key: str | None
    value: Any
    comment: str = ""
    confidence: str = ""
    risk: str = ""
    header: bool = False


@dataclass
class Warning_:
    severity: str  # high | medium | low | info
    title: str
    body: str


@dataclass
class Recommendation:
    profile: HardwareProfile
    target: Target
    gpu: dict[str, Any]
    cpu: dict[str, Any]
    gpu_fps: int
    cpu_fps: int
    predicted_fps: int
    bottleneck: str
    frame_cap: int
    upscaler_mode: str
    upscaler_tech: str
    quality_step: int
    settings: list[SettingChoice] = field(default_factory=list)
    cfg: list[CfgLine] = field(default_factory=list)
    warnings: list[Warning_] = field(default_factory=list)
    tweaks: list[dict[str, Any]] = field(default_factory=list)
    headroom_note: str = ""


# --------------------------------------------------------------------------
# Hardware -> database matching
# --------------------------------------------------------------------------

def _normalise(name: str, aliases: dict[str, str]) -> str:
    text = " " + name.lower() + " "
    for needle, replacement in sorted(aliases.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(needle, replacement)
    text = re.sub(r"\b(\d+)\s*gb\b", r"\1gb", text)
    return re.sub(r"\s+", " ", text).strip()


def match_gpu(profile: HardwareProfile, db: Database) -> dict[str, Any]:
    """Longest-id-first substring match, with a VRAM tiebreak for split SKUs."""
    aliases = db.gpu_db.get("aliases", {})
    needle = _normalise(profile.gpu_name, aliases)
    matches = [g for g in db.gpus if g["id"] in needle]
    if matches:
        best = max(matches, key=lambda g: len(g["id"]))
        # RTX 4060 Ti and RX 9060 XT ship in 8GB and 16GB flavours under the same
        # marketing name, so the adapter string alone cannot tell them apart.
        # Look at every database entry the matched name is a prefix of and pick
        # by detected VRAM instead.
        if profile.vram_gb >= 1:
            family = [g for g in db.gpus if g["id"].startswith(best["id"])]
            if len(family) > 1:
                best = min(family, key=lambda g: abs(g.get("vram", 0) - profile.vram_gb))
        entry = dict(best)
        if profile.vram_gb >= 1:
            entry["vram"] = profile.vram_gb
        entry["matched"] = True
        return entry

    return {
        "id": "unknown",
        "name": profile.gpu_name,
        "vendor": profile.gpu_vendor,
        "arch": "unknown",
        "vram": profile.vram_gb or 8.0,
        # Unknown card: assume something around an RTX 3060 rather than guessing
        # high, so the recommendation errs towards playable.
        "score": 40.0,
        "dlss": 4 if profile.gpu_vendor == "nvidia" else 0,
        "fsr": 3,
        "xess": 2 if profile.gpu_vendor == "intel" else 1,
        "frame_gen": "fsr3",
        "reflex": profile.gpu_vendor == "nvidia",
        "rt": 1,
        "matched": False,
    }


def _cpu_heuristic(profile: HardwareProfile, db: Database) -> float:
    h = db.cpu_db.get("heuristic", {})
    base = h.get("base", 30)
    per_thread = h.get("per_effective_thread", 4.6)
    cap = h.get("effective_thread_cap", 20)
    per_ghz = h.get("per_ghz", 14.0)
    effective_threads = min(profile.threads or profile.cores or 4, cap)
    clock = profile.max_clock_ghz or 3.6
    score = base + effective_threads * per_thread + clock * per_ghz
    return score * h.get("gen_multiplier", {}).get("unknown", 0.85)


def match_cpu(profile: HardwareProfile, db: Database) -> dict[str, Any]:
    needle = re.sub(r"\s+", " ", profile.cpu_name.lower())
    needle = needle.replace("(r)", "").replace("(tm)", "").replace("processor", "").strip()
    matches = [c for c in db.cpus if c["id"] in needle]
    if matches:
        entry = dict(max(matches, key=lambda c: len(c["id"])))
        entry["matched"] = True
        if profile.hybrid:
            entry["hybrid"] = True
        return entry

    return {
        "id": "unknown",
        "vendor": profile.cpu_vendor,
        "cores": profile.cores,
        "threads": profile.threads,
        "score": round(_cpu_heuristic(profile, db), 1),
        "hybrid": profile.hybrid,
        "x3d": "x3d" in needle,
        "ccds": 2 if profile.cores >= 12 and profile.cpu_vendor == "amd" else 1,
        "gen": "unknown",
        "matched": False,
    }


# --------------------------------------------------------------------------
# Frame rate model
# --------------------------------------------------------------------------

def _resolution_cost(width: int, height: int) -> float:
    return ((width * height) / _REFERENCE_PIXELS) ** _RES_EXPONENT


def _memory_penalty(profile: HardwareProfile) -> tuple[float, list[str]]:
    penalty, reasons = 1.0, []
    if profile.single_channel:
        penalty *= 0.78
        reasons.append("single-channel memory")
    if profile.ram_speed_mts:
        if profile.ram_speed_mts < 2800:
            penalty *= 0.86
            reasons.append(f"memory running at {profile.ram_speed_mts} MT/s (XMP/EXPO likely off)")
        elif profile.ram_speed_mts < 3200:
            penalty *= 0.94
            reasons.append(f"modest memory speed ({profile.ram_speed_mts} MT/s)")
    if profile.ram_gb and profile.ram_gb < 16:
        penalty *= 0.85
        reasons.append(f"only {profile.ram_gb:g} GB of system RAM")
    return penalty, reasons


def estimate_gpu_fps(gpu: dict[str, Any], target: Target, preset: str, upscaler: str, step: int) -> float:
    cost = _resolution_cost(target.width, target.height) * UPSCALE_COST.get(upscaler, 1.0)
    factor = _PRESET_GPU_FACTOR[preset]
    # Each quality step below the preset is worth roughly 9% GPU time.
    factor *= (1.0 + 0.09 * abs(step)) if step < 0 else 1.0
    return (gpu["score"] * _FPS_ANCHOR * factor) / max(cost, 0.05)


def estimate_cpu_fps(cpu: dict[str, Any], profile: HardwareProfile, preset: str) -> float:
    penalty, _ = _memory_penalty(profile)
    return cpu["score"] * _PRESET_CPU_FACTOR[preset] * penalty


# --------------------------------------------------------------------------
# Setting selection
# --------------------------------------------------------------------------

def _upscaler_tech(gpu: dict[str, Any]) -> str:
    if gpu.get("dlss", 0) >= 2:
        return "DLSS Super Resolution"
    if gpu.get("xess", 0) >= 2:
        return "Intel XeSS"
    return "AMD FSR"


def _vram_cap_for_textures(setting: dict[str, Any], vram_gb: float, target: Target) -> int:
    """Highest texture level the card's VRAM can hold at this resolution."""
    budget = vram_gb
    if budget <= 0:
        return 2
    # Higher resolutions need more VRAM for render targets before textures.
    pixels = target.width * target.height
    budget -= 1.0 + 2.0 * (pixels / _REFERENCE_PIXELS)
    allowed = 0
    for option in setting["options"]:
        if budget >= option.get("vram_gb", 0) - 4.0:
            allowed = option["value"]
    return allowed


def _target_fps(target: Target, preset: str) -> int:
    refresh = max(target.refresh_hz, 60)
    if preset == "esports":
        return refresh
    if preset == "competitive":
        return refresh
    if preset == "balanced":
        return min(refresh, 144)
    return min(refresh, 90)


def _choose_operating_point(
    gpu: dict[str, Any], cpu: dict[str, Any], profile: HardwareProfile, target: Target
) -> tuple[str, int, float, float]:
    """Pick the least-destructive (upscaler, quality step) that reaches the target."""
    preset = target.preset
    want = _target_fps(target, preset)
    cpu_fps = estimate_cpu_fps(cpu, profile, preset)

    # Never chase a target the CPU cannot deliver - turning textures down does
    # not help a CPU bottleneck, it just makes the game uglier for nothing.
    want = min(want, cpu_fps * 1.02)

    # 1080p is already a low pixel count; upscaling below Quality there costs
    # more clarity than it is worth in a shooter.
    ladder = UPSCALE_LADDER if target.width > 1920 else UPSCALE_LADDER[:2]
    if preset == "quality" and gpu.get("dlss", 0) >= 2:
        ladder = ["off", "dlaa"] + ladder[1:]

    # Evaluate every combination rather than stopping at the first one that
    # clears the target: the first hit is an artefact of iteration order, and it
    # produces results that are not monotonic across presets.
    reachable: list[tuple[float, float, str, int]] = []
    fallback: tuple[float, str, int] | None = None
    for step in QUALITY_STEPS:
        for upscaler in ladder:
            fps = estimate_gpu_fps(gpu, target, preset, upscaler, step)
            if fps >= want:
                cost = _UPSCALE_DEGRADATION[upscaler] + abs(step) * _STEP_DEGRADATION
                reachable.append((cost, -fps, upscaler, step))
            if fallback is None or fps > fallback[0]:
                fallback = (fps, upscaler, step)

    if reachable:
        _, negative_fps, upscaler, step = min(reachable)
        return (upscaler, step, -negative_fps, cpu_fps)

    fps, upscaler, step = fallback  # type: ignore[misc]
    return (upscaler, step, fps, cpu_fps)


def _clamp_option(setting: dict[str, Any], value: int) -> int:
    values = [o["value"] for o in setting.get("options", []) if isinstance(o["value"], int)]
    if not values:
        return value
    return max(min(values), min(max(values), value))


def _label_for(setting: dict[str, Any], value: Any) -> str:
    for option in setting.get("options", []):
        if option["value"] == value:
            return str(option["label"])
    if setting.get("type") == "bool" and isinstance(value, int):
        return "On" if value else "Off"
    unit = setting.get("unit", "")
    return f"{value}{(' ' + unit) if unit else ''}"


def _compute_frame_cap(target: Target, predicted: float) -> int:
    refresh = max(target.refresh_hz, 60)
    if target.vrr:
        # Stay inside the variable-refresh window; below it, VRR stops working
        # and you get the tearing or the latency you were trying to avoid.
        cap = refresh - 3
    else:
        cap = refresh
    # No point advertising a cap the machine cannot hold; round down to a value
    # that is actually sustainable, but never below 60.
    sustainable = int(predicted * 0.94)
    if sustainable < cap:
        cap = max(60, (sustainable // 5) * 5)
    return int(cap)


# --------------------------------------------------------------------------
# User.cfg construction
# --------------------------------------------------------------------------

def _thread_policy(cpu: dict[str, Any], profile: HardwareProfile, target: Target) -> tuple[bool, str]:
    """Should Thread.* overrides be written, and why (not)?"""
    threads = profile.threads or cpu.get("threads", 0)
    if profile.hybrid or cpu.get("hybrid"):
        return (False, (
            "Your CPU has both performance and efficiency cores"
            f" ({profile.p_cores}P + {profile.e_cores}E)."
            " Capping Thread.ProcessorCount on a hybrid part effectively parks the E-cores,"
            " and measured results put that at roughly a 15-20% loss in 1% low FPS - visible"
            " stutter, even when average FPS barely moves. The overrides have been left out"
            " on purpose. Windows' Thread Director already does this job better."
        ))
    cores = profile.cores or cpu.get("cores", 0)
    if cores < 8 or threads < 16:
        return (False, (
            f"Your CPU exposes {cores} cores / {threads} threads. Battlefield 6 can use all of"
            " them, and holding some back on a CPU this size costs frames with nothing to show"
            " for it. Thread overrides are only ever considered at 8 cores and 16 threads or above."
        ))
    if cpu.get("ccds", 1) > 1 and cpu.get("x3d"):
        return (False, (
            "Your CPU is a dual-chiplet X3D part. The real problem on these is threads landing on"
            " the chiplet without the 3D V-Cache, and a thread count cap does not control which"
            " chiplet gets used. Keep the AMD chipset driver and Xbox Game Bar installed, or pin"
            " the game to the cache-carrying chiplet with Process Lasso."
        ))
    if not target.allow_thread_overrides:
        return (False, (
            "Thread overrides are available for your CPU but are off by default. Independent"
            " testing of the popular 'lower CPU usage' config found average FPS roughly flat and"
            " 1% lows down 15%+ on several systems. Enable the advanced toggle if you want to"
            " measure it yourself - use the frame time graph, not the FPS counter."
        ))
    return (True, (
        "Your CPU is homogeneous with enough threads to hold some back, so the overrides are"
        " written. Verify with the frame time graph: if 1% lows drop, remove them."
    ))


def _build_cfg(
    db: Database, gpu: dict[str, Any], cpu: dict[str, Any], profile: HardwareProfile,
    target: Target, frame_cap: int, cpu_bound: bool,
) -> tuple[list[CfgLine], list[Warning_]]:
    lines: list[CfgLine] = []
    warnings: list[Warning_] = []
    legacy = target.include_legacy_commands

    def emit(key: str, value: Any, comment: str = "") -> None:
        entry = db.command(key)
        if entry is None:
            return
        if entry.get("hardware_policy") == "never":
            return
        if entry.get("confidence") == "legacy" and not legacy and key not in (
            "PerfOverlay.DrawGraph",
        ):
            return
        lines.append(
            CfgLine(
                key=key, value=value, comment=comment or entry["summary"],
                confidence=entry.get("confidence", ""), risk=entry.get("risk", ""),
            )
        )

    def header(text: str) -> None:
        lines.append(CfgLine(key=None, value=None, comment=text, header=True))

    header("Frame pacing")
    emit("GameTime.MaxVariableFps", frame_cap,
         f"Cap at {frame_cap} FPS"
         + (" - 3 below your refresh rate so you stay inside the VRR window."
            if target.vrr else " - a rate this machine can hold consistently."))
    emit("RenderDevice.VSyncEnable", 0,
         "In-game V-Sync off. With VRR, enable V-Sync in the driver instead.")
    emit("RenderDevice.TripleBufferingEnable", 0, "Triple buffering off - it only adds latency here.")
    if legacy:
        emit("RenderDevice.VerticalSyncEnable", 0, "Legacy spelling, harmless if unrecognised.")

    header("Render pipeline")
    ahead = 1 if target.preset in ("esports", "competitive", "balanced") else 2
    emit("RenderDevice.RenderAheadLimit", ahead,
         f"Queue at most {ahead} frame(s) ahead - lower input latency and less speculative CPU work.")
    if legacy:
        emit("RenderDevice.ForceRenderAheadLimit", ahead, "Legacy companion to the above.")
    if gpu.get("vendor") == "nvidia" and gpu.get("arch") not in ("pascal", "unknown"):
        emit("GstRender.Dx12NvApi", 1, "NVIDIA fast paths under DX12.")
    if cpu_bound and target.preset in ("esports", "competitive"):
        emit("GstRender.Dx12SubmitThreads", 1,
             "Serialised command list submission - smoother frame pacing when the CPU is the limit.")

    header("Post processing that costs frames and hides players")
    emit("PostProcess.DofMethod", 0, "Depth of field off.")
    emit("WorldRender.MotionBlurEnable", 0, "Motion blur off.")
    if legacy:
        emit("WorldRender.MotionBlurForceOn", 0)
        emit("WorldRender.MotionBlurMaxSampleCount", 0)
        emit("WorldRender.MotionBlurQuality", 0)
        emit("PostProcess.BlurMethod", 0)
        emit("WorldRender.LensFlaresEnable", 0)

    if legacy and gpu["score"] < 35:
        header("Low-end GPU relief (legacy Frostbite keys - may be ignored by BF6)")
        emit("WorldRender.PlanarReflectionEnable", 0)
        emit("WorldRender.TransparencyShadowmapsEnable", 0)
        emit("WorldRender.SpotLightShadowmapResolution", 512)

    header("CPU threading")
    apply_threads, rationale = _thread_policy(cpu, profile, target)
    threads = profile.threads or cpu.get("threads", 8)
    if apply_threads:
        # Leave two logical processors for the OS and the render thread's own
        # scheduling slack, rather than the "half your threads" folklore.
        worker = max(6, threads - 2)
        emit("Thread.ProcessorCount", worker, f"Job system sees {worker} of {threads} threads.")
        emit("Thread.MaxProcessorCount", worker, "Matching upper bound.")
        emit("GstRender.Thread.MaxProcessorCount", threads,
             "Render submission keeps the full thread count.")
        warnings.append(Warning_("medium", "Thread overrides are enabled", rationale))
    else:
        lines.append(CfgLine(key=None, value=None, comment="Thread.* overrides deliberately omitted."))
        warnings.append(Warning_("info", "No Thread.* overrides were written", rationale))

    free = 0
    if target.background_load:
        free = 2 if threads >= 24 else 1
    emit("Thread.MinFreeProcessorCount", free,
         f"Leave {free} logical processor(s) for Windows and background apps."
         if free else "Let the job system use every thread.")
    emit("Thread.JobThreadPriority", 0, "Normal priority - raising this usually backfires.")

    if target.show_fps_overlay:
        header("Telemetry - the only way to tell whether any of this worked")
        emit("PerfOverlay.DrawFps", 1, "On-screen FPS counter.")
        emit("PerfOverlay.DrawGraph", 1,
             "Frame time graph. Stutter is a frame time problem, not an average FPS problem.")

    return lines, warnings


# --------------------------------------------------------------------------
# System tweak evaluation
# --------------------------------------------------------------------------

def _evaluate_tweaks(
    db: Database, profile: HardwareProfile, gpu: dict[str, Any], cpu: dict[str, Any],
    install_drive_media: str | None,
) -> list[dict[str, Any]]:
    active: list[dict[str, Any]] = []
    for tweak in db.tweaks:
        trigger = tweak.get("trigger", {})
        fires = False
        if trigger.get("always"):
            fires = True
        if "ram_speed_below" in trigger and profile.ram_speed_mts:
            fires = fires or profile.ram_speed_mts < trigger["ram_speed_below"]
        if "vram_below" in trigger and profile.vram_gb:
            fires = fires or profile.vram_gb < trigger["vram_below"]
        if "ram_below_gb" in trigger and profile.ram_gb:
            fires = fires or profile.ram_gb < trigger["ram_below_gb"]
        if trigger.get("single_channel"):
            fires = fires or profile.single_channel
        if trigger.get("cpu_hybrid"):
            fires = fires or bool(profile.hybrid or cpu.get("hybrid"))
        if trigger.get("cpu_dual_ccd_x3d"):
            fires = fires or bool(cpu.get("x3d") and cpu.get("ccds", 1) > 1)
        if trigger.get("install_on_hdd"):
            fires = fires or (install_drive_media or "").upper() == "HDD"
        if "gpu_arch_in" in trigger:
            fires = fires or gpu.get("arch") in trigger["gpu_arch_in"]
        if not fires:
            continue

        rendered = dict(tweak)
        substitutions = {
            "{ram_speed}": str(profile.ram_speed_mts),
            "{ram_gb}": f"{profile.ram_gb:g}",
            "{vram}": f"{profile.vram_gb:g}",
            "{driver_version}": profile.driver_version or "unknown",
        }
        for key in ("why", "how"):
            text = rendered.get(key, "")
            for needle, replacement in substitutions.items():
                text = text.replace(needle, replacement)
            rendered[key] = text
        active.append(rendered)
    return active


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def recommend(
    db: Database, profile: HardwareProfile, target: Target,
    install_drive_media: str | None = None,
) -> Recommendation:
    if target.preset not in PRESETS:
        raise ValueError(f"Unknown preset {target.preset!r}; expected one of {PRESETS}")

    gpu = match_gpu(profile, db)
    cpu = match_cpu(profile, db)

    upscaler, step, gpu_fps, cpu_fps = _choose_operating_point(gpu, cpu, profile, target)
    predicted = min(gpu_fps, cpu_fps)
    bottleneck = "CPU" if cpu_fps < gpu_fps * 0.97 else ("GPU" if gpu_fps < cpu_fps * 0.97 else "balanced")
    frame_cap = _compute_frame_cap(target, predicted)

    settings: list[SettingChoice] = []
    warnings: list[Warning_] = []
    preset = target.preset

    for setting in db.settings:
        if setting.get("never_write") and setting.get("presets", {}).get(preset) == "keep":
            settings.append(SettingChoice(
                setting["id"], setting["label"], setting["menu"], "keep", "Leave as-is",
                setting["note"], setting.get("profsave_key"), personal=True,
                profsave_scale=float(setting.get("profsave_scale", 1.0)),
            ))
            continue

        raw = setting.get("presets", {}).get(preset)
        reason = setting["note"]
        value: Any = raw

        if setting.get("always") is not None:
            value = setting["always"]
        elif setting["id"] == "resolution":
            value = f"{target.width}x{target.height}"
            reason = setting["note"]
        elif setting["id"] == "frame_limit":
            value = frame_cap
            reason = (
                f"Capped at {frame_cap} FPS against a predicted {int(predicted)} FPS"
                f" and a {target.refresh_hz} Hz display."
                + (" Three below refresh keeps VRR engaged." if target.vrr else "")
            )
        elif setting["id"] == "upscaler":
            value = upscaler
            tech = _upscaler_tech(gpu)
            if upscaler == "off":
                reason = f"Native rendering - your GPU reaches the target without help. {tech} is available if you want extra headroom."
            elif upscaler == "dlaa":
                reason = f"{tech} in native mode: same pixel count, better anti-aliasing, spare GPU time put into image quality."
            else:
                reason = f"{tech} on {upscaler.replace('_', ' ')} - needed to reach the target frame rate at {target.width}x{target.height}."
        elif setting["id"] == "texture_quality":
            baseline = int(raw)
            cap = _vram_cap_for_textures(setting, gpu.get("vram", profile.vram_gb), target)
            value = min(baseline, cap)
            if value < baseline:
                reason = (
                    f"Capped at {_label_for(setting, value)} by {gpu.get('vram', 0):g} GB of VRAM at"
                    f" {target.width}x{target.height}. Going higher would spill into system memory,"
                    " which is felt as hitching rather than as a lower frame rate."
                )
                warnings.append(Warning_(
                    "medium", "Texture quality limited by VRAM",
                    f"{gpu.get('name', 'Your GPU')} has {gpu.get('vram', 0):g} GB. At"
                    f" {target.width}x{target.height} that is not enough for"
                    f" {_label_for(setting, baseline)} textures without risking VRAM spill.",
                ))
        elif setting["id"] == "ray_tracing":
            gate = setting.get("gate", {})
            capable = gpu.get("rt", 0) >= gate.get("min_rt", 3) and gpu.get("vram", 0) >= gate.get("min_vram", 12)
            value = 1 if (raw == "computed" and capable and preset == "quality") else 0
            if raw == "computed" and not capable:
                reason = f"Off - {gpu.get('name', 'your GPU')} does not have the ray tracing throughput or VRAM to hold a stable frame rate with it on."
        elif setting["id"] == "frame_generation":
            capable = gpu.get("frame_gen", "none") != "none"
            value = 1 if (raw == "computed" and capable and target.allow_frame_generation
                          and predicted >= 60 and preset in ("balanced", "quality")) else 0
            if value == 0 and raw == "computed":
                if not target.allow_frame_generation:
                    reason = "Off - frame generation is opt-in in this app because generated frames raise latency even as the counter goes up."
                elif predicted < 60:
                    reason = f"Off - frame generation needs a solid real frame rate underneath it, and you are predicted at {int(predicted)} FPS."
        elif setting["id"] == "low_latency":
            if not gpu.get("reflex") and gpu.get("vendor") != "amd":
                value = 0
                reason = "Not available on this GPU."
        elif setting["id"] == "hdr":
            value = 1 if (raw == "computed" and target.hdr_display and preset in ("balanced", "quality")) else 0
            if raw == "computed" and not target.hdr_display:
                reason = "Off - no HDR display confirmed. Tick the HDR box if your monitor genuinely does HDR well."
        elif isinstance(raw, int) and setting.get("options") and step < 0:
            floor_min = setting.get("floor", {}).get("competitive_min")
            value = _clamp_option(setting, raw + step)
            if floor_min is not None:
                value = max(value, floor_min)
            if value != raw:
                reason = (
                    f"Lowered from {_label_for(setting, raw)} to {_label_for(setting, value)} to reach"
                    f" the {_target_fps(target, preset)} FPS target. {setting['note']}"
                )

        settings.append(SettingChoice(
            setting_id=setting["id"], label=setting["label"], menu=setting["menu"],
            value=value, display=_label_for(setting, value), reason=reason,
            profsave_key=setting.get("profsave_key"), personal=bool(setting.get("personal")),
            profsave_scale=float(setting.get("profsave_scale", 1.0)),
        ))

    cfg_lines, cfg_warnings = _build_cfg(
        db, gpu, cpu, profile, target, frame_cap, cpu_bound=(bottleneck == "CPU")
    )
    warnings.extend(cfg_warnings)

    if not gpu.get("matched", False):
        warnings.append(Warning_(
            "medium", "GPU not in the database",
            f"'{profile.gpu_name}' was not recognised, so the frame rate estimate assumes something"
            " around an RTX 3060. The settings are still valid, but treat the predicted FPS as a"
            " rough figure and adjust from the in-game overlay.",
        ))
    if not cpu.get("matched", False):
        warnings.append(Warning_(
            "low", "CPU not in the database",
            f"'{profile.cpu_name}' was scored from its core count and clock speed rather than from"
            " a measured entry. Thread policy is still driven by real topology detection, so the"
            " important decision here is unaffected.",
        ))
    if bottleneck == "CPU":
        warnings.append(Warning_(
            "info", "This machine is CPU-limited",
            f"Predicted {int(cpu_fps)} FPS from the CPU against {int(gpu_fps)} FPS from the GPU."
            " Lowering graphics settings further will not raise your frame rate - it will just make"
            " the game look worse. The levers that matter here are memory speed (XMP/EXPO), Mesh and"
            " Effects quality, and closing background applications.",
        ))
    elif bottleneck == "GPU" and gpu_fps < _target_fps(target, preset) * 0.85:
        warnings.append(Warning_(
            "info", "GPU-limited below the target",
            f"Even at the most aggressive settings this preset allows, the estimate is {int(gpu_fps)} FPS"
            f" against a {_target_fps(target, preset)} FPS target. Either accept the lower cap or drop"
            " to a lighter preset.",
        ))

    penalty, penalty_reasons = _memory_penalty(profile)
    headroom = ""
    if penalty_reasons:
        lost = int(cpu_fps / penalty - cpu_fps)
        headroom = (
            f"Memory configuration is costing an estimated {lost} FPS at the CPU limit ("
            + ", ".join(penalty_reasons) + ")."
        )

    return Recommendation(
        profile=profile, target=target, gpu=gpu, cpu=cpu,
        gpu_fps=int(gpu_fps), cpu_fps=int(cpu_fps), predicted_fps=int(predicted),
        bottleneck=bottleneck, frame_cap=frame_cap,
        upscaler_mode=upscaler, upscaler_tech=_upscaler_tech(gpu), quality_step=step,
        settings=settings, cfg=cfg_lines, warnings=warnings,
        tweaks=_evaluate_tweaks(db, profile, gpu, cpu, install_drive_media),
        headroom_note=headroom,
    )
