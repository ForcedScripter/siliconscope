"""Fault injection for the SIMULATED fleet — one function per fault type.

Each function takes a host's *true* metrics (a plain dict built by
``simulator.healthy_host``) and a numpy ``Generator``, and mutates the metrics
in place to look like that fault. Every function's docstring states the
real-world cause it imitates, so the simulated signatures are grounded in how
these failures actually present in a datacenter.

Metric keys used here:
    l1_ns, l2_ns, l3_ns, dram_ns   pointer-chase latency per level (ns)
    read_gbps                      single-thread DRAM read bandwidth (GB/s)
    loaded_mhz                     CPU frequency under load (MHz)
    loaded_temp_c                  max package temperature under load (°C)
    ce_daily                       14 daily ECC correctable-error counts
    ue                             ECC uncorrectable-error count
    nic, nvme                      PCIe link dicts: cur/max speed (GT/s) and width (lanes)
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

Metrics = dict[str, Any]

FAULT_TYPES = [
    "MEMORY_ECC_DEGRADATION",
    "MEMORY_UE",
    "PCIE_LINK_DOWNTRAIN",
    "THERMAL_THROTTLING",
    "MEMORY_CONFIG_SLOW",
    "SUBTLE_PERF_DEGRADATION",
]

DAYS = 14


def memory_ecc_degradation(m: Metrics, rng: np.random.Generator) -> None:
    """Correctable-error rate climbing over 14 days.

    Real cause: a DIMM cell/row/chip wearing out (or a marginal bit line). ECC
    keeps fixing single-bit flips, so nothing crashes, but the CE *rate* rises
    day over day. Left alone, these DIMMs often progress to uncorrectable errors.

    Daily counts are Poisson with a mean that grows linearly: lambda_d = a + b*d.
    The growth ``b`` is log-uniform in [0.12, 3.0] CE/day², so some hosts are
    in an early, hard-to-see stage and others are blatant.
    """
    a = rng.uniform(0.0, 1.5)
    b = float(np.exp(rng.uniform(np.log(0.12), np.log(3.0))))
    lam = a + b * np.arange(DAYS)
    m["ce_daily"] = [int(x) for x in rng.poisson(lam)]


def memory_ue(m: Metrics, rng: np.random.Generator) -> None:
    """One or more uncorrectable ECC errors (critical).

    Real cause: a multi-bit error ECC can detect but not fix — failed DRAM chip,
    bad DIMM contacts, or a failing memory-controller channel. The kernel raises
    a machine check and usually kills the affected process or panics. UEs are
    often accompanied by a small burst of CEs on the same day(s).
    """
    m["ue"] = 1 + int(rng.poisson(0.5))
    burst_days = rng.choice(DAYS, size=int(rng.integers(1, 3)), replace=False)
    for d in burst_days:
        m["ce_daily"][int(d)] += int(rng.integers(0, 6))


def pcie_link_downtrain(m: Metrics, rng: np.random.Generator) -> None:
    """NIC link trained below its capability: x8 instead of x16, or Gen3 instead of Gen4.

    Real cause: a damaged lane, a card not fully seated, a marginal riser or
    cable, or a dirty connector. Link training (LTSSM) falls back to fewer lanes
    or a lower generation; the NIC still works at ~half bandwidth, so it shows
    up as a "slow host", not as an error.
    """
    if rng.random() < 0.6:
        m["nic"]["cur_width"] = 8
    else:
        m["nic"]["cur_speed"] = 8.0  # Gen3


def thermal_throttling(m: Metrics, rng: np.random.Generator) -> None:
    """Hot and slow: high package temperature, loaded frequency 15–30 % low.

    Real cause: failed or under-speed fan, bad fan curve, clogged heatsink,
    dried thermal paste, missing blanking panels / blocked airflow. Firmware cuts
    frequency to stay under Tjmax.

    Physics we keep consistent: L1/L2 latency is fixed in *cycles*, so in *ns*
    it grows by 1/ratio when the clock drops. L3 runs partly on the uncore
    clock, so it grows by about half as much. DRAM latency barely moves;
    bandwidth dips slightly.
    """
    ratio = rng.uniform(0.70, 0.85)
    m["loaded_mhz"] *= ratio
    m["loaded_temp_c"] = rng.uniform(91.0, 99.0)
    m["l1_ns"] /= ratio
    m["l2_ns"] /= ratio
    m["l3_ns"] *= 1.0 + 0.5 * (1.0 / ratio - 1.0)
    m["dram_ns"] *= 1.02
    m["read_gbps"] *= rng.uniform(0.92, 0.97)


def memory_config_slow(m: Metrics, rng: np.random.Generator) -> None:
    """DIMMs running below rated speed: DRAM latency +10–20 %, bandwidth −20–30 %.

    Real cause: BIOS memory-speed setting, mixed DIMM part numbers forcing the
    slowest common speed, too many DIMMs per channel, or memory training
    falling back to a lower speed bin after marginal signal integrity. Caches
    are unaffected; only the DRAM path slows.
    """
    m["dram_ns"] *= rng.uniform(1.10, 1.20)
    m["read_gbps"] *= rng.uniform(0.70, 0.80)


SUBTLE_METRICS = ["l1_ns", "l2_ns", "l3_ns", "dram_ns", "read_gbps"]


def subtle_perf_degradation(m: Metrics, rng: np.random.Generator) -> None:
    """A 5–8 % slowdown across 3–4 perf metrics at once.

    Real cause: a firmware/microcode or BIOS-setting regression (e.g. a power
    profile, prefetcher or snoop-mode setting), a marginal part, or a NUMA
    misconfiguration. Each metric alone stays within ~3 sigma of the SKU noise,
    so per-metric thresholds miss it; only the *joint* shift is anomalous —
    which is what the ML stage is for.
    """
    k = int(rng.integers(3, 5))
    for key in rng.choice(SUBTLE_METRICS, size=k, replace=False):
        shift = rng.uniform(0.05, 0.08)
        if key == "read_gbps":
            m[key] *= 1.0 - shift  # bandwidth: lower is worse
        else:
            m[key] *= 1.0 + shift  # latency: higher is worse


INJECTORS: dict[str, Callable[[Metrics, np.random.Generator], None]] = {
    "MEMORY_ECC_DEGRADATION": memory_ecc_degradation,
    "MEMORY_UE": memory_ue,
    "PCIE_LINK_DOWNTRAIN": pcie_link_downtrain,
    "THERMAL_THROTTLING": thermal_throttling,
    "MEMORY_CONFIG_SLOW": memory_config_slow,
    "SUBTLE_PERF_DEGRADATION": subtle_perf_degradation,
}


def inject(m: Metrics, fault: str, rng: np.random.Generator) -> None:
    """Apply one named fault to a host's metrics."""
    INJECTORS[fault](m, rng)
