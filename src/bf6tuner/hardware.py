"""Hardware detection.

Everything the recommendation engine needs about the machine, gathered in one
pass. On Windows this is a single PowerShell/CIM round trip plus two ctypes
calls; on anything else it returns a clearly-labelled sample profile so the
engine and the UI can be developed and tested off-platform.

The one piece worth explaining is P-core / E-core detection. It uses
``GetLogicalProcessorInformationEx``, which reports an *efficiency class* per
physical core (higher = faster core, lower = more efficient core). That is the
authoritative answer, and it matters here because the most widely-copied
Battlefield User.cfg tweak - capping ``Thread.ProcessorCount`` - is actively
harmful on a hybrid CPU. Guessing from the model name would get this wrong on
exactly the parts where being wrong costs the user 1% lows.
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import re
import subprocess
from dataclasses import dataclass, field, asdict
from typing import Any

IS_WINDOWS = os.name == "nt"

RELATION_PROCESSOR_CORE = 0
LTP_PC_SMT = 0x1


@dataclass
class MemoryStick:
    capacity_gb: float = 0.0
    speed_mts: int = 0
    slot: str = ""


@dataclass
class HardwareProfile:
    # CPU
    cpu_name: str = "Unknown CPU"
    cpu_vendor: str = "unknown"
    cores: int = 0
    threads: int = 0
    p_cores: int = 0
    e_cores: int = 0
    hybrid: bool = False
    max_clock_ghz: float = 0.0

    # GPU
    gpu_name: str = "Unknown GPU"
    gpu_vendor: str = "unknown"
    vram_gb: float = 0.0
    driver_version: str = ""
    all_gpus: list[str] = field(default_factory=list)

    # Memory
    ram_gb: float = 0.0
    ram_speed_mts: int = 0
    ram_sticks: list[MemoryStick] = field(default_factory=list)

    # Display
    width: int = 1920
    height: int = 1080
    refresh_hz: int = 60
    max_refresh_hz: int = 60
    hdr_display: bool = False

    # System
    os_name: str = ""
    os_build: str = ""
    drive_media: dict[str, str] = field(default_factory=dict)

    detected: bool = False
    detection_notes: list[str] = field(default_factory=list)

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"

    @property
    def single_channel(self) -> bool:
        populated = [s for s in self.ram_sticks if s.capacity_gb > 0]
        return len(populated) == 1 and self.ram_gb >= 8

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# Windows: topology via GetLogicalProcessorInformationEx
# --------------------------------------------------------------------------

def _core_topology() -> tuple[int, int, int, int, bool]:
    """Return (physical_cores, logical_threads, p_cores, e_cores, hybrid)."""
    if not IS_WINDOWS:
        return (0, 0, 0, 0, False)

    kernel32 = ctypes.windll.kernel32
    length = ctypes.c_ulong(0)
    kernel32.GetLogicalProcessorInformationEx(RELATION_PROCESSOR_CORE, None, ctypes.byref(length))
    if length.value == 0:
        return (0, 0, 0, 0, False)

    buf = ctypes.create_string_buffer(length.value)
    if not kernel32.GetLogicalProcessorInformationEx(
        RELATION_PROCESSOR_CORE, buf, ctypes.byref(length)
    ):
        return (0, 0, 0, 0, False)

    raw = buf.raw
    offset = 0
    classes: list[int] = []
    threads = 0
    ptr_size = ctypes.sizeof(ctypes.c_void_p)

    while offset + 8 <= len(raw):
        relationship = int.from_bytes(raw[offset : offset + 4], "little")
        size = int.from_bytes(raw[offset + 4 : offset + 8], "little")
        if size <= 0 or offset + size > len(raw):
            break
        if relationship == RELATION_PROCESSOR_CORE:
            # PROCESSOR_RELATIONSHIP: Flags(1) EfficiencyClass(1) Reserved(20)
            #                         GroupCount(2) GroupMask[]
            efficiency_class = raw[offset + 9]
            group_count = int.from_bytes(raw[offset + 30 : offset + 32], "little")
            classes.append(efficiency_class)
            base = offset + 32
            for g in range(group_count):
                mask_at = base + g * (ptr_size + 8)
                if mask_at + ptr_size > len(raw):
                    break
                mask = int.from_bytes(raw[mask_at : mask_at + ptr_size], "little")
                threads += bin(mask).count("1")
        offset += size

    if not classes:
        return (0, 0, 0, 0, False)

    cores = len(classes)
    distinct = sorted(set(classes))
    hybrid = len(distinct) > 1
    if hybrid:
        top = distinct[-1]
        p_cores = sum(1 for c in classes if c == top)
        e_cores = cores - p_cores
    else:
        p_cores, e_cores = cores, 0
    return (cores, threads or cores, p_cores, e_cores, hybrid)


# --------------------------------------------------------------------------
# Windows: display modes via EnumDisplaySettings
# --------------------------------------------------------------------------

class _DEVMODE(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32),
        ("dmSpecVersion", ctypes.c_ushort),
        ("dmDriverVersion", ctypes.c_ushort),
        ("dmSize", ctypes.c_ushort),
        ("dmDriverExtra", ctypes.c_ushort),
        ("dmFields", ctypes.c_ulong),
        ("dmPositionX", ctypes.c_long),
        ("dmPositionY", ctypes.c_long),
        ("dmDisplayOrientation", ctypes.c_ulong),
        ("dmDisplayFixedOutput", ctypes.c_ulong),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.c_wchar * 32),
        ("dmLogPixels", ctypes.c_ushort),
        ("dmBitsPerPel", ctypes.c_ulong),
        ("dmPelsWidth", ctypes.c_ulong),
        ("dmPelsHeight", ctypes.c_ulong),
        ("dmDisplayFlags", ctypes.c_ulong),
        ("dmDisplayFrequency", ctypes.c_ulong),
        ("dmICMMethod", ctypes.c_ulong),
        ("dmICMIntent", ctypes.c_ulong),
        ("dmMediaType", ctypes.c_ulong),
        ("dmDitherType", ctypes.c_ulong),
        ("dmReserved1", ctypes.c_ulong),
        ("dmReserved2", ctypes.c_ulong),
        ("dmPanningWidth", ctypes.c_ulong),
        ("dmPanningHeight", ctypes.c_ulong),
    ]


ENUM_CURRENT_SETTINGS = -1


def _display_info() -> tuple[int, int, int, int]:
    """Return (width, height, current_hz, max_hz_at_that_resolution)."""
    if not IS_WINDOWS:
        return (0, 0, 0, 0)
    user32 = ctypes.windll.user32
    current = _DEVMODE()
    current.dmSize = ctypes.sizeof(_DEVMODE)
    if not user32.EnumDisplaySettingsW(None, ENUM_CURRENT_SETTINGS, ctypes.byref(current)):
        return (0, 0, 0, 0)

    width, height = int(current.dmPelsWidth), int(current.dmPelsHeight)
    current_hz = int(current.dmDisplayFrequency)
    max_hz = current_hz

    mode = _DEVMODE()
    mode.dmSize = ctypes.sizeof(_DEVMODE)
    index = 0
    while user32.EnumDisplaySettingsW(None, index, ctypes.byref(mode)):
        if int(mode.dmPelsWidth) == width and int(mode.dmPelsHeight) == height:
            max_hz = max(max_hz, int(mode.dmDisplayFrequency))
        index += 1
        if index > 4000:  # defensive: some drivers enumerate a very long list
            break
    return (width, height, current_hz, max_hz)


# --------------------------------------------------------------------------
# Windows: CIM / registry inventory
# --------------------------------------------------------------------------

_PS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$cs  = Get-CimInstance Win32_ComputerSystem
$os  = Get-CimInstance Win32_OperatingSystem
$vc  = @(Get-CimInstance Win32_VideoController)
$mem = @(Get-CimInstance Win32_PhysicalMemory)

$vram = @()
$classKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
foreach ($sub in (Get-ChildItem $classKey)) {
    $p = Get-ItemProperty $sub.PSPath
    if ($p.'HardwareInformation.qwMemorySize') {
        $vram += [ordered]@{ desc = [string]$p.DriverDesc; bytes = [int64]$p.'HardwareInformation.qwMemorySize' }
    }
}

$media = [ordered]@{}
try {
    $phys = @{}
    foreach ($d in (Get-PhysicalDisk)) { $phys[[string]$d.DeviceId] = [string]$d.MediaType }
    foreach ($part in (Get-Partition)) {
        if ($part.DriveLetter) {
            $m = $phys[[string]$part.DiskNumber]
            if (-not $m) { $m = 'Unknown' }
            $media[[string]$part.DriveLetter] = $m
        }
    }
} catch {}

$result = [ordered]@{
    cpu_name      = [string]$cpu.Name
    cpu_vendor    = [string]$cpu.Manufacturer
    cores         = [int]$cpu.NumberOfCores
    threads       = [int]$cpu.NumberOfLogicalProcessors
    max_clock_mhz = [int]$cpu.MaxClockSpeed
    ram_bytes     = [int64]$cs.TotalPhysicalMemory
    os_caption    = [string]$os.Caption
    os_build      = [string]$os.BuildNumber
    vram          = $vram
    drive_media   = $media
    memory        = @($mem | ForEach-Object {
        [ordered]@{ capacity = [int64]$_.Capacity; speed = [int]$_.ConfiguredClockSpeed; slot = [string]$_.DeviceLocator }
    })
    gpus          = @($vc | ForEach-Object {
        [ordered]@{
            name   = [string]$_.Name
            driver = [string]$_.DriverVersion
            ram    = [int64]$_.AdapterRAM
            w      = [int]$_.CurrentHorizontalResolution
            h      = [int]$_.CurrentVerticalResolution
            hz     = [int]$_.CurrentRefreshRate
        }
    })
}
$result | ConvertTo-Json -Depth 6 -Compress
"""


def _run_powershell(script: str, timeout: int = 45) -> dict[str, Any]:
    creationflags = 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW
    proc = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-Command", script,
        ],
        capture_output=True, text=True, timeout=timeout, creationflags=creationflags,
    )
    out = (proc.stdout or "").strip()
    if not out:
        raise RuntimeError((proc.stderr or "PowerShell returned no output").strip()[:400])
    return json.loads(out)


_DISCRETE_HINTS = ("geforce", "radeon rx", "radeon pro", "arc a", "arc b", "quadro", "rtx", "gtx")
_IGPU_HINTS = ("uhd graphics", "hd graphics", "iris", "vega graphics", "radeon graphics", "890m", "880m", "780m", "760m", "arc 1")


def _pick_primary_gpu(gpus: list[dict[str, Any]]) -> dict[str, Any]:
    """Prefer a discrete adapter over an iGPU or a virtual display driver."""
    def rank(g: dict[str, Any]) -> tuple[int, int]:
        name = str(g.get("name", "")).lower()
        if any(h in name for h in ("basic display", "remote display", "virtual", "parsec", "meta ")):
            return (0, 0)
        if any(h in name for h in _DISCRETE_HINTS) and not any(h in name for h in _IGPU_HINTS):
            return (3, int(g.get("ram") or 0))
        if any(h in name for h in _IGPU_HINTS):
            return (1, int(g.get("ram") or 0))
        return (2, int(g.get("ram") or 0))

    return max(gpus, key=rank) if gpus else {}


def _sample_profile(note: str) -> HardwareProfile:
    return HardwareProfile(
        cpu_name="AMD Ryzen 7 7800X3D 8-Core Processor",
        cpu_vendor="amd", cores=8, threads=16, p_cores=8, e_cores=0, hybrid=False,
        max_clock_ghz=5.0,
        gpu_name="NVIDIA GeForce RTX 4070 SUPER", gpu_vendor="nvidia", vram_gb=12.0,
        driver_version="32.0.15.6094", all_gpus=["NVIDIA GeForce RTX 4070 SUPER"],
        ram_gb=32.0, ram_speed_mts=6000,
        ram_sticks=[MemoryStick(16.0, 6000, "DIMM 1"), MemoryStick(16.0, 6000, "DIMM 3")],
        width=2560, height=1440, refresh_hz=165, max_refresh_hz=165,
        os_name="Sample profile (not this machine)", os_build="-",
        detected=False, detection_notes=[note],
    )


def detect() -> HardwareProfile:
    """Detect the current machine. Never raises - degrades to a sample profile."""
    if not IS_WINDOWS:
        return _sample_profile(
            f"Not running on Windows (detected {platform.system()}). "
            "Showing a sample machine so the recommendation engine can be exercised."
        )

    profile = HardwareProfile(detected=True)
    notes: list[str] = []

    try:
        info = _run_powershell(_PS_SCRIPT)
    except Exception as exc:
        sample = _sample_profile(f"Hardware inventory failed: {exc}")
        return sample

    profile.cpu_name = re.sub(r"\s+", " ", str(info.get("cpu_name", ""))).strip() or "Unknown CPU"
    vendor_raw = str(info.get("cpu_vendor", "")).lower()
    profile.cpu_vendor = "amd" if "amd" in vendor_raw or "authenticamd" in vendor_raw else (
        "intel" if "intel" in vendor_raw or "genuineintel" in vendor_raw else "unknown"
    )
    profile.cores = int(info.get("cores") or 0)
    profile.threads = int(info.get("threads") or 0)
    profile.max_clock_ghz = round(int(info.get("max_clock_mhz") or 0) / 1000.0, 2)

    cores, threads, p_cores, e_cores, hybrid = _core_topology()
    if cores:
        profile.cores = cores
        profile.threads = max(threads, profile.threads)
        profile.p_cores, profile.e_cores, profile.hybrid = p_cores, e_cores, hybrid
    else:
        profile.p_cores = profile.cores
        notes.append("Could not read per-core efficiency classes; assuming a homogeneous CPU.")

    profile.ram_gb = round(int(info.get("ram_bytes") or 0) / (1024 ** 3), 1)
    sticks: list[MemoryStick] = []
    for entry in info.get("memory") or []:
        sticks.append(
            MemoryStick(
                capacity_gb=round(int(entry.get("capacity") or 0) / (1024 ** 3), 1),
                speed_mts=int(entry.get("speed") or 0),
                slot=str(entry.get("slot") or ""),
            )
        )
    profile.ram_sticks = sticks
    if sticks:
        profile.ram_speed_mts = max(s.speed_mts for s in sticks)

    gpus = info.get("gpus") or []
    profile.all_gpus = [str(g.get("name", "")) for g in gpus if g.get("name")]
    primary = _pick_primary_gpu(gpus)
    profile.gpu_name = re.sub(r"\s+", " ", str(primary.get("name", ""))).strip() or "Unknown GPU"
    profile.driver_version = str(primary.get("driver", ""))
    lowered = profile.gpu_name.lower()
    profile.gpu_vendor = (
        "nvidia" if "nvidia" in lowered or "geforce" in lowered
        else "amd" if "radeon" in lowered or "amd" in lowered
        else "intel" if "intel" in lowered or "arc" in lowered
        else "unknown"
    )

    # AdapterRAM is a signed 32-bit field and lies about anything over 4GB;
    # the driver's registry key carries the real number.
    vram_bytes = 0
    for entry in info.get("vram") or []:
        desc = str(entry.get("desc", "")).lower()
        if desc and (desc in lowered or lowered in desc):
            vram_bytes = max(vram_bytes, int(entry.get("bytes") or 0))
    if not vram_bytes:
        candidates = [int(e.get("bytes") or 0) for e in info.get("vram") or []]
        vram_bytes = max(candidates) if candidates else int(primary.get("ram") or 0)
    profile.vram_gb = round(vram_bytes / (1024 ** 3), 1)

    width, height, hz, max_hz = _display_info()
    if width:
        profile.width, profile.height = width, height
        profile.refresh_hz, profile.max_refresh_hz = hz, max_hz
    elif primary.get("w"):
        profile.width = int(primary.get("w") or 1920)
        profile.height = int(primary.get("h") or 1080)
        profile.refresh_hz = profile.max_refresh_hz = int(primary.get("hz") or 60)
        notes.append("Refresh rate read from the display adapter; the panel may support more.")

    profile.os_name = str(info.get("os_caption", ""))
    profile.os_build = str(info.get("os_build", ""))
    profile.drive_media = {
        str(k).upper(): str(v) for k, v in (info.get("drive_media") or {}).items()
    }

    if profile.vram_gb <= 0:
        notes.append("VRAM size could not be read; texture recommendations fall back to safe values.")
    if profile.ram_speed_mts == 0:
        notes.append("Memory speed unavailable (common on laptops with soldered memory).")

    profile.detection_notes = notes
    return profile
