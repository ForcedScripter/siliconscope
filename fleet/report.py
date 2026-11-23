"""Render validator results: CSV, a triage report in Markdown, and plots.

Outputs (in ``out_dir``, normally reports/):
  fleet_results.csv      one row per host: flagged, severity, causes, action, evidence
  fleet_report.md        triage list, worst first, with evidence values
  fleet_scatter.png      DRAM latency vs read bandwidth per SKU, coloured by verdict
  fleet_ce_trends.png    daily CE counts for hosts flagged with a memory-ECC cause
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from fleet.rules import Thresholds

RESULT_COLUMNS = ["host_id", "sku", "flagged", "severity", "primary_cause", "causes",
                  "action", "evidence", "rules_fired", "ml_flag", "if_score", "magnitude"]


def write_results_csv(results: pd.DataFrame, path: Path) -> None:
    results[RESULT_COLUMNS].to_csv(path, index=False)


def triage_markdown(results: pd.DataFrame, th: Thresholds, label: str) -> str:
    flagged = results[results["flagged"]]
    lines = [
        "# Fleet triage report (SIMULATED fleet)", "",
        f"> All hosts in this report are **SIMULATED** (`fleet/simulator.py`, `{label}`). "
        "The sku-a baseline is seeded from real measurements of the dev machine.", "",
        f"**{len(results)} hosts validated — {len(flagged)} flagged** "
        f"({int((flagged['severity'] == 'critical').sum())} critical, "
        f"{int((flagged['severity'] == 'warning').sum())} warning).", "",
        "Thresholds (tuned on seed 1 only): "
        + ", ".join(f"`{k}={v}`" for k, v in th.__dict__.items()) + "", "",
        "## Summary by cause", "",
        "| Cause | Hosts |", "|---|---:|",
    ]
    counts: dict[str, int] = {}
    for causes in flagged["causes"]:
        for c in causes.split(";"):
            counts[c] = counts.get(c, 0) + 1
    for c, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {c} | {n} |")
    lines += ["", "## Triage list (worst first)", ""]
    for i, r in enumerate(flagged.itertuples(index=False), start=1):
        lines += [
            f"### {i}. `{r.host_id}` ({r.sku}) — **{r.severity.upper()}**: {r.causes}",
            "",
            f"- **Evidence:** {r.evidence}",
            f"- **Action:** {r.action}",
            f"- Rules fired: `{r.rules_fired or '-'}`; ML flag: {r.ml_flag}"
            + (f" (score {r.if_score})" if r.if_score == r.if_score and r.if_score is not None
               else ""),
            "",
        ]
    return "\n".join(lines) + "\n"


def plot_scatter(results: pd.DataFrame, feats: pd.DataFrame, path: Path) -> None:
    """Small multiples per SKU: DRAM latency vs read bandwidth, coloured by verdict."""
    import matplotlib.pyplot as plt

    from scripts import plotstyle

    plotstyle.apply()
    df = feats.merge(results[["host_id", "severity", "primary_cause"]], on="host_id")
    skus = sorted(df["sku"].unique())
    fig, axes = plt.subplots(1, len(skus), figsize=(5 * len(skus), 4.4), squeeze=False)
    styles = [("ok", plotstyle.SERIES[0], "o", "healthy (not flagged)", 0.55),
              ("warning", plotstyle.WARNING, "D", "warning", 1.0),
              ("critical", plotstyle.CRITICAL, "X", "critical", 1.0)]
    for ax, sku in zip(axes[0], skus, strict=True):
        g = df[df["sku"] == sku]
        for sev, color, marker, label, alpha in styles:
            s = g[g["severity"] == sev]
            ax.scatter(s["dram_ns"], s["read_gbps"], c=color, marker=marker, s=36 if sev == "ok"
                       else 60, alpha=alpha, edgecolors=plotstyle.SURFACE, linewidths=1.2,
                       label=f"{label} ({len(s)})")
        ax.set_title(f"{sku} — {len(g)} hosts", loc="left")
        ax.set_xlabel("DRAM latency (ns, median of samples)")
        ax.legend(loc="lower left", fontsize=8)
    axes[0][0].set_ylabel("read bandwidth (GB/s)")
    fig.suptitle("SIMULATED fleet: memory latency vs bandwidth by verdict", x=0.01, ha="left",
                 fontweight="bold")
    fig.savefig(path)
    plt.close(fig)


def plot_ce_trends(results: pd.DataFrame, feats: pd.DataFrame, path: Path) -> None:
    """Daily CE counts for hosts flagged with a memory-ECC cause vs a healthy envelope."""
    import matplotlib.pyplot as plt

    from scripts import plotstyle

    plotstyle.apply()
    df = feats.merge(results[["host_id", "causes", "flagged"]], on="host_id")
    mem = df[df["causes"].str.contains("MEMORY_ECC|MEMORY_UE", regex=True)]
    healthy = df[~df["flagged"]]
    fig, ax = plt.subplots(figsize=(9, 4.6))
    days = list(range(1, 15))
    if len(healthy):
        hmax = pd.DataFrame(healthy["ce_daily"].tolist()).max(axis=0)
        ax.fill_between(days, 0, hmax, color=plotstyle.GRID, alpha=0.9,
                        label=f"max of {len(healthy)} unflagged hosts")
    for r in mem.itertuples(index=False):
        is_ue = "MEMORY_UE" in r.causes
        ax.plot(days, r.ce_daily, color=plotstyle.CRITICAL if is_ue else plotstyle.SERIES[1],
                marker="o", markersize=3, alpha=0.85, linewidth=1.6)
    ax.plot([], [], color=plotstyle.SERIES[1], label="flagged: MEMORY_ECC_DEGRADATION")
    ax.plot([], [], color=plotstyle.CRITICAL, label="flagged: MEMORY_UE (+ its CE burst)")
    ax.set_xlabel("day")
    ax.set_ylabel("correctable errors per day")
    ax.set_title("SIMULATED fleet: ECC correctable-error trends of flagged hosts", loc="left")
    ax.legend(loc="upper left")
    fig.savefig(path)
    plt.close(fig)


def write_fleet_outputs(results: pd.DataFrame, feats: pd.DataFrame, out_dir: Path,
                        th: Thresholds, label: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_results_csv(results, out_dir / "fleet_results.csv")
    (out_dir / "fleet_report.md").write_text(triage_markdown(results, th, label))
    plot_scatter(results, feats, out_dir / "fleet_scatter.png")
    plot_ce_trends(results, feats, out_dir / "fleet_ce_trends.png")
