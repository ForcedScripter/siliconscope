"""Plot a bandwidth sweep and write a compact summary.

Usage:
    python -m scripts.plot_bandwidth reports/bandwidth_<host>.csv [--out-dir reports]

Input: CSV from ``probe/build/bandwidth`` (op,threads,size_bytes,gbps_median,gbps_p10,gbps_p90),
possibly holding several thread counts.
Outputs:
  * ``reports/bandwidth_<host>.png``      one panel per thread count, one line per op
  * ``reports/bandwidth_summary.json``    peak (cache) and DRAM GB/s per op and thread count
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from collector.readers.cache import data_cache_sizes, read_cache

OPS = ["read", "write", "copy"]


def summarize(df: pd.DataFrame, llc_bytes: int | None) -> dict[str, Any]:
    """Per (threads, op): peak GB/s (in-cache) and DRAM GB/s.

    "DRAM" = median GB/s over sizes >= 4x the last-level cache, i.e. clearly
    streaming from memory. If the LLC size is unknown we use the largest size.
    """
    out: dict[str, Any] = {}
    for (threads, op), g in df.groupby(["threads", "op"]):
        g = g.sort_values("size_bytes")
        if llc_bytes:
            dram = g[g["size_bytes"] >= 4 * llc_bytes]
        else:
            dram = g.tail(1)
        out.setdefault(f"threads_{threads}", {})[op] = {
            "peak_gbps": round(float(g["gbps_median"].max()), 2),
            "peak_at_bytes": int(g.loc[g["gbps_median"].idxmax(), "size_bytes"]),
            "dram_gbps": round(float(dram["gbps_median"].median()), 2) if len(dram) else None,
            "dram_sizes_used": [int(s) for s in dram["size_bytes"]],
        }
    return out


def plot(df: pd.DataFrame, reported: dict[str, int], out_png: Path, title: str) -> None:
    """Small multiples: one panel per thread count, same y-axis for comparison."""
    import matplotlib.pyplot as plt

    from scripts import plotstyle
    from scripts.plot_latency import _fmt_size

    plotstyle.apply()
    thread_counts = sorted(df["threads"].unique())
    fig, axes = plt.subplots(1, len(thread_counts), figsize=(5.5 * len(thread_counts), 4.5),
                             sharey=True, squeeze=False)
    for ax, t in zip(axes[0], thread_counts, strict=True):
        for i, op in enumerate(OPS):
            g = df[(df["threads"] == t) & (df["op"] == op)].sort_values("size_bytes")
            if g.empty:
                continue
            ax.fill_between(g["size_bytes"], g["gbps_p10"], g["gbps_p90"],
                            color=plotstyle.SERIES[i], alpha=0.15, linewidth=0)
            ax.plot(g["size_bytes"], g["gbps_median"], color=plotstyle.SERIES[i], marker="o",
                    markersize=3.5, label=op)
        for name, size in reported.items():
            # x is the per-thread buffer. L1/L2 are private to a core, so each thread
            # has the whole cache; L3 is shared, so t threads fill it at L3 / t each.
            x_line = size // t if name == "L3" else size
            ax.axvline(x_line, color=plotstyle.TEXT_SECONDARY, linestyle="--", linewidth=1)
            ax.annotate(name if name != "L3" or t == 1 else f"L3/{t}", (x_line, 1),
                        xycoords=("data", "axes fraction"), xytext=(3, -12),
                        textcoords="offset points", fontsize=8, color=plotstyle.TEXT_SECONDARY)
        ax.set_xscale("log", base=2)
        sizes = df["size_bytes"]
        ticks = [2 ** p for p in range(int(sizes.min()).bit_length() - 1,
                                       int(sizes.max()).bit_length(), 3)]
        ax.set_xticks(ticks)
        ax.set_xticklabels([_fmt_size(x) for x in ticks])
        ax.set_xlabel("buffer size per thread (log2)")
        ax.set_title(f"{t} thread{'s' if t > 1 else ''} (aggregate)", loc="left")
    axes[0][0].set_ylabel("GB/s")
    axes[0][0].legend(loc="upper right")
    fig.suptitle(title, x=0.01, ha="left", fontweight="bold")
    fig.savefig(out_png)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("csv", type=Path)
    ap.add_argument("--out-dir", type=Path, default=Path("reports"))
    ap.add_argument("--root", default="/")
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    reported = data_cache_sizes(read_cache(args.root))
    summary = summarize(df, reported.get("L3"))
    summary["source_csv"] = args.csv.as_posix()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "bandwidth_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    png = args.out_dir / f"{args.csv.stem}.png"
    host = args.csv.stem.replace("bandwidth_", "host ")
    plot(df, reported, png, f"Sequential bandwidth — {host}")
    for key, ops in summary.items():
        if key.startswith("threads_"):
            for op, s in ops.items():
                print(f"{key:>10} {op:>5}: peak {s['peak_gbps']:7.2f} GB/s, "
                      f"DRAM {s['dram_gbps']} GB/s")
    print(f"wrote {png} and {args.out_dir / 'bandwidth_summary.json'}")


if __name__ == "__main__":
    main()
