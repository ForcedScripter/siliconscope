# Walkthrough: the codebase in reading order

Read top to bottom. Each step says what the file does, the 2–3 things worth pointing
at in an interview, and where its output goes next.

## 0. The map

- [`CLAUDE.md`](../CLAUDE.md): the spec this was built from.
- [`Makefile`](../Makefile): the whole pipeline as targets (`probe → collect → fleet →
  evaluate → report`). Reading the target bodies is the fastest way to see data flow.
- [`docs/architecture.md`](architecture.md): the diagram.

## 1. Probes (C++), measuring real hardware

1. [`probe/common.hpp`](../probe/common.hpp): shared helpers.
   - `now_ns()`: a monotonic clock.
   - `Args`: tiny `--key value` parser.
   - `pin_to_cpu()`: `sched_setaffinity`.
   - `cpus_one_per_core_first()`: SMT-aware thread placement from sysfs topology.
   - `mem_available_bytes()` / `cap_to_available()`: read `/proc/meminfo` before allocating.
   - `alloc_buffer()`: 2 MiB-aligned buffer, optional `madvise(MADV_HUGEPAGE)`.
   - `percentile()`: nearest-rank.
2. [`probe/latency.cpp`](../probe/latency.cpp): the pointer chase.
   - `Node` is exactly one 64-byte line.
   - `sattolo_cycle()` builds one random cycle; `sequential_cycle()` is the prefetcher control.
   - `chase()` is the dependent-load loop.
   - `main()` loops sizes (powers of two + 1.5× midpoints), runs a warm-up lap, then
     `--repeats` timed samples, and prints `size_bytes,ns_median,ns_p10,ns_p90`.
   - `g_sink` defeats dead-code elimination.
   - With `--hugepages` it prints `AnonHugePages` to stderr, to prove THP was granted.
3. [`probe/bandwidth.cpp`](../probe/bandwidth.cpp): read/write/copy streams.
   - The read uses 8 accumulators, so it measures memory rather than adder latency.
   - `sample_gbps()` starts N pinned threads behind a spin barrier and divides by the
     slowest thread's time.
   - The total memory budget is capped at 1 GB or ½ MemAvailable.
4. [`probe/Makefile`](../probe/Makefile): `g++ -O2 -std=c++17`, two binaries.

## 2. Turning measurements into knowledge

5. [`scripts/plot_latency.py`](../scripts/plot_latency.py)
   - `best_segmentation()`: DP piecewise-constant fit of log(p10).
   - `choose_segmentation()`: keeps adding steps while each removes ≥ 35 % of the error.
   - `detect_levels()`: plateaus and knees, named via sysfs sizes.
   - `match_knees()`: is each reported cache size within ½ octave of a knee?
   - `summarize()`: the compact L1/L2/L3/DRAM ns used downstream.
   - `--compare` overlays huge-page and sequential runs.
6. [`scripts/plot_bandwidth.py`](../scripts/plot_bandwidth.py): small multiples per thread
   count. `summarize()` gives in-cache peak and DRAM (sizes ≥ 4× L3) GB/s.
7. [`scripts/run_perf.sh`](../scripts/run_perf.sh) + [`scripts/perf_tlb.py`](../scripts/perf_tlb.py):
   perf wrapper that degrades gracefully, plus the 4 KiB-vs-2 MiB TLB experiment normalised
   per hop.
8. [`scripts/plotstyle.py`](../scripts/plotstyle.py): one palette and style for every PNG.

**Outputs:** `reports/latency_levels.json`, `reports/bandwidth_summary.json`, `reports/perf_tlb.md`
and the PNGs. These are the *real* numbers everything later depends on.

## 3. Collector: hardware health of one machine

9. [`collector/readers/common.py`](../collector/readers/common.py): read this first.
   - `rpath()` / `read_text()` prefix every path with `root`, which is how tests swap in fake
     trees.
   - `unavailable()` is the standard failure shape.
   - `run_tool()` wraps external commands without ever raising.
   - `host_id()` hashes the hostname.
10. The readers, one per subsystem, all of the form `read_x(root) -> dict`, never raising:
    - [`cache.py`](../collector/readers/cache.py): `/sys/.../cache/index*`, with an `lscpu -J`
      fallback.
    - [`cpu.py`](../collector/readers/cpu.py): `/proc/cpuinfo` + cpufreq.
    - [`memory.py`](../collector/readers/memory.py): meminfo; the dmidecode parser flags
      below-rated DIMMs.
    - [`thermal.py`](../collector/readers/thermal.py): thermal zones + hwmon.
    - [`pcie.py`](../collector/readers/pcie.py): link speed/width; `downtrained`.
    - [`ecc.py`](../collector/readers/ecc.py): EDAC CE/UE.
    - [`storage.py`](../collector/readers/storage.py): lsblk + smartctl.
    - [`platform.py`](../collector/readers/platform.py): kernel, OS, virtualization, `host_id`.
    - [`probe.py`](../collector/readers/probe.py): `--with-probe`. Runs the quick probes
      while `LoadSampler` samples frequency and temperature, and keeps itself off CPU 0's
      physical core.
11. [`collector/schema.py`](../collector/schema.py): **the contract.** `SECTION_FIELDS` and
    `ITEM_FIELDS` say what a valid snapshot is; `validate()` returns readable error strings.
12. [`collector/collector.py`](../collector/collector.py): `collect()` calls every reader and
    assembles the snapshot; `main()` validates and writes JSON.

**Output:** `reports/snapshot_<host>.json`.

## 4. Fleet: simulate, validate, evaluate

13. [`fleet/faults.py`](../fleet/faults.py): six small functions. Read each docstring: it's
    the real-world cause, and the code is its signature.
14. [`fleet/simulator.py`](../fleet/simulator.py)
    - `sku_baselines()`: sku-a from `reports/`; sku-b/c by `DERIVED_SKUS` ratios.
    - `healthy_host()` → `choose_faults()` → `inject()` → `probe_samples()` (3 samples,
      optional glitch) → `render_snapshot()`, which emits schema v1.
    - `write_fleet()` validates every snapshot and writes `ground_truth.csv` separately.
15. [`fleet/rules.py`](../fleet/rules.py): `Thresholds` (all knobs, load/save JSON);
    `Finding`; five rules, each a few lines with an evidence string.
16. [`fleet/validator.py`](../fleet/validator.py): the core.
    - `extract_features()`: median of samples.
    - `robust_z()` / `add_fleet_stats()`: per-SKU, signed so that worse is positive.
    - `ml_scores()`: Isolation Forest per SKU.
    - `classify_perf_pattern()` + `attribute()`: causes, severity, action, evidence.
    - `featurize()` is split from `validate_features()` so tuning can reuse the expensive part.
    - `health_report()`: the single-machine mode.
17. [`fleet/report.py`](../fleet/report.py): CSV, the triage Markdown (worst first) and two
    plots.
18. [`fleet/evaluate.py`](../fleet/evaluate.py): the only reader of ground truth.
    - `score()`: P/R/F1, per type, root cause, confusion.
    - `tune()`: grid search on seed 1 with central tie-break.
    - `evaluate()`: held-out seed 2 + robustness seeds, ablation over `MODES`.

**Outputs:** `reports/thresholds.json`, `fleet_results.csv`, `fleet_report.md`, `evaluation.md`,
`confusion_matrix.png`, `health_<host>.md`.

## 5. Tests

19. [`tests/fixtures/make_fake_sysfs.py`](../tests/fixtures/make_fake_sysfs.py) builds
    `fake_sysfs/{healthy,degraded,minimal}`.
    - [`test_collector.py`](../tests/test_collector.py): every reader on all three trees, plus
      parsers.
    - [`test_simulator.py`](../tests/test_simulator.py): determinism, schema parity, each fault
      signature, glitches.
    - [`test_validator.py`](../tests/test_validator.py): rules, robust z, glitch robustness,
      the end-to-end ablation, and an AST check that the validator never references ground
      truth.
20. [`.github/workflows/ci.yml`](../.github/workflows/ci.yml): build, quick probe, ruff,
    pytest, collector on the CI VM, a small fleet evaluation, and `reports/` uploaded as an
    artifact.

## Data flow, end to end

```
probe CSVs ──► plot_latency / plot_bandwidth ──► latency_levels.json, bandwidth_summary.json
                                                           │ (sku-a baseline)
collector ──► snapshot.json (schema v1) ──► validator --snapshot ──► health_<host>.md
                                                           ▼
simulator ──► data/fleet_S/snapshots/*.json ──► validator ──► fleet_results.csv / fleet_report.md
          └─► data/fleet_S/ground_truth.csv ─────────────────► evaluate ──► evaluation.md
```
