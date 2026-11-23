"""Stage 1 of the validator: deterministic, explainable rules.

Every rule looks at one host's extracted features (see
``validator.extract_features``) and returns zero or more :class:`Finding`s.
Each finding names the rule, the root cause it points to, a severity, and an
evidence string with the actual values — so an on-call engineer can check the
verdict by hand.

Thresholds live in :class:`Thresholds`. Defaults are sensible starting points;
``fleet/evaluate.py --tune`` re-picks them on seed 1 only and saves them to
``reports/thresholds.json``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

import numpy as np

# Perf metrics and which direction is "bad". Latency: higher is worse.
# Bandwidth and loaded frequency: lower is worse.
PERF_METRICS = ["l1_ns", "l2_ns", "l3_ns", "dram_ns", "read_gbps"]
HIGHER_IS_WORSE = {"l1_ns": True, "l2_ns": True, "l3_ns": True, "dram_ns": True,
                   "read_gbps": False, "loaded_mhz": False}
UNITS = {"l1_ns": "ns", "l2_ns": "ns", "l3_ns": "ns", "dram_ns": "ns", "read_gbps": "GB/s",
         "loaded_mhz": "MHz"}


@dataclass
class Thresholds:
    """All tunable knobs of the validator in one place."""

    ce_slope_per_day: float = 0.5   # CE/day growth (least-squares slope over 14 days)
    ce_total: int = 20              # CE count over 14 days
    temp_c: float = 88.0            # loaded package temperature
    freq_ratio: float = 0.90        # loaded MHz / SKU median loaded MHz
    z_abs: float = 4.0              # per-metric robust z-score (fixed by spec)
    if_score: float = 0.60          # Isolation Forest anomaly score (0.5 ~ normal)

    @classmethod
    def load(cls, path: Path | None) -> Thresholds:
        if path is None or not Path(path).exists():
            return cls()
        data = json.loads(Path(path).read_text())
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path, extra: dict[str, Any] | None = None) -> None:
        Path(path).write_text(json.dumps({**asdict(self), **(extra or {})}, indent=2) + "\n")


@dataclass
class Finding:
    rule: str
    cause: str          # one of the 6 fault labels, or "PERF_OUTLIER" (attributed later)
    severity: str       # "critical" | "warning"
    evidence: str
    magnitude: float    # for ranking: bigger = worse


# ---------------------------------------------------------------------------
# Individual rules
# ---------------------------------------------------------------------------

def rule_pcie_downtrain(h: dict[str, Any], th: Thresholds) -> list[Finding]:
    """PCIe link trained below capability (current speed or width < max)."""
    out = []
    for d in h.get("pcie_devices", []):
        if d.get("role") not in ("nic", "nvme"):
            continue  # GPUs etc. may legitimately down-clock at idle
        if not d.get("downtrained"):
            continue
        out.append(Finding(
            "pcie_downtrain", "PCIE_LINK_DOWNTRAIN", "warning",
            f"{d['role']} {d['bdf']}: x{d['current_width']} @ {d['current_speed_gts']} GT/s "
            f"(capable of x{d['max_width']} @ {d['max_speed_gts']} GT/s)",
            magnitude=1.0))
    return out


def rule_ue(h: dict[str, Any], th: Thresholds) -> list[Finding]:
    """Any uncorrectable ECC error is critical."""
    ue = h.get("ue_total") or 0
    if ue > 0:
        return [Finding("ecc_ue", "MEMORY_UE", "critical", f"UE count = {ue}",
                        magnitude=10.0 + ue)]
    return []


def ce_slope(ce_daily: list[int]) -> float:
    """Least-squares slope of daily CE counts (CE/day per day)."""
    if not ce_daily or len(ce_daily) < 2:
        return 0.0
    days = np.arange(len(ce_daily))
    return float(np.polyfit(days, np.asarray(ce_daily, dtype=float), 1)[0])


def rule_ce_trend(h: dict[str, Any], th: Thresholds) -> list[Finding]:
    """Correctable errors trending up, or too many in total."""
    daily = h.get("ce_daily")
    if not daily:
        return []
    slope, total = ce_slope(daily), int(sum(daily))
    if slope > th.ce_slope_per_day or total > th.ce_total:
        return [Finding(
            "ecc_ce_trend", "MEMORY_ECC_DEGRADATION", "warning",
            f"CE slope = {slope:+.2f}/day (>{th.ce_slope_per_day}), 14-day total = {total} "
            f"(>{th.ce_total}?), last 3 days = {daily[-3:]}",
            magnitude=max(slope, total / 50))]
    return []


def rule_thermal(h: dict[str, Any], th: Thresholds) -> list[Finding]:
    """Hot AND slow: loaded temperature high together with a frequency drop."""
    temp, ratio = h.get("loaded_temp_c"), h.get("freq_ratio")
    if temp is None or ratio is None:
        return []
    if temp > th.temp_c and ratio < th.freq_ratio:
        return [Finding(
            "thermal_throttle", "THERMAL_THROTTLING", "warning",
            f"loaded temp = {temp:.1f} °C (>{th.temp_c}), loaded freq = "
            f"{h['loaded_mhz']:.0f} MHz = {ratio:.2f}x SKU median (<{th.freq_ratio})",
            magnitude=(1 - ratio) * 10)]
    return []


def rule_perf_zscore(h: dict[str, Any], th: Thresholds) -> list[Finding]:
    """Per-SKU robust z-score: |z| > z_abs on any perf metric.

    The finding's cause is a placeholder; the validator attributes it from the
    *pattern* of deviations (which metrics, which direction).
    """
    out = []
    for k in PERF_METRICS:
        z = h.get(f"z_{k}")
        if z is not None and abs(z) > th.z_abs:
            out.append(Finding(
                "perf_robust_z", "PERF_OUTLIER", "warning",
                f"{k} = {h[k]:.2f} {UNITS[k]} vs SKU median {h[f'med_{k}']:.2f} "
                f"(robust z = {z:+.1f})",
                magnitude=abs(z)))
    return out


ALL_RULES = [rule_ue, rule_pcie_downtrain, rule_ce_trend, rule_thermal, rule_perf_zscore]


def run_rules(h: dict[str, Any], th: Thresholds) -> list[Finding]:
    findings: list[Finding] = []
    for rule in ALL_RULES:
        findings.extend(rule(h, th))
    return findings
