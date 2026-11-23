"""Score the validator against ground truth: tune on seed 1, report on held-out seeds.

Usage:
    python -m fleet.evaluate --tune   # tune on data/fleet_1 -> reports/thresholds.json
    python -m fleet.evaluate          # score held-out data/fleet_2 (+ robustness seeds)

Protocol (written into reports/evaluation.md):
  * seed 1  = TUNING fleet. Thresholds are grid-searched here and nowhere else.
  * seed 2  = HELD-OUT test fleet. Headline numbers. Never used for any decision.
  * seeds 3–11 = extra held-out fleets, to show how much the headline varies by seed.
  * dev seeds (101+) were used while writing the code; they are never reported.

This module is the only place that reads ground_truth.csv.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from fleet.faults import FAULT_TYPES
from fleet.rules import Thresholds
from fleet.validator import featurize, load_snapshots, validate_features

MODES = ["rules", "ml", "combined"]
PRED_COLUMNS = FAULT_TYPES + ["UNKNOWN_PERF_ANOMALY", "NOT_FLAGGED"]


# ---------------------------------------------------------------------------
# Ground truth + scoring
# ---------------------------------------------------------------------------

def load_truth(fleet_dir: Path) -> dict[str, set[str]]:
    with (Path(fleet_dir) / "ground_truth.csv").open() as f:
        return {r["host_id"]: set(filter(None, r["fault_types"].split(";")))
                for r in csv.DictReader(f)}


def prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    """Precision = of what we flagged, how much was really bad.
    Recall = of what was really bad, how much we flagged. F1 = their harmonic mean."""
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f, 3),
            "tp": tp, "fp": fp, "fn": fn}


def score(results: pd.DataFrame, truth: dict[str, set[str]]) -> dict[str, Any]:
    """Detection P/R/F1, per-type recall, cause P/R, root-cause accuracy, confusion counts."""
    pred = {r.host_id: (bool(r.flagged), set(filter(None, r.causes.split(";"))), r.primary_cause)
            for r in results.itertuples(index=False)}
    tp = sum(1 for h, (f, _, _) in pred.items() if f and truth[h])
    fp = sum(1 for h, (f, _, _) in pred.items() if f and not truth[h])
    fn = sum(1 for h, (f, _, _) in pred.items() if not f and truth[h])
    out: dict[str, Any] = {"detection": prf(tp, fp, fn), "per_type": {}}

    for ft in FAULT_TYPES:
        hosts = [h for h in truth if ft in truth[h]]
        detected = sum(1 for h in hosts if pred[h][0])
        c_tp = sum(1 for h in hosts if ft in pred[h][1])
        c_fp = sum(1 for h in pred if ft in pred[h][1] and ft not in truth[h])
        out["per_type"][ft] = {"n": len(hosts),
                               "detect_recall": round(detected / len(hosts), 3) if hosts else None,
                               "cause": prf(c_tp, c_fp, len(hosts) - c_tp)}

    tps = [h for h in truth if truth[h] and pred[h][0]]
    exact = sum(1 for h in tps if pred[h][1] == truth[h])
    primary = sum(1 for h in tps if pred[h][2] in truth[h])
    out["root_cause"] = {"tp_hosts": len(tps),
                         "exact_set_accuracy": round(exact / len(tps), 3) if tps else None,
                         "primary_accuracy": round(primary / len(tps), 3) if tps else None}

    # Confusion: one entry per (host, true label). For a true fault, the predicted
    # column is that fault if it was among the predicted causes, else the host's
    # primary predicted cause, else NOT_FLAGGED.
    conf = pd.DataFrame(0, index=FAULT_TYPES + ["NONE"], columns=PRED_COLUMNS)
    for h, labels in truth.items():
        flagged, causes, primary = pred[h]
        for lab in (labels or {"NONE"}):
            col = lab if lab in causes else (primary if flagged else "NOT_FLAGGED")
            conf.loc[lab, col] += 1
    out["confusion"] = conf
    return out


# ---------------------------------------------------------------------------
# Tuning (seed 1 only)
# ---------------------------------------------------------------------------

def _f1(pred: np.ndarray, actual: np.ndarray) -> float:
    tp = int((pred & actual).sum())
    fp = int((pred & ~actual).sum())
    fn = int((~pred & actual).sum())
    return prf(tp, fp, fn)["f1"]


def _central_best(grid: list[tuple], scores: list[float]) -> tuple[tuple, float]:
    """Best score; among ties pick the combination nearest the centre of the tied set.

    With only a handful of faulty hosts per type, many thresholds tie on seed 1.
    Picking the middle of the tied region (not its edge) leaves margin on both
    sides — a cheap guard against overfitting the tuning fleet.
    """
    best = max(scores)
    tied = np.array([g for g, s in zip(grid, scores, strict=True) if s == best], dtype=float)
    centre = tied.mean(axis=0)
    span = np.ptp(np.array(grid, dtype=float), axis=0)
    span[span == 0] = 1.0
    pick = tied[np.argmin((((tied - centre) / span) ** 2).sum(axis=1))]
    return tuple(pick.tolist()), best


def tune(fleet_dir: Path) -> tuple[Thresholds, dict[str, Any]]:
    """Grid-search each threshold group on the tuning fleet."""
    truth = load_truth(fleet_dir)
    feats = featurize(load_snapshots(fleet_dir))
    labels = [truth[h] for h in feats["host_id"]]
    has = {ft: np.array([ft in lab for lab in labels]) for ft in FAULT_TYPES}
    log: dict[str, Any] = {}
    th = Thresholds()

    # 1) CE trend rule -> MEMORY_ECC_DEGRADATION (UE hosts are attributed to UE instead).
    no_ue = (feats["ue_total"].fillna(0) == 0).to_numpy()
    slopes, totals = feats["ce_slope"].to_numpy(), feats["ce_total"].fillna(0).to_numpy()
    grid = list(itertools.product([0.2, 0.3, 0.4, 0.5, 0.75, 1.0], [10, 15, 20, 30, 40]))
    scores = [_f1(((slopes > s) | (totals > t)) & no_ue, has["MEMORY_ECC_DEGRADATION"])
              for s, t in grid]
    (th.ce_slope_per_day, ce_total), log["ce_f1"] = _central_best(grid, scores)
    th.ce_total = int(ce_total)

    # 2) Thermal rule -> THERMAL_THROTTLING.
    temp, ratio = feats["loaded_temp_c"].to_numpy(), feats["freq_ratio"].to_numpy()
    grid = list(itertools.product([84.0, 86.0, 88.0, 90.0], [0.85, 0.88, 0.90, 0.93]))
    scores = [_f1((temp > t) & (ratio < r), has["THERMAL_THROTTLING"]) for t, r in grid]
    (th.temp_c, th.freq_ratio), log["thermal_f1"] = _central_best(grid, scores)

    # 3) Isolation Forest threshold -> overall detection F1 of the combined system.
    grid_if = [round(x, 3) for x in np.arange(0.52, 0.72, 0.01)]
    scores = []
    for t in grid_if:
        th.if_score = t
        res = validate_features(feats, th, "combined")
        scores.append(score(res, truth)["detection"]["f1"])
    (th.if_score,), log["combined_f1"] = _central_best([(t,) for t in grid_if], scores)
    log["if_grid"] = dict(zip(grid_if, scores, strict=True))
    return th, log


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def plot_confusion(conf: pd.DataFrame, path: Path, title: str) -> None:
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap

    from scripts import plotstyle

    plotstyle.apply()
    # Sequential single-hue ramp (light -> dark blue) for counts.
    cmap = LinearSegmentedColormap.from_list("seq", ["#f4f8fd", "#86b6ef", "#2a78d6", "#104281"])
    # Row-normalise so each true label's row shows where its hosts went.
    norm = conf.div(conf.sum(axis=1).replace(0, 1), axis=0)
    fig, ax = plt.subplots(figsize=(10.5, 5.8))
    ax.imshow(norm.to_numpy(), cmap=cmap, vmin=0, vmax=1, aspect="auto")
    ax.grid(False)
    short = {c: c.replace("MEMORY_", "MEM_").replace("_DEGRADATION", "_DEGR")
             .replace("PERF_ANOMALY", "PERF").replace("THROTTLING", "THROTTLE") for c in
             list(conf.columns) + list(conf.index)}
    ax.set_xticks(range(len(conf.columns)))
    ax.set_xticklabels([short[c] for c in conf.columns], rotation=35, ha="right", fontsize=8)
    ax.set_yticks(range(len(conf.index)))
    ax.set_yticklabels([short[c] for c in conf.index], fontsize=8)
    for i in range(conf.shape[0]):
        for j in range(conf.shape[1]):
            v = int(conf.iat[i, j])
            if v:
                ax.text(j, i, str(v), ha="center", va="center", fontsize=9,
                        color="white" if norm.iat[i, j] > 0.55 else plotstyle.TEXT)
    ax.set_xlabel("predicted cause")
    ax.set_ylabel("true fault (one entry per host x fault)")
    ax.set_title(title, loc="left")
    fig.savefig(path)
    plt.close(fig)


def _det_row(name: str, d: dict[str, float]) -> str:
    return (f"| {name} | {d['precision']:.3f} | {d['recall']:.3f} | {d['f1']:.3f} | "
            f"{d['tp']} | {d['fp']} | {d['fn']} |")


def evaluate(tune_dir: Path, test_dir: Path, robust_dirs: list[Path], th_path: Path,
             out_dir: Path) -> dict[str, Any]:
    th = Thresholds.load(th_path)
    truth = load_truth(test_dir)
    feats = featurize(load_snapshots(test_dir))
    by_mode = {m: score(validate_features(feats, th, m), truth) for m in MODES}
    comb = by_mode["combined"]
    plot_confusion(comb["confusion"], out_dir / "confusion_matrix.png",
                   f"Held-out fleet ({test_dir.name}, SIMULATED): true fault vs predicted cause")

    robust = []
    for d in robust_dirs:
        t = load_truth(d)
        f = featurize(load_snapshots(d))
        robust.append({m: score(validate_features(f, th, m), t) for m in MODES})

    tuning_meta = json.loads(th_path.read_text()) if th_path.exists() else {}
    md = ["# Evaluation (SIMULATED fleet)", "",
          "> Every number below comes from SIMULATED fleets generated by `fleet/simulator.py`. "
          "Nothing here is a measurement of real servers.", "",
          "## Protocol", "",
          f"- **Tuning fleet:** `{tune_dir}` (seed 1). Thresholds were grid-searched **only** "
          "here (`python -m fleet.evaluate --tune`) and frozen in `reports/thresholds.json`.",
          f"- **Held-out test fleet:** `{test_dir}` (seed 2). Headline numbers. Not used for "
          "any design or tuning decision.",
          f"- **Robustness:** {len(robust_dirs)} more held-out fleets (seeds 3–"
          f"{2 + len(robust_dirs)}) show how much the headline moves with the random draw.",
          "- Development used separate dev seeds (101+), never reported.",
          "- The validator reads only `snapshots/`; only this script reads `ground_truth.csv`.",
          "",
          "Frozen thresholds: " + ", ".join(f"`{k}={v}`" for k, v in th.__dict__.items()),
          f"(tuning F1 on seed 1: CE rule {tuning_meta.get('ce_f1')}, thermal rule "
          f"{tuning_meta.get('thermal_f1')}, combined detection {tuning_meta.get('combined_f1')})",
          "",
          f"## Held-out results ({test_dir.name}: {len(truth)} hosts, "
          f"{sum(1 for v in truth.values() if v)} faulty)", "",
          "### Detection (is the host faulty?)", "",
          "| Stages | Precision | Recall | F1 | TP | FP | FN |",
          "|---|---:|---:|---:|---:|---:|---:|",
          *[_det_row(m, by_mode[m]["detection"]) for m in MODES], "",
          "### Root cause (combined system)", "",
          f"- True-positive hosts: {comb['root_cause']['tp_hosts']}",
          f"- Exact cause-set accuracy: **{comb['root_cause']['exact_set_accuracy']}**",
          f"- Primary cause correct: **{comb['root_cause']['primary_accuracy']}**", "",
          "### Per fault type", "",
          "| Fault type | Hosts | Detect recall: rules | ML | combined | Cause precision | "
          "Cause recall |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for ft in FAULT_TYPES:
        r = {m: by_mode[m]["per_type"][ft] for m in MODES}
        md.append(f"| {ft} | {r['combined']['n']} | {r['rules']['detect_recall']} | "
                  f"{r['ml']['detect_recall']} | {r['combined']['detect_recall']} | "
                  f"{r['combined']['cause']['precision']} | {r['combined']['cause']['recall']} |")
    md += ["", "### Confusion matrix (combined)", "",
           "![confusion matrix](confusion_matrix.png)", "",
           "Rows: true fault (multi-fault hosts contribute one entry per fault; healthy hosts are "
           "`NONE`). Columns: the predicted cause matching that fault if present, else the host's "
           "primary predicted cause, else `NOT_FLAGGED`.", "",
           "| true \\ predicted | " + " | ".join(PRED_COLUMNS) + " |",
           "|---|" + "---:|" * len(PRED_COLUMNS)]
    for lab, row in comb["confusion"].iterrows():
        md.append(f"| {lab} | " + " | ".join(str(int(v)) for v in row) + " |")

    md += ["", f"## Robustness across {len(robust)} more held-out seeds", "",
           "| Stages | Precision (mean ± sd) | Recall | F1 |", "|---|---|---|---|"]
    for m in MODES:
        vals = {k: [r[m]["detection"][k] for r in robust] for k in ("precision", "recall", "f1")}
        md.append(f"| {m} | " + " | ".join(
            f"{np.mean(v):.3f} ± {np.std(v):.3f}" for v in vals.values()) + " |")
    md += ["", "Pooled per-type detection recall over these seeds (combined | rules | ML):", "",
           "| Fault type | Hosts | combined | rules only | ML only |", "|---|---:|---:|---:|---:|"]
    for ft in FAULT_TYPES:
        n = sum(r["combined"]["per_type"][ft]["n"] for r in robust)
        cells = []
        for m in MODES:
            det = sum(round((r[m]["per_type"][ft]["detect_recall"] or 0)
                            * r[m]["per_type"][ft]["n"]) for r in robust)
            cells.append(f"{det / n:.3f}" if n else "-")
        md.append(f"| {ft} | {n} | {cells[2]} | {cells[0]} | {cells[1]} |")
    rc = [r["combined"]["root_cause"]["exact_set_accuracy"] for r in robust]
    md += ["", f"Exact root-cause accuracy on TPs (combined): {np.mean(rc):.3f} ± {np.std(rc):.3f}",
           ""]
    (out_dir / "evaluation.md").write_text("\n".join(md) + "\n")

    summary = {"test_seed_dir": str(test_dir), "thresholds": th.__dict__,
               **{f"{m}_detection": by_mode[m]["detection"] for m in MODES},
               "root_cause": comb["root_cause"],
               "per_type": {ft: comb["per_type"][ft] for ft in FAULT_TYPES},
               "robust_f1_mean": {m: round(float(np.mean([r[m]["detection"]["f1"]
                                                          for r in robust])), 3)
                                  for m in MODES} if robust else {}}
    (out_dir / "evaluation_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Tune on seed 1 / evaluate on held-out seeds.")
    ap.add_argument("--tune", action="store_true", help="tune thresholds on the tuning fleet")
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--tune-seed", type=int, default=1)
    ap.add_argument("--test-seed", type=int, default=2)
    ap.add_argument("--robust-seeds", type=int, nargs="*", default=list(range(3, 12)))
    ap.add_argument("--thresholds", type=Path, default=Path("reports/thresholds.json"))
    ap.add_argument("--out-dir", type=Path, default=Path("reports"))
    args = ap.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    tune_dir = args.data / f"fleet_{args.tune_seed}"

    if args.tune:
        th, log = tune(tune_dir)
        th.save(args.thresholds, {"tuned_on": str(tune_dir), "ce_f1": log["ce_f1"],
                                  "thermal_f1": log["thermal_f1"],
                                  "combined_f1": log["combined_f1"],
                                  "if_grid_combined_f1": {str(k): v for k, v in
                                                          log["if_grid"].items()}})
        print(f"tuned on {tune_dir}: {th} (CE F1 {log['ce_f1']}, thermal F1 "
              f"{log['thermal_f1']}, combined F1 {log['combined_f1']}) -> {args.thresholds}")
        return

    s = evaluate(tune_dir, args.data / f"fleet_{args.test_seed}",
                 [args.data / f"fleet_{s}" for s in args.robust_seeds], args.thresholds,
                 args.out_dir)
    d = s["combined_detection"]
    print(f"held-out seed {args.test_seed} (combined): precision {d['precision']} recall "
          f"{d['recall']} F1 {d['f1']}; root-cause exact {s['root_cause']['exact_set_accuracy']}"
          f" -> {args.out_dir / 'evaluation.md'}")


if __name__ == "__main__":
    main()
