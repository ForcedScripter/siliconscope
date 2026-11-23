"""SIMULATED fleet generator.

Usage:
    python -m fleet.simulator --n 200 --fault-rate 0.12 --seed 1 --out-dir data/fleet_1

Writes:
    <out-dir>/snapshots/<host_id>.json   one schema-v1 snapshot per server (source="simulator")
    <out-dir>/ground_truth.csv           host_id,fault_types  ("" = healthy, "A;B" = two faults)
    reports/sku_baselines.json           (with --write-baselines) SKU reference values + provenance

The validator reads ONLY the snapshots directory, never ground_truth.csv.

How a host is made (all randomness comes from one seeded numpy Generator, so a
seed reproduces the fleet exactly):
  1. pick a SKU (50 % sku-a, 30 % sku-b, 20 % sku-c);
  2. draw its *true* metrics around the SKU baseline (~2.5 % Gaussian
     part-to-part variation; CE counts mostly 0 with rare isolated 1–2s);
  3. with probability ``fault_rate`` inject a fault (5 % of faulty hosts get two);
  4. render the snapshot: the probe reports 3 samples per perf metric (1 %
     measurement noise) and their mean; ~3 % of hosts get one glitched sample.

SKU baselines: sku-a comes from the *real* Phase 1–2 measurements in
``reports/`` (latency plateaus and single-thread DRAM read bandwidth of the dev
machine). sku-b and sku-c are derived with the ratios in ``DERIVED_SKUS``.
Frequencies and temperatures are not measurable in the WSL2 VM, so they are
documented assumptions (see ``SKU_ASSUMPTIONS``).
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from collector.schema import SCHEMA_VERSION, assert_valid
from fleet.faults import DAYS, FAULT_TYPES, inject

PERF_KEYS = ["l1_ns", "l2_ns", "l3_ns", "dram_ns", "read_gbps"]
SKU_MIX = {"sku-a": 0.5, "sku-b": 0.3, "sku-c": 0.2}

HOST_NOISE = 0.025          # part-to-part variation of perf metrics (1 sigma)
FREQ_NOISE = 0.015          # part-to-part variation of loaded frequency
TEMP_NOISE_C = 3.0          # loaded temperature spread
SAMPLE_NOISE = 0.01         # run-to-run measurement noise per probe sample
N_SAMPLES = 3               # probe repeats reported per metric
GLITCH_RATE = 0.03          # fraction of hosts with one glitched sample
P_STRAY_CE = 0.12           # healthy host has one isolated day with 1–2 CEs
P_SECOND_FAULT = 0.05       # faulty host gets a second, different fault

# Not measurable in WSL2 (no cpufreq, no sensors) -> documented assumptions.
# sku-a frequencies are the dev CPU's published max / all-core turbo; temps are
# typical loaded package temperatures for a well-cooled part.
SKU_ASSUMPTIONS = {
    "sku-a": {"rated_mhz": 4200.0, "loaded_mhz": 3800.0, "loaded_temp_c": 72.0,
              "cache": {"L1": 48 << 10, "L2": 1280 << 10, "L3": 8 << 20},
              "cores": 4, "threads": 8, "sockets": 1},
    "sku-b": {"rated_mhz": 3500.0, "loaded_mhz": 3000.0, "loaded_temp_c": 76.0,
              "cache": {"L1": 48 << 10, "L2": 2 << 20, "L3": 60 << 20},
              "cores": 32, "threads": 64, "sockets": 2},
    "sku-c": {"rated_mhz": 3800.0, "loaded_mhz": 3400.0, "loaded_temp_c": 68.0,
              "cache": {"L1": 32 << 10, "L2": 1 << 20, "L3": 32 << 20},
              "cores": 24, "threads": 48, "sockets": 1},
}

# Perf ratios relative to sku-a, with the reasoning for each:
#   sku-b: big 2-socket server part — lower clock (+L1/L2 ns), large mesh L3 that is
#          slower to reach, remote-ish DRAM path, lower single-thread bandwidth.
#   sku-c: newer 1-socket part with DDR5 — similar core caches, mid-size L3,
#          slightly higher DRAM latency (DDR5) but more single-thread bandwidth.
DERIVED_SKUS = {
    "sku-b": {"l1_ns": 1.10, "l2_ns": 1.15, "l3_ns": 1.60, "dram_ns": 1.20, "read_gbps": 0.85},
    "sku-c": {"l1_ns": 0.95, "l2_ns": 1.00, "l3_ns": 1.25, "dram_ns": 1.05, "read_gbps": 1.25},
}


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------

def load_measured_baseline(reports: Path) -> dict[str, float]:
    """sku-a perf baseline from the real probe outputs in reports/."""
    lat = json.loads((reports / "latency_levels.json").read_text())["summary"]
    bw = json.loads((reports / "bandwidth_summary.json").read_text())
    return {"l1_ns": lat["l1_ns"], "l2_ns": lat["l2_ns"], "l3_ns": lat["l3_ns"],
            "dram_ns": lat["dram_ns"], "read_gbps": bw["threads_1"]["read"]["dram_gbps"]}


def sku_baselines(reports: Path) -> dict[str, dict[str, Any]]:
    """Full per-SKU baselines: perf (measured or derived) + assumptions."""
    measured = load_measured_baseline(reports)
    out = {"sku-a": {**measured, **SKU_ASSUMPTIONS["sku-a"],
                     "provenance": "perf measured on dev machine (reports/latency_levels.json, "
                                   "reports/bandwidth_summary.json); freq/temp assumed"}}
    for sku, ratios in DERIVED_SKUS.items():
        perf = {k: round(measured[k] * r, 3) for k, r in ratios.items()}
        out[sku] = {**perf, **SKU_ASSUMPTIONS[sku],
                    "provenance": f"SIMULATED: sku-a x {ratios}; freq/temp assumed"}
    return out


# ---------------------------------------------------------------------------
# Host generation
# ---------------------------------------------------------------------------

def host_id_for(seed: int, index: int) -> str:
    return hashlib.sha256(f"sim-{seed}-{index}".encode()).hexdigest()[:12]


def healthy_host(base: dict[str, Any], rng: np.random.Generator) -> dict[str, Any]:
    """True metrics of a healthy host: SKU baseline + part-to-part variation."""
    m: dict[str, Any] = {k: base[k] * rng.normal(1.0, HOST_NOISE) for k in PERF_KEYS}
    m["loaded_mhz"] = base["loaded_mhz"] * rng.normal(1.0, FREQ_NOISE)
    m["loaded_temp_c"] = base["loaded_temp_c"] + rng.normal(0.0, TEMP_NOISE_C)
    ce = [0] * DAYS
    if rng.random() < P_STRAY_CE:  # a rare isolated CE (cosmic ray), not a trend
        ce[int(rng.integers(0, DAYS))] = int(rng.integers(1, 3))
    m["ce_daily"] = ce
    m["ue"] = 0
    m["nic"] = {"cur_speed": 16.0, "max_speed": 16.0, "cur_width": 16, "max_width": 16}
    m["nvme"] = {"cur_speed": 16.0, "max_speed": 16.0, "cur_width": 4, "max_width": 4}
    return m


def choose_faults(rng: np.random.Generator, fault_rate: float) -> list[str]:
    if rng.random() >= fault_rate:
        return []
    faults = [str(rng.choice(FAULT_TYPES))]
    if rng.random() < P_SECOND_FAULT:
        faults.append(str(rng.choice([f for f in FAULT_TYPES if f != faults[0]])))
    return faults


def probe_samples(m: dict[str, Any], rng: np.random.Generator, glitch: bool) -> dict[str, list]:
    """3 measurement samples per perf metric; optionally corrupt one of them.

    A glitch imitates a one-off bad run (a noisy neighbour, an SMI, a host power
    state change — see reports/glitch/ for a real one): latency x1.3–1.8 or
    bandwidth x0.5–0.75 on a single sample of one metric.
    """
    samples = {k: [round(m[k] * rng.normal(1.0, SAMPLE_NOISE), 3) for _ in range(N_SAMPLES)]
               for k in PERF_KEYS}
    if glitch:
        key = str(rng.choice(PERF_KEYS))
        i = int(rng.integers(0, N_SAMPLES))
        factor = rng.uniform(0.5, 0.75) if key == "read_gbps" else rng.uniform(1.3, 1.8)
        samples[key][i] = round(samples[key][i] * factor, 3)
    return samples


def _link(bdf: str, pci_class: str, role: str, link: dict[str, Any]) -> dict[str, Any]:
    gen = {8.0: 3, 16.0: 4}
    return {"bdf": bdf, "class": pci_class, "role": role, "vendor": None, "device": None,
            "current_speed_gts": link["cur_speed"], "max_speed_gts": link["max_speed"],
            "current_gen": gen.get(link["cur_speed"]), "max_gen": gen.get(link["max_speed"]),
            "current_width": link["cur_width"], "max_width": link["max_width"],
            "downtrained": link["cur_speed"] < link["max_speed"]
            or link["cur_width"] < link["max_width"]}


def render_snapshot(host_id: str, sku: str, base: dict[str, Any], m: dict[str, Any],
                    samples: dict[str, list], collected_at: datetime) -> dict[str, Any]:
    """Turn true metrics into a schema-v1 snapshot (exactly what the collector emits)."""
    cache_levels = [{"level": int(name[1]), "type": "Unified" if name != "L1" else "Data",
                     "size_bytes": size, "line_bytes": 64, "ways": None, "shared_cpu_list": None}
                    for name, size in base["cache"].items()]
    pcie = [_link("0000-17-00.0", "0x020000", "nic", m["nic"]),
            _link("0000-5e-00.0", "0x010802", "nvme", m["nvme"])]
    idle_temp = round(m["loaded_temp_c"] - 25.0, 1)
    snap = {
        "schema_version": SCHEMA_VERSION,
        "host_id": host_id,
        "collected_at": collected_at.isoformat(timespec="seconds"),
        "source": "simulator",
        "sku": sku,
        "platform": {"available": True, "kernel": "6.8.0-sim", "os": "SIMULATED Linux",
                     "virtualization": "none", "host_id": host_id},
        "cpu": {"available": True, "model": f"SIMULATED {sku} CPU", "sockets": base["sockets"],
                "cores": base["cores"], "threads": base["threads"],
                "flags": {"avx2": True, "avx512f": sku != "sku-c"},
                "freq": {"available": True, "per_core": [], "max_mhz": base["rated_mhz"],
                         "cur_mhz_mean": round(m["loaded_mhz"], 1)}},
        "cache": {"available": True, "source": "simulator", "levels": cache_levels},
        "memory": {"available": True, "mem_total_bytes": 256 << 30,
                   "mem_available_bytes": 200 << 30,
                   "dimms": {"available": False,
                             "reason": "SIMULATED agent runs unprivileged (no dmidecode)"}},
        "thermal": {"available": True, "max_temp_c": idle_temp,
                    "zones": [{"name": "thermal_zone0", "kind": "thermal_zone",
                               "type": "x86_pkg_temp", "temp_c": idle_temp}]},
        "pcie": {"available": True, "devices": pcie,
                 "any_downtrained": any(d["downtrained"] for d in pcie)},
        "ecc": {"available": True,
                "controllers": [{"mc": "mc0", "ce_count": sum(m["ce_daily"]),
                                 "ue_count": m["ue"], "counts_readable": True}],
                "ce_total": sum(m["ce_daily"]), "ue_total": m["ue"],
                "ce_daily": list(m["ce_daily"])},
        "storage": {"available": True, "devices": [
            {"name": "nvme0n1", "size_bytes": 3840 << 30, "rotational": False,
             "model": "SIMULATED NVMe", "transport": "nvme",
             "smart": {"available": True, "passed": True}}]},
        "probe": {"available": True, "mode": "simulated",
                  **{k: round(float(np.mean(v)), 3) for k, v in samples.items()},
                  "samples": samples,
                  "loaded_cpu_mhz": round(m["loaded_mhz"], 1),
                  "loaded_max_temp_c": round(m["loaded_temp_c"], 1)},
    }
    return snap


def simulate(n: int, fault_rate: float, seed: int, baselines: dict[str, dict[str, Any]]
             ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Generate n snapshots and their ground truth. Deterministic for a given seed."""
    rng = np.random.default_rng(seed)
    skus = list(SKU_MIX)
    t0 = datetime(2026, 11, 1, tzinfo=UTC)
    snapshots, truth = [], []
    for i in range(n):
        sku = str(rng.choice(skus, p=list(SKU_MIX.values())))
        base = baselines[sku]
        m = healthy_host(base, rng)
        faults = choose_faults(rng, fault_rate)
        for f in faults:
            inject(m, f, rng)
        glitch = rng.random() < GLITCH_RATE
        samples = probe_samples(m, rng, glitch)
        hid = host_id_for(seed, i)
        snap = render_snapshot(hid, sku, base, m, samples, t0 + timedelta(minutes=i))
        snapshots.append(snap)
        truth.append({"host_id": hid, "fault_types": ";".join(faults)})
    return snapshots, truth


def write_fleet(out_dir: Path, snapshots: list[dict[str, Any]], truth: list[dict[str, str]]
                ) -> None:
    snap_dir = out_dir / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    for old in snap_dir.glob("*.json"):  # a re-run must not leave stale hosts behind
        old.unlink()
    for snap in snapshots:
        assert_valid(snap)  # the simulator must emit exactly the collector schema
        (snap_dir / f"{snap['host_id']}.json").write_text(json.dumps(snap, indent=1) + "\n")
    with (out_dir / "ground_truth.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["host_id", "fault_types"])
        w.writeheader()
        w.writerows(truth)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Generate a SIMULATED fleet of snapshots.")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--fault-rate", type=float, default=0.12)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument("--reports", type=Path, default=Path("reports"),
                    help="where the real probe summaries live (sku-a baseline)")
    ap.add_argument("--write-baselines", action="store_true",
                    help="also write reports/sku_baselines.json")
    args = ap.parse_args(argv)

    out_dir = args.out_dir or Path(f"data/fleet_{args.seed}")
    baselines = sku_baselines(args.reports)
    snapshots, truth = simulate(args.n, args.fault_rate, args.seed, baselines)
    write_fleet(out_dir, snapshots, truth)
    if args.write_baselines:
        (args.reports / "sku_baselines.json").write_text(
            json.dumps(copy.deepcopy(baselines), indent=2) + "\n")
    n_faulty = sum(1 for t in truth if t["fault_types"])
    print(f"SIMULATED fleet seed={args.seed}: {len(snapshots)} hosts, {n_faulty} faulty "
          f"-> {out_dir}")


if __name__ == "__main__":
    main()
