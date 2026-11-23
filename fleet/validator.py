"""Fleet validator: rules + Isolation Forest + root-cause attribution.

Usage:
    # a fleet (reads ONLY <fleet-dir>/snapshots/*.json, never ground_truth.csv)
    python -m fleet.validator --fleet-dir data/fleet_2 [--thresholds reports/thresholds.json]
    # one real machine
    python -m fleet.validator --snapshot reports/snapshot_<host>.json

Pipeline for a fleet:
  1. extract_features: one row per host. Perf metrics use the MEDIAN of the
     probe's samples, so a single glitched sample can't move them.
  2. robust z-scores per SKU (median / MAD), signed so that "worse" is positive.
  3. Stage 1 rules (fleet/rules.py) -> findings with evidence.
  4. Stage 2 ML: an IsolationForest per SKU on the signed z-scores + a composite
     slowdown score -> anomaly score; flagged if >= threshold.
  5. attribute(): map findings + deviation pattern to root causes, a severity and
     a recommended action.

``mode`` selects stages for the ablation: "rules", "ml" or "combined".
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from fleet.rules import HIGHER_IS_WORSE, PERF_METRICS, Finding, Thresholds, ce_slope, run_rules

ML_FEATURES = PERF_METRICS + ["loaded_mhz"]
MIN_HOSTS_PER_SKU = 10  # below this a per-SKU baseline (median/MAD, IF) is meaningless
IF_SUBSAMPLE = 64       # Isolation Forest max_samples (see ml_scores)

SEVERITY_RANK = {"critical": 0, "warning": 1, "ok": 2}

ACTIONS = {
    "MEMORY_UE": "Drain host; replace the DIMM (EDAC per-DIMM counts identify the slot); "
                 "run memtester/memtest86 before returning to service.",
    "MEMORY_ECC_DEGRADATION": "Schedule DIMM replacement; run memtester; watch for UEs "
                              "until replaced.",
    "PCIE_LINK_DOWNTRAIN": "Reseat the card, inspect riser/cable and connector, retrain the "
                           "link (hot reset / reboot); RMA the card or riser if it persists.",
    "THERMAL_THROTTLING": "Check fans and fan curves, heatsink seating/paste, airflow and "
                          "blanking panels; compare with rack inlet temperature.",
    "MEMORY_CONFIG_SLOW": "Verify DIMM speed and population in BIOS/SPD "
                          "(dmidecode 'Configured Memory Speed' vs rated); fix mixed DIMMs.",
    "SUBTLE_PERF_DEGRADATION": "Diff BIOS/firmware/microcode against the SKU golden config; "
                               "run burn-in (stress-ng) and re-probe.",
    "UNKNOWN_PERF_ANOMALY": "Re-run probes to rule out a one-off; if it persists, escalate "
                            "for manual perf triage (perf counters, NUMA, power capping).",
}


# ---------------------------------------------------------------------------
# 1. Features
# ---------------------------------------------------------------------------

def _metric(probe: dict[str, Any], key: str) -> float | None:
    """Median of samples if the probe reported them, else the summary value."""
    samples = (probe.get("samples") or {}).get(key)
    if samples:
        return float(np.median(samples))
    v = probe.get(key)
    return float(v) if v is not None else None


def extract_features(snap: dict[str, Any]) -> dict[str, Any]:
    """Flatten one snapshot into the fields the rules and ML use."""
    probe = snap["probe"] if snap["probe"].get("available") else {}
    ecc = snap["ecc"] if snap["ecc"].get("available") else {}
    pcie = snap["pcie"] if snap["pcie"].get("available") else {}
    h: dict[str, Any] = {
        "host_id": snap["host_id"],
        "sku": snap.get("sku") or "unknown",
        "loaded_mhz": probe.get("loaded_cpu_mhz"),
        "loaded_temp_c": probe.get("loaded_max_temp_c"),
        "ce_daily": ecc.get("ce_daily"),
        "ce_total": ecc.get("ce_total"),
        "ue_total": ecc.get("ue_total"),
        "pcie_devices": pcie.get("devices", []),
    }
    for k in PERF_METRICS:
        h[k] = _metric(probe, k)
    return h


# ---------------------------------------------------------------------------
# 2. Robust z-scores
# ---------------------------------------------------------------------------

def robust_z(values: pd.Series) -> tuple[pd.Series, float, float]:
    """z = (x - median) / (1.4826 * MAD).

    1.4826 * MAD estimates the standard deviation for Gaussian data, but the
    median and MAD ignore outliers — the faulty hosts we're hunting don't drag
    the baseline toward themselves the way they would with mean/std.
    """
    med = float(values.median())
    mad = float((values - med).abs().median()) * 1.4826
    if not np.isfinite(mad) or mad == 0:
        mad = float(values.std()) or 1e-9
    return (values - med) / mad, med, mad


def add_fleet_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Per SKU: signed robust z for every metric (positive = worse) and freq ratio."""
    df = df.copy()
    for k in ML_FEATURES:
        df[f"z_{k}"] = np.nan
        df[f"med_{k}"] = np.nan
    df["freq_ratio"] = np.nan
    for _sku, idx in df.groupby("sku").groups.items():
        g = df.loc[idx]
        if len(g) < MIN_HOSTS_PER_SKU:
            continue
        for k in ML_FEATURES:
            col = g[k].astype(float)
            if col.isna().all():
                continue
            z, med, _ = robust_z(col)
            df.loc[idx, f"z_{k}"] = z if HIGHER_IS_WORSE[k] else -z
            df.loc[idx, f"med_{k}"] = med
        if g["loaded_mhz"].notna().any():
            df.loc[idx, "freq_ratio"] = g["loaded_mhz"] / g["loaded_mhz"].median()
    zcols = [f"z_{k}" for k in PERF_METRICS]
    # Composite slowdown: mean of the *positive* (worse-direction) perf z-scores.
    df["z_composite"] = df[zcols].clip(lower=0).mean(axis=1)
    return df


# ---------------------------------------------------------------------------
# 3 + 4. Isolation Forest
# ---------------------------------------------------------------------------

def ml_scores(df: pd.DataFrame, seed: int = 0) -> pd.Series:
    """Isolation Forest anomaly score per host (higher = more anomalous, ~0.5 = normal).

    Fitted separately per SKU, on that SKU's hosts — unsupervised, no labels. An
    Isolation Forest builds random trees that split on random features at random
    values; anomalies are isolated in fewer splits (shorter paths). Features are
    the signed z-scores clipped at 0 (being *faster* than your siblings is not a
    fault) plus the composite slowdown score, which is what exposes a small shift
    spread across several metrics at once.
    """
    scores = pd.Series(np.nan, index=df.index)
    feats = [f"z_{k}" for k in ML_FEATURES] + ["z_composite"]
    for _sku, idx in df.groupby("sku").groups.items():
        X = df.loc[idx, feats].astype(float)
        if len(X) < MIN_HOSTS_PER_SKU or X.isna().all().all():
            continue
        X = X.fillna(0.0).clip(lower=0.0)
        # Fixed subsample size: IF scores are normalised by the expected path length
        # for `max_samples` points, so using the same value for every SKU and fleet
        # size keeps a threshold tuned on one fleet meaningful on another.
        forest = IsolationForest(n_estimators=300, max_samples=min(IF_SUBSAMPLE, len(X)),
                                 random_state=seed)
        forest.fit(X.to_numpy())
        scores.loc[idx] = -forest.score_samples(X.to_numpy())
    return scores


# ---------------------------------------------------------------------------
# 5. Attribution
# ---------------------------------------------------------------------------

def _pos_z(h: dict[str, Any], k: str) -> float:
    v = h.get(f"z_{k}")
    return 0.0 if v is None or v != v else max(0.0, float(v))  # v != v catches NaN


def memory_path_slow(h: dict[str, Any], min_bw_z: float = 3.0, check_caches: bool = True) -> bool:
    """DRAM latency and bandwidth clearly worse while the caches are roughly normal.

    ``check_caches=False`` is used when throttling already explains slow caches.
    """
    z_dram, z_bw = _pos_z(h, "dram_ns"), _pos_z(h, "read_gbps")
    mem_score = (z_dram + z_bw) / 2
    cache_score = max(_pos_z(h, "l1_ns"), _pos_z(h, "l2_ns"), _pos_z(h, "l3_ns"))
    caches_ok = cache_score < mem_score / 2 or not check_caches
    return z_dram >= 2.0 and z_bw >= min_bw_z and mem_score >= 3.0 and caches_ok


def classify_perf_pattern(h: dict[str, Any]) -> str:
    """Name the cause of a perf-only anomaly from *which* metrics moved.

    * loaded clock clearly low and core-clocked caches (L1/L2) slower in ns
      -> THERMAL_THROTTLING (a clock drop; the thermal rule usually fires first).
    * DRAM latency AND bandwidth clearly worse, caches roughly normal
      -> MEMORY_CONFIG_SLOW (only the DRAM path is slow).
    * two or more metrics worse at once -> SUBTLE_PERF_DEGRADATION.
    * anything else -> UNKNOWN_PERF_ANOMALY.
    """
    if _pos_z(h, "loaded_mhz") >= 3.0 and max(_pos_z(h, "l1_ns"), _pos_z(h, "l2_ns")) >= 3.0:
        return "THERMAL_THROTTLING"
    if memory_path_slow(h):
        return "MEMORY_CONFIG_SLOW"
    if sum(_pos_z(h, k) >= 1.0 for k in PERF_METRICS) >= 2:
        return "SUBTLE_PERF_DEGRADATION"
    return "UNKNOWN_PERF_ANOMALY"


def top_deviations(h: dict[str, Any], n: int = 3) -> str:
    items = [(k, h.get(f"z_{k}")) for k in ML_FEATURES if h.get(f"z_{k}") is not None]
    items = sorted(items, key=lambda kv: -abs(kv[1]))[:n]
    return ", ".join(f"{k} z={v:+.1f}" for k, v in items)


def attribute(h: dict[str, Any], findings: list[Finding], ml_flag: bool, ml_score: float | None,
              th: Thresholds, mode: str) -> dict[str, Any]:
    """Turn findings + ML verdict into causes, severity, action and evidence."""
    use_rules, use_ml = mode in ("rules", "combined"), mode in ("ml", "combined")
    rule_f = findings if use_rules else []
    by_cause: dict[str, list[Finding]] = {}
    for f in rule_f:
        by_cause.setdefault(f.cause, []).append(f)

    causes: list[str] = []
    evidence: list[str] = []
    if "MEMORY_UE" in by_cause:
        causes.append("MEMORY_UE")
        # CE activity on a host with UEs is evidence for the UE, not a second cause.
        evidence += [f.evidence for f in by_cause.pop("MEMORY_UE")]
        evidence += [f.evidence for f in by_cause.pop("MEMORY_ECC_DEGRADATION", [])]
    for cause in ("MEMORY_ECC_DEGRADATION", "PCIE_LINK_DOWNTRAIN", "THERMAL_THROTTLING"):
        if cause in by_cause:
            causes.append(cause)
            evidence += [f.evidence for f in by_cause[cause]]

    perf_hit = "PERF_OUTLIER" in by_cause
    ml_hit = use_ml and ml_flag
    perf_evidence = [f.evidence for f in by_cause.get("PERF_OUTLIER", [])]
    if ml_hit:
        perf_evidence.append(f"IsolationForest score {ml_score:.3f} (>= {th.if_score}); "
                             f"top deviations: {top_deviations(h)}")
    if perf_hit or ml_hit:
        if "THERMAL_THROTTLING" not in causes:
            perf_cause = classify_perf_pattern(h)
            if perf_cause not in causes:
                causes.append(perf_cause)
            evidence += perf_evidence
        elif memory_path_slow(h, min_bw_z=6.0, check_caches=False):
            # Throttling explains slow caches and a small bandwidth dip (bw z <~ 3),
            # but not a large DRAM-path slowdown: that's a second, memory fault.
            causes.append("MEMORY_CONFIG_SLOW")
            evidence += perf_evidence

    flagged = bool(causes)
    severity = ("critical" if "MEMORY_UE" in causes else "warning") if flagged else "ok"
    ml_part = ml_score if (ml_hit and ml_score is not None) else 0.0
    magnitude = max([f.magnitude for f in rule_f] + [0.0]) + ml_part
    return {
        "host_id": h["host_id"], "sku": h["sku"], "flagged": flagged, "severity": severity,
        "primary_cause": causes[0] if causes else "",
        "causes": ";".join(causes),
        "action": " | ".join(ACTIONS[c] for c in causes),
        "evidence": " ; ".join(evidence),
        "rules_fired": ";".join(sorted({f.rule for f in rule_f})),
        "ml_flag": bool(ml_hit), "if_score": None if ml_score is None else round(ml_score, 4),
        "magnitude": round(magnitude, 3),
    }


# ---------------------------------------------------------------------------
# Fleet entry point
# ---------------------------------------------------------------------------

def load_snapshots(fleet_dir: Path) -> list[dict[str, Any]]:
    """Read every snapshot in <fleet_dir>/snapshots/. Nothing else in fleet_dir is read."""
    snap_dir = Path(fleet_dir) / "snapshots"
    return [json.loads(p.read_text()) for p in sorted(snap_dir.glob("*.json"))]


def featurize(snapshots: list[dict[str, Any]], seed: int = 0) -> pd.DataFrame:
    """Features + fleet statistics + ML scores (independent of thresholds; cache it)."""
    df = pd.DataFrame([extract_features(s) for s in snapshots])
    df = add_fleet_stats(df)
    df["if_score"] = ml_scores(df, seed)
    df["ce_slope"] = [ce_slope(d) if d else 0.0 for d in df["ce_daily"]]
    return df


def validate_features(df: pd.DataFrame, th: Thresholds, mode: str = "combined") -> pd.DataFrame:
    """Run rules + ML threshold + attribution on precomputed features."""
    rows = []
    for h in df.to_dict(orient="records"):
        score = h.get("if_score")
        score = None if score is None or (isinstance(score, float) and np.isnan(score)) else score
        ml_flag = score is not None and score >= th.if_score
        rows.append(attribute(h, run_rules(h, th), ml_flag, score, th, mode))
    out = pd.DataFrame(rows)
    out["_rank"] = out["severity"].map(SEVERITY_RANK)
    out = out.sort_values(["_rank", "magnitude"], ascending=[True, False]).drop(columns="_rank")
    return out.reset_index(drop=True)


def validate_fleet(snapshots: list[dict[str, Any]], th: Thresholds,
                   mode: str = "combined") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (results, features)."""
    feats = featurize(snapshots)
    return validate_features(feats, th, mode), feats


# ---------------------------------------------------------------------------
# Single real machine
# ---------------------------------------------------------------------------

def health_report(snap: dict[str, Any], baselines: dict[str, Any] | None,
                  th: Thresholds, ref_sku: str = "sku-a", tolerance: float = 0.15) -> str:
    """Markdown health report for one machine (no fleet to compare against).

    Deterministic rules run as usual on whatever the machine exposes. Perf is
    compared to a reference baseline (default: sku-a, which was measured on the
    dev machine) with a coarse ±15 % tolerance, because a single host has no
    fleet distribution to compute z-scores from.
    """
    h = extract_features(snap)
    lines = [f"# Health report — host {snap['host_id']}", "",
             f"Source: `{snap['source']}` snapshot collected {snap['collected_at']}. "
             f"Platform: {snap['platform'].get('os')} / kernel {snap['platform'].get('kernel')} "
             f"/ virtualization: {snap['platform'].get('virtualization')}.", "",
             "| Check | Status | Detail |", "|---|---|---|"]

    def row(check: str, status: str, detail: str) -> None:
        lines.append(f"| {check} | {status} | {detail} |")

    for name, rule_fn, needs in [
        ("ECC uncorrectable errors", "ue", "ecc"), ("ECC correctable-error trend", "ce", "ecc"),
        ("PCIe link training", "pcie", "pcie"), ("Thermal throttling", "thermal", "thermal")]:
        sec = snap[needs]
        if not sec.get("available"):
            row(name, "not evaluated", f"{needs} unavailable: {sec.get('reason')}")
            continue
        if rule_fn == "ue":
            ue = h["ue_total"] or 0
            row(name, "FAIL" if ue else "pass", f"UE count = {ue}")
        elif rule_fn == "ce":
            if h["ce_daily"]:
                s = ce_slope(h["ce_daily"])
                bad = s > th.ce_slope_per_day or sum(h["ce_daily"]) > th.ce_total
                row(name, "FAIL" if bad else "pass", f"slope {s:+.2f}/day")
            else:
                row(name, "not evaluated",
                    f"no daily history (single reading: CE total = {h['ce_total']})")
        elif rule_fn == "pcie":
            down = [d for d in h["pcie_devices"] if d.get("downtrained")]
            row(name, "FAIL" if down else "pass",
                f"{len(h['pcie_devices'])} devices, {len(down)} downtrained")
        else:
            if h["loaded_temp_c"] is None:
                row(name, "not evaluated", "no loaded temperature (sensors unavailable)")
            else:
                row(name, "pass" if h["loaded_temp_c"] <= th.temp_c else "FAIL",
                    f"loaded temp {h['loaded_temp_c']} °C")

    if not snap["probe"].get("available"):
        row("Memory performance", "not evaluated", snap["probe"].get("reason", ""))
    elif not baselines or ref_sku not in baselines:
        row("Memory performance", "not evaluated", "no reference baseline")
    else:
        base = baselines[ref_sku]
        for k in PERF_METRICS:
            v, ref = h.get(k), base.get(k)
            if v is None or ref is None:
                row(k, "not evaluated", "not measured")
                continue
            rel = v / ref - 1.0
            worse = rel if HIGHER_IS_WORSE[k] else -rel
            status = "WARN" if worse > tolerance else "pass"
            row(k, status, f"{v:.2f} vs reference {ref:.2f} ({rel:+.0%}; tolerance ±"
                           f"{tolerance:.0%})")
    lines += ["", f"Reference baseline: `{ref_sku}` from reports/sku_baselines.json "
              "(sku-a perf = this project's full canonical probe run). The collector runs a "
              "*quick* probe (fewer sizes and repeats), so deviations within a few percent "
              "are measurement noise.", ""]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate a fleet or a single snapshot.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--fleet-dir", type=Path)
    src.add_argument("--snapshot", type=Path)
    ap.add_argument("--thresholds", type=Path, default=Path("reports/thresholds.json"))
    ap.add_argument("--baselines", type=Path, default=Path("reports/sku_baselines.json"))
    ap.add_argument("--mode", choices=["rules", "ml", "combined"], default="combined")
    ap.add_argument("--out-dir", type=Path, default=Path("reports"))
    args = ap.parse_args(argv)
    th = Thresholds.load(args.thresholds)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    if args.snapshot:
        snap = json.loads(args.snapshot.read_text())
        baselines = json.loads(args.baselines.read_text()) if args.baselines.exists() else None
        out = args.out_dir / f"health_{snap['host_id']}.md"
        out.write_text(health_report(snap, baselines, th))
        print(f"wrote {out}")
        return 0

    from fleet.report import write_fleet_outputs

    snapshots = load_snapshots(args.fleet_dir)
    results, feats = validate_fleet(snapshots, th, args.mode)
    write_fleet_outputs(results, feats, args.out_dir, th, label=str(args.fleet_dir))
    n_flag = int(results["flagged"].sum())
    print(f"validated {len(results)} hosts from {args.fleet_dir}: {n_flag} flagged "
          f"({int((results['severity'] == 'critical').sum())} critical) -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
