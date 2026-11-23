"""Plot a latency sweep and infer the cache hierarchy from it.

Usage:
    python -m scripts.plot_latency reports/latency_<host>.csv [--out-dir reports] [--root /]

Inputs: the CSV written by ``probe/build/latency`` (size_bytes,ns_median,ns_p10,ns_p90).
Outputs:
  * ``reports/latency_<host>.png``   latency curve, p10–p90 band, reported cache sizes
  * ``reports/latency_levels.json``  detected levels + knees + a compact summary

How levels are detected (``detect_levels``)
-------------------------------------------
A latency curve is roughly a staircase: flat while the working set fits a cache
level, then a step up when it spills to the next. We fit the best
*piecewise-constant* function to log(latency) with dynamic programming (classic
change-point detection) and keep adding steps while each new step still removes a
large share of the remaining error. Each step = one inferred level; the boundary
between two steps = a "knee". We fit on **p10** rather than the median because
interference from other tenants on a VM only ever *adds* time, so the lower
percentile is closer to the hardware's true cost.

Names (L1/L2/L3/DRAM) come from comparing each level's largest size against the
sizes the kernel reports in sysfs; the *detection itself* does not use them.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from collector.readers.cache import data_cache_sizes, read_cache
from collector.readers.common import host_id

# A new step must cut the remaining squared error by at least this fraction to
# be accepted. Tuned once on the real sweep in reports/ (see docs/DECISIONS.md).
MIN_GAIN = 0.35
MAX_LEVELS = 6
MIN_POINTS_PER_LEVEL = 2


# ---------------------------------------------------------------------------
# Change-point detection
# ---------------------------------------------------------------------------

def _segment_costs(y: np.ndarray) -> np.ndarray:
    """cost[i, j] = squared error of fitting y[i:j] with its mean (j exclusive)."""
    n = len(y)
    c1 = np.concatenate([[0.0], np.cumsum(y)])
    c2 = np.concatenate([[0.0], np.cumsum(y * y)])
    cost = np.full((n + 1, n + 1), np.inf)
    for i in range(n):
        for j in range(i + MIN_POINTS_PER_LEVEL, n + 1):
            s, s2, m = c1[j] - c1[i], c2[j] - c2[i], j - i
            cost[i, j] = s2 - s * s / m
    return cost


def best_segmentation(y: np.ndarray, k: int) -> tuple[float, list[int]]:
    """Optimal split of y into k contiguous segments. Returns (sse, start indices)."""
    n = len(y)
    cost = _segment_costs(y)
    # dp[s][j] = min error covering y[:j] with s segments; back[s][j] = start of last segment
    dp = np.full((k + 1, n + 1), np.inf)
    back = np.zeros((k + 1, n + 1), dtype=int)
    dp[0, 0] = 0.0
    for s in range(1, k + 1):
        for j in range(1, n + 1):
            for i in range(j):
                cand = dp[s - 1, i] + cost[i, j]
                if cand < dp[s, j]:
                    dp[s, j], back[s, j] = cand, i
    starts, j = [], n
    for s in range(k, 0, -1):
        i = back[s, j]
        starts.append(i)
        j = i
    return float(dp[k, n]), sorted(starts)


def choose_segmentation(y: np.ndarray) -> list[int]:
    """Add segments while each one removes >= MIN_GAIN of the remaining error."""
    max_k = min(MAX_LEVELS, len(y) // MIN_POINTS_PER_LEVEL)
    sse, starts = best_segmentation(y, 1)
    for k in range(2, max_k + 1):
        sse_k, starts_k = best_segmentation(y, k)
        if not math.isfinite(sse_k) or sse <= 0 or (sse - sse_k) / sse < MIN_GAIN:
            break
        sse, starts = sse_k, starts_k
    return starts


def _name_level(first_size: int, reported: dict[str, int]) -> str:
    """Name a level by the smallest reported cache that holds its *first* size.

    A plateau begins right after the previous level overflows, so where it starts
    says which cache is serving it; its end can run past the cache's nominal size
    (replacement policy and sharing make capacity edges soft).
    """
    for name in ("L1", "L2", "L3"):
        # Strict "<": a working set as big as the cache can't be served by it alone
        # (other data — page tables, the OS, the other cores — also lives there).
        if name in reported and first_size < reported[name]:
            return name
    return "DRAM" if reported else "unknown"


def detect_levels(df: pd.DataFrame, reported: dict[str, int]) -> dict[str, Any]:
    """Infer levels and knees from a latency sweep."""
    sizes = df["size_bytes"].to_numpy()
    p10 = df["ns_p10"].to_numpy()
    median = df["ns_median"].to_numpy()
    starts = choose_segmentation(np.log(p10))
    bounds = starts + [len(df)]

    levels = []
    for a, b in zip(bounds[:-1], bounds[1:], strict=True):
        levels.append({
            "name": _name_level(int(sizes[a]), reported),
            "min_size_bytes": int(sizes[a]),
            "max_size_bytes": int(sizes[b - 1]),
            "ns_median": round(float(np.median(median[a:b])), 3),
            "ns_p10": round(float(np.median(p10[a:b])), 3),
            "n_points": int(b - a),
        })
    # Disambiguate repeated names, e.g. two segments both inside L3.
    seen: dict[str, int] = {}
    for lv in levels:
        seen[lv["name"]] = seen.get(lv["name"], 0) + 1
        if seen[lv["name"]] > 1:
            lv["name"] = f"{lv['name']}#{seen[lv['name']]}"

    knees = []
    for i in starts[1:]:
        knees.append({
            "before_bytes": int(sizes[i - 1]),
            "after_bytes": int(sizes[i]),
            "ratio_p10": round(float(p10[i] / p10[i - 1]), 3),
        })
    return {"levels": levels, "knees": knees}


def match_knees(knees: list[dict[str, Any]], reported: dict[str, int]) -> dict[str, Any]:
    """For each reported cache size, find the nearest knee and its distance in octaves.

    A knee "matches" if the reported size lies within half an octave (x1.41) of
    the knee's midpoint — the resolution of our 1.5x-spaced size grid.
    """
    out = {}
    for name, size in reported.items():
        if not knees:
            out[name] = {"reported_bytes": size, "nearest_knee": None, "matches": False}
            continue
        def dist(k: dict[str, Any], size: int = size) -> float:
            mid = math.sqrt(k["before_bytes"] * k["after_bytes"])
            return abs(math.log2(mid / size))
        best = min(knees, key=dist)
        out[name] = {
            "reported_bytes": size,
            "nearest_knee": best,
            "octaves_away": round(dist(best), 3),
            "matches": dist(best) <= 0.5,
        }
    return out


def summarize(df: pd.DataFrame, reported: dict[str, int]) -> dict[str, float | None]:
    """Compact per-level latency (ns) used by the collector and fleet simulator.

    Preferred: the *first* detected plateau carrying each name (L1/L2/L3/DRAM).
    The first plateau inside a cache is the cleanest reading of it — later
    sub-plateaus in the same cache are inflated by TLB misses (see LEARNING.md).
    Fallback for a level with no plateau: the median latency at the largest
    measured size <= half that cache. Without reported sizes at all we use the
    detected levels in order.
    """
    sizes, med = df["size_bytes"].to_numpy(), df["ns_median"].to_numpy()
    summary: dict[str, float | None] = {"l1_ns": None, "l2_ns": None, "l3_ns": None,
                                        "dram_ns": None}
    if reported:
        detected = {lv["name"]: lv["ns_median"] for lv in detect_levels(df, reported)["levels"]}
        for name in ("L1", "L2", "L3", "DRAM"):
            if name in detected:
                summary[f"{name.lower()}_ns"] = detected[name]
                continue
            if name in reported:
                idx = np.where(sizes <= reported[name] / 2)[0]
                if len(idx):
                    summary[f"{name.lower()}_ns"] = round(float(med[idx[-1]]), 3)
        largest_cache = max(reported.values())
        # Only call it DRAM if we measured well past the last-level cache.
        if summary["dram_ns"] is None and sizes[-1] >= 4 * largest_cache:
            summary["dram_ns"] = round(float(med[-1]), 3)
    else:
        levels = detect_levels(df, {})["levels"]
        for key, lv in zip(("l1_ns", "l2_ns", "l3_ns"), levels[:-1], strict=False):
            summary[key] = lv["ns_median"]
        if len(levels) > 1:
            summary["dram_ns"] = levels[-1]["ns_median"]
    return summary


# ---------------------------------------------------------------------------
# Plot
# ---------------------------------------------------------------------------

def _fmt_size(b: float) -> str:
    if b >= 1 << 20:
        v = b / (1 << 20)
        return f"{v:g} MB"
    return f"{b / 1024:g} KB"


def plot(df: pd.DataFrame, reported: dict[str, int], result: dict[str, Any], out_png: Path,
         title: str) -> None:
    """Latency vs size on log2 x-axis, p10–p90 band, reported sizes, detected levels."""
    import matplotlib.pyplot as plt

    from scripts import plotstyle

    plotstyle.apply()
    fig, ax = plt.subplots(figsize=(9, 5))
    x = df["size_bytes"]
    ax.fill_between(x, df["ns_p10"], df["ns_p90"], color=plotstyle.SERIES[0], alpha=0.18,
                    linewidth=0, label="p10–p90")
    ax.plot(x, df["ns_median"], color=plotstyle.SERIES[0], marker="o", markersize=4,
            label="median")

    # Detected plateaus as thin horizontal segments with a direct label.
    for lv in result["levels"]:
        ax.hlines(lv["ns_median"], lv["min_size_bytes"], lv["max_size_bytes"],
                  color=plotstyle.SERIES[1], linewidth=2, alpha=0.9)
        ax.annotate(f"{lv['name']} ≈ {lv['ns_median']:.1f} ns",
                    (lv["min_size_bytes"], lv["ns_median"]), textcoords="offset points",
                    xytext=(0, 6), fontsize=8, color=plotstyle.TEXT)
    ax.plot([], [], color=plotstyle.SERIES[1], label="detected level (median)")

    # Reported (sysfs) cache sizes as vertical reference lines.
    for name, size in reported.items():
        ax.axvline(size, color=plotstyle.TEXT_SECONDARY, linestyle="--", linewidth=1)
        ax.annotate(f"{name} {_fmt_size(size)}", (size, 1), xycoords=("data", "axes fraction"),
                    xytext=(3, -12), textcoords="offset points", fontsize=8,
                    color=plotstyle.TEXT_SECONDARY)

    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ticks = [2 ** p for p in range(int(math.log2(x.min())), int(math.log2(x.max())) + 1, 2)]
    ax.set_xticks(ticks)
    ax.set_xticklabels([_fmt_size(t) for t in ticks], rotation=0)
    ax.set_xlabel("working-set size (log2)")
    ax.set_ylabel("ns per dependent load (log)")
    ax.set_title(title, loc="left")
    ax.legend(loc="upper left")
    fig.savefig(out_png)
    plt.close(fig)


def plot_compare(runs: list[tuple[str, pd.DataFrame]], reported: dict[str, int], out_png: Path,
                 title: str) -> None:
    """Overlay several latency sweeps (e.g. 4 KiB vs huge pages vs sequential)."""
    import matplotlib.pyplot as plt

    from scripts import plotstyle

    plotstyle.apply()
    fig, ax = plt.subplots(figsize=(9, 5))
    for i, (label, df) in enumerate(runs):
        color = plotstyle.SERIES[i]
        ax.fill_between(df["size_bytes"], df["ns_p10"], df["ns_p90"], color=color, alpha=0.12,
                        linewidth=0)
        ax.plot(df["size_bytes"], df["ns_median"], color=color, marker="o", markersize=3.5,
                label=label)
    for name, size in reported.items():
        ax.axvline(size, color=plotstyle.TEXT_SECONDARY, linestyle="--", linewidth=1)
        ax.annotate(f"{name} {_fmt_size(size)}", (size, 1), xycoords=("data", "axes fraction"),
                    xytext=(3, -12), textcoords="offset points", fontsize=8,
                    color=plotstyle.TEXT_SECONDARY)
    x = runs[0][1]["size_bytes"]
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ticks = [2 ** p for p in range(int(math.log2(x.min())), int(math.log2(x.max())) + 1, 2)]
    ax.set_xticks(ticks)
    ax.set_xticklabels([_fmt_size(t) for t in ticks])
    ax.set_xlabel("working-set size (log2)")
    ax.set_ylabel("ns per dependent load (log)")
    ax.set_title(title, loc="left")
    ax.legend(loc="upper left")
    fig.savefig(out_png)
    plt.close(fig)


def comparison_table(runs: list[tuple[str, pd.DataFrame]]) -> list[dict[str, Any]]:
    """Median ns per size for every run side by side (sizes common to all runs)."""
    merged = runs[0][1][["size_bytes", "ns_median"]].rename(columns={"ns_median": runs[0][0]})
    for label, df in runs[1:]:
        merged = merged.merge(
            df[["size_bytes", "ns_median"]].rename(columns={"ns_median": label}), on="size_bytes")
    return merged.round(3).to_dict(orient="records")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("csv", type=Path)
    ap.add_argument("--out-dir", type=Path, default=Path("reports"))
    ap.add_argument("--root", default="/", help="filesystem root for reading cache sysfs")
    ap.add_argument("--levels-name", default="latency_levels.json")
    ap.add_argument("--title", default=None)
    ap.add_argument("--compare", nargs="*", default=[], metavar="CSV:LABEL",
                    help="extra sweeps to overlay, e.g. reports/latency_hugepages_x.csv:'huge'")
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    cache = read_cache(args.root)
    reported = data_cache_sizes(cache)
    result = detect_levels(df, reported)
    result["reported_cache_bytes"] = reported
    result["knee_match"] = match_knees(result["knees"], reported)
    result["summary"] = summarize(df, reported)
    result["source_csv"] = str(args.csv.as_posix())

    # Optional comparison runs: detect their plateaus too, and tabulate side by side.
    runs = [("random, 4 KiB pages", df)]
    result["comparisons"] = {}
    for spec in args.compare:
        path, _, label = spec.partition(":")
        cdf = pd.read_csv(path)
        runs.append((label or Path(path).stem, cdf))
        result["comparisons"][label or Path(path).stem] = {
            "source_csv": path,
            "levels": detect_levels(cdf, reported)["levels"],
        }
    if len(runs) > 1:
        result["comparison_ns_median"] = comparison_table(runs)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    if len(runs) > 1:
        plot_compare(runs, reported, args.out_dir / f"latency_compare_{host_id()}.png",
                     f"Latency: page size and access pattern — host {host_id()}")
    (args.out_dir / args.levels_name).write_text(json.dumps(result, indent=2) + "\n")
    png = args.out_dir / f"{args.csv.stem}.png"
    if args.csv.stem == "latency" or not args.csv.stem.startswith("latency_"):
        png = args.out_dir / f"latency_{host_id()}.png"
    plot(df, reported, result, png,
         args.title or f"Pointer-chase latency — host {host_id()} (pinned, 4 KiB pages)")

    for lv in result["levels"]:
        print(f"{lv['name']:>7}: {_fmt_size(lv['min_size_bytes']):>8} – "
              f"{_fmt_size(lv['max_size_bytes']):>8}  ~{lv['ns_median']:.2f} ns")
    for name, m in result["knee_match"].items():
        print(f"reported {name} {_fmt_size(m['reported_bytes'])}: "
              f"matches={m['matches']} ({m.get('octaves_away')} octaves)")
    print(f"wrote {png} and {args.out_dir / args.levels_name}")


if __name__ == "__main__":
    main()
