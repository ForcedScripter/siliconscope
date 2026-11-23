"""Simulator tests: reproducibility, schema parity with the collector, fault signatures."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from collector.schema import validate
from fleet import faults
from fleet.simulator import (
    SKU_MIX,
    healthy_host,
    load_measured_baseline,
    probe_samples,
    simulate,
    sku_baselines,
    write_fleet,
)

REPORTS = Path(__file__).resolve().parents[1] / "reports"


@pytest.fixture(scope="module")
def baselines() -> dict:
    return sku_baselines(REPORTS)


def test_same_seed_same_fleet(baselines: dict) -> None:
    a = simulate(50, 0.12, 7, baselines)
    b = simulate(50, 0.12, 7, baselines)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    c = simulate(50, 0.12, 8, baselines)
    assert json.dumps(a, sort_keys=True) != json.dumps(c, sort_keys=True)


def test_every_snapshot_matches_collector_schema(baselines: dict) -> None:
    snaps, _ = simulate(120, 0.3, 3, baselines)
    for s in snaps:
        assert validate(s) == [], s["host_id"]
        assert s["source"] == "simulator" and s["sku"] in SKU_MIX
        assert len(s["ecc"]["ce_daily"]) == 14


def test_sku_a_is_seeded_from_real_measurements(baselines: dict) -> None:
    measured = load_measured_baseline(REPORTS)
    for k, v in measured.items():
        assert baselines["sku-a"][k] == v
    # Derived SKUs differ from sku-a by their documented ratios.
    assert baselines["sku-b"]["l3_ns"] == pytest.approx(measured["l3_ns"] * 1.60, rel=1e-3)


def test_fault_rate_and_multi_fault(baselines: dict) -> None:
    _, truth = simulate(2000, 0.12, 11, baselines)
    labels = [t["fault_types"] for t in truth]
    faulty = [lab for lab in labels if lab]
    assert 0.09 < len(faulty) / len(labels) < 0.15
    multi = [lab for lab in faulty if ";" in lab]
    assert 0 < len(multi) / len(faulty) < 0.12
    for lab in faulty:
        assert set(lab.split(";")) <= set(faults.FAULT_TYPES)


def test_healthy_ce_mostly_zero(baselines: dict) -> None:
    rng = np.random.default_rng(0)
    hosts = [healthy_host(baselines["sku-a"], rng) for _ in range(500)]
    totals = [sum(h["ce_daily"]) for h in hosts]
    assert np.mean([t == 0 for t in totals]) > 0.8 and max(totals) <= 2


def test_write_fleet_separates_ground_truth(tmp_path: Path, baselines: dict) -> None:
    snaps, truth = simulate(20, 0.5, 5, baselines)
    write_fleet(tmp_path, snaps, truth)
    assert len(list((tmp_path / "snapshots").glob("*.json"))) == 20
    assert (tmp_path / "ground_truth.csv").read_text().startswith("host_id,fault_types")
    assert not list((tmp_path / "snapshots").glob("*.csv"))


# --- fault signatures ---------------------------------------------------------------

def _host(baselines: dict, seed: int = 0) -> tuple[dict, np.random.Generator]:
    rng = np.random.default_rng(seed)
    return healthy_host(baselines["sku-a"], rng), rng


def test_ecc_degradation_trends_up(baselines: dict) -> None:
    m, rng = _host(baselines)
    faults.memory_ecc_degradation(m, rng)
    slope = np.polyfit(np.arange(14), m["ce_daily"], 1)[0]
    assert slope > 0 and sum(m["ce_daily"]) > 0


def test_ue_sets_uncorrectable(baselines: dict) -> None:
    m, rng = _host(baselines)
    faults.memory_ue(m, rng)
    assert m["ue"] >= 1


def test_pcie_downtrain_halves_link(baselines: dict) -> None:
    for seed in range(10):
        m, rng = _host(baselines, seed)
        faults.pcie_link_downtrain(m, rng)
        nic = m["nic"]
        assert nic["cur_width"] < nic["max_width"] or nic["cur_speed"] < nic["max_speed"]


def test_thermal_is_hot_slow_and_physically_consistent(baselines: dict) -> None:
    m, rng = _host(baselines)
    before = dict(m)
    faults.thermal_throttling(m, rng)
    ratio = m["loaded_mhz"] / before["loaded_mhz"]
    assert 0.70 <= ratio <= 0.85 and m["loaded_temp_c"] >= 91
    # L1 latency in ns scales with 1/clock (fixed cycles).
    assert m["l1_ns"] == pytest.approx(before["l1_ns"] / ratio)
    assert m["read_gbps"] < before["read_gbps"]


def test_memory_config_slow_only_hits_dram_path(baselines: dict) -> None:
    m, rng = _host(baselines)
    before = dict(m)
    faults.memory_config_slow(m, rng)
    assert 1.10 <= m["dram_ns"] / before["dram_ns"] <= 1.20
    assert 0.70 <= m["read_gbps"] / before["read_gbps"] <= 0.80
    assert m["l1_ns"] == before["l1_ns"] and m["l3_ns"] == before["l3_ns"]


def test_subtle_shift_is_small_and_multi_metric(baselines: dict) -> None:
    m, rng = _host(baselines, 3)
    before = dict(m)
    faults.subtle_perf_degradation(m, rng)
    changed = [k for k in faults.SUBTLE_METRICS if m[k] != before[k]]
    assert 3 <= len(changed) <= 4
    for k in changed:
        assert 0.05 <= abs(m[k] / before[k] - 1) <= 0.08 + 1e-9


def test_glitch_corrupts_exactly_one_sample(baselines: dict) -> None:
    m, _ = _host(baselines)
    clean = probe_samples(m, np.random.default_rng(1), glitch=False)
    dirty = probe_samples(m, np.random.default_rng(1), glitch=True)
    diffs = [(k, i) for k in clean for i in range(3) if clean[k][i] != dirty[k][i]]
    assert len(diffs) == 1
