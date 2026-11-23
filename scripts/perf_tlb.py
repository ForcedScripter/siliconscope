"""TLB experiment: perf counters for the latency probe at fixed sizes, 4 KiB vs 2 MiB pages.

Usage:
    python -m scripts.perf_tlb [--sizes-mb 1 2 4 6 16 64] [--repeats 3] [--out-dir reports]

For each working-set size we run ``probe/build/latency`` at exactly that size,
once with normal 4 KiB pages and once with ``--hugepages``, under
``scripts/run_perf.sh``. We then compute **dTLB load misses per pointer-chase
hop**. If a latency step is caused by TLB misses, huge pages should (a) slash the
misses per hop and (b) remove most of the extra latency at that size.

Outputs ``reports/perf_tlb.csv`` and ``reports/perf_tlb.md``. If perf is not
usable on this machine the script writes a note saying so and exits 0.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
LATENCY = ROOT / "probe" / "build" / "latency"
RUN_PERF = ROOT / "scripts" / "run_perf.sh"
EVENTS = ["cycles", "instructions", "cache-misses", "cache-references", "dTLB-load-misses"]


def hops_for(size_bytes: int, repeats: int) -> int:
    """Pointer-chase hops the probe performs at one size (warm-up lap + timed repeats).

    Mirrors latency.cpp: steps = max(n, 2^21); warm-up = n hops. Setup work
    (building the Sattolo cycle) also runs under perf, so per-hop figures are a
    slight overestimate — equally for both page sizes, so the comparison holds.
    """
    n = size_bytes // 64
    return n + repeats * max(n, 1 << 21)


def parse_perf(text: str) -> dict[str, float]:
    """Parse ``perf stat -x,`` CSV lines into {event: value}. Missing -> NaN."""
    out = {e: float("nan") for e in EVENTS}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split(",")
        if len(parts) < 3:
            continue
        event = parts[2].split(":")[0]  # "cycles:u" -> "cycles"
        if event in out:
            try:
                out[event] = float(parts[0])
            except ValueError:  # "<not supported>"
                pass
    return out


def run_one(size_mb: float, hugepages: bool, repeats: int) -> tuple[dict[str, float], float, str]:
    """Run one config under perf; return (counters, median ns from the probe, raw perf text)."""
    size_kb = int(size_mb * 1024)
    cmd = [str(LATENCY), "--min-kb", str(size_kb), "--max-mb", str(max(1, int(size_mb))),
           "--repeats", str(repeats)]
    if hugepages:
        cmd.append("--hugepages")
    # The probe's own CSV gives the latency at this size; perf gives the counters.
    probe = subprocess.run(cmd, capture_output=True, text=True, check=True)
    ns = float(probe.stdout.strip().splitlines()[1].split(",")[1])
    perf = subprocess.run([str(RUN_PERF), "--", *cmd], capture_output=True, text=True)
    return parse_perf(perf.stdout), ns, perf.stdout + perf.stderr


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sizes-mb", type=float, nargs="+", default=[1, 2, 4, 6, 16, 64])
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out-dir", type=Path, default=Path("reports"))
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    rows, perf_ok = [], True
    for size_mb in args.sizes_mb:
        for huge in (False, True):
            counters, ns, raw = run_one(size_mb, huge, args.repeats)
            if all(pd.isna(v) for v in counters.values()):
                perf_ok = False
            hops = hops_for(int(size_mb * (1 << 20)), args.repeats)
            rows.append({
                "size_mb": size_mb, "page": "2MiB" if huge else "4KiB", "ns_per_hop": ns,
                **counters, "hops": hops,
                "dtlb_miss_per_hop": counters["dTLB-load-misses"] / hops,
                "cache_miss_per_hop": counters["cache-misses"] / hops,
            })
            print(f"{size_mb:>5} MB {'2MiB' if huge else '4KiB'}: {ns:7.2f} ns/hop, "
                  f"dTLB misses/hop {rows[-1]['dtlb_miss_per_hop']:.3f}")
    df = pd.DataFrame(rows)
    df.to_csv(args.out_dir / "perf_tlb.csv", index=False)

    md = ["# TLB experiment (real measurement, `make perf`)", ""]
    if not perf_ok:
        md.append("perf counters were **not available** on this machine; see "
                  "`scripts/run_perf.sh` output. Only latency columns are meaningful.")
    view = df[["size_mb", "page", "ns_per_hop", "dtlb_miss_per_hop", "cache_miss_per_hop"]]
    md += ["| size (MB) | page | ns/hop | dTLB misses/hop | LLC misses/hop |",
           "|---:|---|---:|---:|---:|"]
    for r in view.itertuples(index=False):
        md.append(f"| {r.size_mb:g} | {r.page} | {r.ns_per_hop:.2f} | "
                  f"{r.dtlb_miss_per_hop:.3f} | {r.cache_miss_per_hop:.3f} |")
    md += ["", "Per-hop counts include the probe's setup (building the cycle), so they slightly "
           "overstate the chase itself — equally for both page sizes."]
    (args.out_dir / "perf_tlb.md").write_text("\n".join(md) + "\n")
    print(f"wrote {args.out_dir / 'perf_tlb.csv'} and {args.out_dir / 'perf_tlb.md'}")


if __name__ == "__main__":
    main()
