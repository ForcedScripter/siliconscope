"""Validator tests: rules, robust statistics, attribution, isolation from ground truth."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from collector import collector
from fleet import evaluate, rules
from fleet.rules import Thresholds
from fleet.simulator import healthy_host, probe_samples, render_snapshot, simulate, sku_baselines
from fleet.validator import (
    extract_features,
    health_report,
    load_snapshots,
    robust_z,
    validate_features,
    validate_fleet,
)

ROOT = Path(__file__).resolve().parents[1]
TH = Thresholds()


@pytest.fixture(scope="module")
def baselines() -> dict:
    return sku_baselines(ROOT / "reports")


# --- isolation from ground truth ----------------------------------------------------

def test_validator_code_never_references_ground_truth() -> None:
    """No identifier or string in the validator's code (docstrings aside) names ground truth."""
    for name in ("validator.py", "rules.py", "report.py"):
        tree = ast.parse((ROOT / "fleet" / name).read_text())
        docstrings = {ast.get_docstring(n, clean=False) for n in ast.walk(tree)
                      if isinstance(n, ast.Module | ast.FunctionDef | ast.ClassDef)}
        for node in ast.walk(tree):
            text = (node.value if isinstance(node, ast.Constant) and isinstance(node.value, str)
                    else getattr(node, "id", None) or getattr(node, "attr", None) or "")
            if text in docstrings:
                continue
            assert "ground_truth" not in text, f"{name}: {text!r}"


def test_validator_works_without_ground_truth_file(tmp_path: Path, baselines: dict) -> None:
    snaps, _ = simulate(40, 0.2, 21, baselines)
    (tmp_path / "snapshots").mkdir()
    for s in snaps:
        (tmp_path / "snapshots" / f"{s['host_id']}.json").write_text(json.dumps(s))
    assert not (tmp_path / "ground_truth.csv").exists()
    results, _ = validate_fleet(load_snapshots(tmp_path), TH)
    assert len(results) == 40


# --- robust statistics ----------------------------------------------------------------

def test_robust_z_ignores_outliers() -> None:
    clean = pd.Series([100.0, 101, 99, 100.5, 99.5, 100, 100.2, 99.8])
    z_clean, med, _ = robust_z(clean)
    dirty = pd.concat([clean, pd.Series([500.0])], ignore_index=True)
    z_dirty, med2, _ = robust_z(dirty)
    assert med == pytest.approx(med2, abs=0.3)  # one huge outlier barely moves the median
    assert z_dirty.iloc[-1] > 50                   # ...and it stands out enormously
    assert abs(z_clean).max() < 3


# --- rules ---------------------------------------------------------------------------------

def _h(**kw) -> dict:  # type: ignore[no-untyped-def]
    base = {"host_id": "h", "sku": "sku-a", "ue_total": 0, "ce_daily": [0] * 14,
            "pcie_devices": [], "loaded_temp_c": 70.0, "freq_ratio": 1.0, "loaded_mhz": 3800.0}
    return {**base, **kw}


def test_rule_ue_is_critical() -> None:
    (f,) = rules.rule_ue(_h(ue_total=2), TH)
    assert f.cause == "MEMORY_UE" and f.severity == "critical"


def test_rule_ce_trend_vs_isolated_spike() -> None:
    rising = [0, 0, 1, 1, 2, 2, 3, 4, 4, 5, 6, 6, 7, 8]
    assert rules.rule_ce_trend(_h(ce_daily=rising), TH)
    spike = [0] * 13 + [2]
    assert not rules.rule_ce_trend(_h(ce_daily=spike), TH)


def test_rule_pcie_flags_only_downtrained_nic_or_nvme() -> None:
    nic = {"bdf": "x", "role": "nic", "current_width": 8, "max_width": 16,
           "current_speed_gts": 16.0, "max_speed_gts": 16.0, "downtrained": True}
    gpu = {**nic, "role": "gpu"}
    assert len(rules.rule_pcie_downtrain(_h(pcie_devices=[nic, gpu]), TH)) == 1


def test_rule_thermal_needs_heat_and_slowdown() -> None:
    assert rules.rule_thermal(_h(loaded_temp_c=95.0, freq_ratio=0.78), TH)
    assert not rules.rule_thermal(_h(loaded_temp_c=95.0, freq_ratio=1.0), TH)  # hot, full speed
    assert not rules.rule_thermal(_h(loaded_temp_c=70.0, freq_ratio=0.78), TH)  # slow, cool


def test_thresholds_roundtrip(tmp_path: Path) -> None:
    th = Thresholds(ce_total=7, if_score=0.61)
    th.save(tmp_path / "t.json", {"note": "x"})
    assert Thresholds.load(tmp_path / "t.json") == th
    assert Thresholds.load(tmp_path / "missing.json") == Thresholds()


# --- end to end ----------------------------------------------------------------------------

def test_glitched_sample_does_not_flag_healthy_host(baselines: dict) -> None:
    """A healthy fleet where one host has a 1.8x glitch on one DRAM sample."""
    rng = np.random.default_rng(0)
    snaps = []
    for i in range(60):
        m = healthy_host(baselines["sku-a"], rng)
        samples = probe_samples(m, rng, glitch=False)
        if i == 0:
            samples["dram_ns"][1] *= 1.8
        snaps.append(render_snapshot(f"h{i:03d}", "sku-a", baselines["sku-a"], m, samples,
                                     pd.Timestamp("2026-11-01", tz="UTC").to_pydatetime()))
    assert extract_features(snaps[0])["dram_ns"] < 1.1 * baselines["sku-a"]["dram_ns"]
    results, _ = validate_fleet(snaps, Thresholds(if_score=0.65), "rules")
    assert not results.set_index("host_id").loc["h000", "flagged"]


def test_end_to_end_detection_and_ablation(baselines: dict) -> None:
    """On a dev fleet: decent detection, and ML is what catches SUBTLE_PERF_DEGRADATION."""
    snaps, truth_rows = simulate(800, 0.15, 4242, baselines)
    truth = {r["host_id"]: set(filter(None, r["fault_types"].split(";"))) for r in truth_rows}
    _, feats = validate_fleet(snaps, TH)
    th = Thresholds(if_score=0.57)
    s = {m: evaluate.score(validate_features(feats, th, m), truth) for m in evaluate.MODES}
    assert s["combined"]["detection"]["precision"] > 0.8
    assert s["combined"]["detection"]["recall"] > 0.85
    subtle = {m: s[m]["per_type"]["SUBTLE_PERF_DEGRADATION"]["detect_recall"] for m in s}
    assert subtle["ml"] > subtle["rules"]
    # Rules alone catch the faults with no perf signature; ML alone can't.
    for ft in ("MEMORY_UE", "PCIE_LINK_DOWNTRAIN"):
        assert s["rules"]["per_type"][ft]["detect_recall"] == 1.0
        assert s["ml"]["per_type"][ft]["cause"]["recall"] == 0.0


def test_evaluate_score_counts() -> None:
    results = pd.DataFrame([
        {"host_id": "a", "flagged": True, "causes": "MEMORY_UE", "primary_cause": "MEMORY_UE"},
        {"host_id": "b", "flagged": True, "causes": "MEMORY_CONFIG_SLOW",
         "primary_cause": "MEMORY_CONFIG_SLOW"},
        {"host_id": "c", "flagged": False, "causes": "", "primary_cause": ""},
        {"host_id": "d", "flagged": False, "causes": "", "primary_cause": ""},
    ])
    truth = {"a": {"MEMORY_UE"}, "b": set(), "c": {"PCIE_LINK_DOWNTRAIN"}, "d": set()}
    s = evaluate.score(results, truth)
    assert s["detection"] == {"precision": 0.5, "recall": 0.5, "f1": 0.5, "tp": 1, "fp": 1,
                              "fn": 1}
    assert s["root_cause"]["exact_set_accuracy"] == 1.0
    assert s["confusion"].loc["PCIE_LINK_DOWNTRAIN", "NOT_FLAGGED"] == 1
    assert s["confusion"].loc["NONE", "MEMORY_CONFIG_SLOW"] == 1


# --- single machine ------------------------------------------------------------------------

def test_health_report_on_collector_snapshot(baselines: dict) -> None:
    snap = collector.collect(ROOT / "tests" / "fixtures" / "fake_sysfs" / "degraded")
    md = health_report(snap, baselines, TH)
    assert "| ECC uncorrectable errors | FAIL | UE count = 1 |" in md
    assert "PCIe link training | FAIL" in md
    assert "Memory performance | not evaluated" in md  # no probe in this snapshot
