"""The per-setting cost model.

Shared by the recommendation engine and the comparison view so a value's cost is
computed one way everywhere. Costs are expressed as a percentage of total frame
time relative to each setting's baseline option, which is what makes it valid to
add them up across a whole configuration.
"""

from __future__ import annotations

from typing import Any

# Effective pixel cost of each upscaler mode, including reconstruction overhead.
UPSCALE_COST = {
    "off": 1.00,
    "dlaa": 1.05,
    "quality": 0.58,
    "balanced": 0.48,
    "performance": 0.40,
    "ultra_performance": 0.28,
}

# Upscaling changes the pixel count, not the whole frame. Scale its modelled
# effect down so a Quality-mode switch does not claim the entire frame time.
UPSCALE_FRAME_SHARE = 0.90

# Frame time is modelled in units where 100 = the reference configuration (every
# setting at its baseline option). A config's frame time is 100 plus the sum of
# its options' costs, which composes correctly for any number of simultaneous
# changes - unlike summing pairwise deltas, which double-counts and saturates
# once several expensive settings move at once.
FRAME_TIME_BASE = 100.0

# These settings cannot plausibly account for more than this share of a frame,
# so the summed cost is floored to keep the arithmetic physical.
MIN_TOTAL_COST = -85.0
MAX_TOTAL_COST = 250.0

AXES = ("gpu", "cpu", "vram")


def frame_time(total_cost: float) -> float:
    return FRAME_TIME_BASE + min(MAX_TOTAL_COST, max(MIN_TOTAL_COST, total_cost))


def zero() -> dict[str, float]:
    return {axis: 0.0 for axis in AXES}


def option_cost(setting: dict[str, Any], value: Any) -> dict[str, float]:
    """Frame-time and VRAM cost of one option, relative to that setting's baseline."""
    cost = setting.get("cost")
    if not cost or value is None:
        return zero()

    if cost.get("interpolate"):
        try:
            point = float(value)
        except (TypeError, ValueError):
            return zero()
        anchors = sorted((float(k), v) for k, v in cost.items() if k != "interpolate")
        if not anchors:
            return zero()
        if point <= anchors[0][0]:
            entry = anchors[0][1]
        elif point >= anchors[-1][0]:
            entry = anchors[-1][1]
        else:
            entry = None
            for (low, low_v), (high, high_v) in zip(anchors, anchors[1:]):
                if low <= point <= high:
                    span = high - low
                    ratio = 0.0 if span == 0 else (point - low) / span
                    entry = {
                        axis: low_v.get(axis, 0.0)
                        + (high_v.get(axis, 0.0) - low_v.get(axis, 0.0)) * ratio
                        for axis in AXES
                    }
                    break
            if entry is None:
                return zero()
    else:
        entry = cost.get(str(value))
        if entry is None:
            return zero()

    return {axis: float(entry.get(axis, 0.0)) for axis in AXES}


def upscaler_cost(value: Any) -> dict[str, float]:
    multiplier = UPSCALE_COST.get(str(value))
    if multiplier is None:
        return zero()
    return {"gpu": (multiplier - 1.0) * 100.0 * UPSCALE_FRAME_SHARE, "cpu": 0.0, "vram": 0.0}


def cost_for(setting: dict[str, Any], value: Any) -> dict[str, float]:
    if setting["id"] == "upscaler":
        return upscaler_cost(value)
    return option_cost(setting, value)
