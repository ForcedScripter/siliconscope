"""CPU reader: /proc/cpuinfo (model, topology, flags) + cpufreq (per-core MHz)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import read_int, read_text, rpath, unavailable

# ISA features a silicon/perf team cares about: vector widths and crypto/matrix
# extensions change which code paths (and performance) a server gets.
FLAGS_OF_INTEREST = ["sse4_2", "avx", "avx2", "fma", "avx512f", "avx512bw", "avx512_vnni",
                     "amx_tile", "aes", "sha_ni", "hypervisor"]


def parse_cpuinfo(text: str) -> dict[str, Any]:
    """Parse /proc/cpuinfo into model, socket/core/thread counts and flags."""
    model = None
    flags: set[str] = set()
    sockets: set[str] = set()
    cores: set[tuple[str, str]] = set()
    threads = 0
    mhz: list[float] = []
    physical_id = "0"
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, value = (s.strip() for s in line.partition(":"))
        if key == "processor":
            threads += 1
        elif key == "model name" and model is None:
            model = value
        elif key == "physical id":
            physical_id = value
            sockets.add(value)
        elif key == "core id":
            cores.add((physical_id, value))
        elif key == "flags" and not flags:
            flags = set(value.split())
        elif key == "cpu MHz":
            try:
                mhz.append(float(value))
            except ValueError:
                pass
    return {
        "model": model,
        "sockets": len(sockets) or (1 if threads else None),
        "cores": len(cores) or None,
        "threads": threads or None,
        "flags": {f: f in flags for f in FLAGS_OF_INTEREST},
        "cpuinfo_mhz_mean": round(sum(mhz) / len(mhz), 1) if mhz else None,
    }


def read_cpufreq(root: str | Path) -> dict[str, Any]:
    """Per-core current/max frequency from cpufreq (kHz in sysfs -> MHz here).

    Often missing in VMs: the hypervisor owns frequency scaling.
    """
    base = rpath(root, "/sys/devices/system/cpu")
    cpus = sorted((p for p in base.glob("cpu[0-9]*") if p.name[3:].isdigit()),
                  key=lambda p: int(p.name[3:])) if base.is_dir() else []
    per_core = []
    for p in cpus:
        rel = f"/sys/devices/system/cpu/{p.name}/cpufreq"
        cur = read_int(root, f"{rel}/scaling_cur_freq")
        mx = read_int(root, f"{rel}/cpuinfo_max_freq")
        if cur is None and mx is None:
            continue
        per_core.append({"cpu": int(p.name[3:]),
                         "cur_mhz": cur / 1000 if cur is not None else None,
                         "max_mhz": mx / 1000 if mx is not None else None})
    if not per_core:
        return unavailable("cpufreq not exposed (common in VMs: the hypervisor controls DVFS)")
    maxes = [c["max_mhz"] for c in per_core if c["max_mhz"]]
    curs = [c["cur_mhz"] for c in per_core if c["cur_mhz"]]
    return {"available": True, "per_core": per_core,
            "max_mhz": max(maxes) if maxes else None,
            "cur_mhz_mean": round(sum(curs) / len(curs), 1) if curs else None}


def read_cpu(root: str | Path = "/") -> dict[str, Any]:
    """CPU model, topology, ISA flags and frequencies."""
    try:
        text = read_text(root, "/proc/cpuinfo")
        if text is None:
            return unavailable("/proc/cpuinfo not readable")
        info = parse_cpuinfo(text)
        info["freq"] = read_cpufreq(root)
        return {"available": True, **info}
    except Exception as exc:  # noqa: BLE001 — a reader must never raise
        return unavailable(f"cpu reader error: {exc}")
