# Build SiliconScope — complete project prompt

Build the entire project below, end to end, autonomously. Save this spec as `CLAUDE.md` in the repo root so it persists across sessions, then work through all phases in order without waiting for my confirmation between phases.

> **Local environment note (added during the build):** development actually happens on a Windows 11
> laptop inside **WSL2 Ubuntu** (Intel i5-1135G7, 8 logical CPUs, ~3.8 GB RAM, Hyper-V VM), not a
> Codespace. All measurements in `reports/` come from that machine unless stated otherwise. CI runs on
> GitHub Actions `ubuntu-latest`. See `docs/DECISIONS.md`.

## 1. Context

I'm Harshil, a B.Tech CSE student (Amrita, Chennai). I'm building this project for an application to
**Meta's ASIC Engineer Intern – Infra Silicon Enablement** role (Release to Production team, Bangalore).
That team builds validation, diagnostics, performance-analysis and debug tooling for data-center silicon,
from lab bring-up to fleet deployment, and root-causes silicon issues in the fleet.

I will study the concepts separately, so don't stop to teach me. The code and docs must still be clear
enough that I can read any file and explain it in an interview.

**SiliconScope** is a small-scale version of that team's work:

1. **Probe** (C++): microbenchmarks that measure a machine's real memory hierarchy: L1/L2/L3/DRAM latency and memory bandwidth.
2. **Collector** (Python): reads hardware health from Linux (CPU, caches, memory, thermals, PCIe link state, ECC error counters) into one JSON snapshot.
3. **Fleet** (Python): simulates about 200 servers in the collector's snapshot format, injects known silicon faults, and then runs a validator (rule checks + Isolation Forest) that flags faulty servers, names a root cause, and is scored against ground truth.

## 2. Environment

- I develop in **GitHub Codespaces** (Ubuntu, a 2–4 core VM). Assume it's a VM: many hardware interfaces (EDAC, thermal zones, `perf` counters, `dmidecode`, real PCIe devices) may be missing or need root.
- **Every hardware read must degrade gracefully.** It should return `{"available": false, "reason": "..."}` and never crash.
- Toolchain: C++17 with `g++ -O2` and a plain `Makefile` (no CMake). Python 3.11+ with `numpy`, `pandas`, `matplotlib`, `scikit-learn` and `pytest`; lint with `ruff`. Pin versions in `requirements.txt`.
- Keep memory use under about 1 GB. The probe's default maximum working set is 256 MB, and it checks `/proc/meminfo` `MemAvailable` before allocating.

## 3. Working rules

1. **Build all phases (section 5) in order, without pausing for confirmation.** After each phase, run its checks (build, tests, the phase's commands). Fix any failure before moving on, then make a git commit (e.g. `phase1: pointer-chase latency probe + plot`).
2. **Never fabricate results.** Every number in the README, reports or docs must come from an actual run in this repo. Clearly label everything simulated as SIMULATED. If something can't be measured in this environment, say so.
3. **Readable over clever.** Use small functions, type hints, docstrings, and comments that explain *why*. Don't over-engineer: no frameworks, no databases, no web UI.
4. **Make sensible decisions yourself** where this spec is silent, and record each one in `docs/DECISIONS.md` (decision, alternatives, reason). Only stop and ask me if something is truly blocked (e.g. it needs credentials or a paid service).
5. **Tests run anywhere.** They must pass in a Codespace and on GitHub Actions without real hardware. Use fixture files and fake sysfs trees.
6. If a command fails because of the VM (e.g. `perf` is not permitted), handle it gracefully, note it in the README limitations, and continue.
7. **Write the study docs as you go** (I'll read them separately):
   - `docs/LEARNING.md`: a beginner-friendly explanation of every concept used (caches, pointer chasing, prefetching, TLB/huge pages, perf counters, sysfs/procfs, ECC CE/UE, PCIe link training, thermal throttling, robust z-score, Isolation Forest, precision/recall, held-out evaluation), each linked to the files where it appears.
   - `docs/INTERVIEW.md`: likely interview questions per component with concise model answers, a 60-second project pitch, and "what I'd do with more time".
   - `docs/WALKTHROUGH.md`: a guided tour of the codebase in reading order, explaining what each file does and how data flows.
8. **When everything is done**, give me a final summary:
   - What was built.
   - The real measured results and the held-out fleet evaluation numbers.
   - Anything that couldn't run in this environment.
   - 2–3 resume bullets that use only numbers from `reports/`.

## 4. Repository layout (target)

```
siliconscope/
├── CLAUDE.md                 # this spec
├── README.md
├── Makefile                  # top-level: make probe | collect | fleet | evaluate | report | test | all
├── requirements.txt
├── probe/
│   ├── Makefile
│   ├── latency.cpp           # pointer-chase latency vs working-set size
│   ├── bandwidth.cpp         # sequential read/write/copy bandwidth, optional multi-thread
│   └── common.hpp            # timing, arg parsing, CPU pinning, MemAvailable check
├── scripts/
│   ├── plot_latency.py       # latency curve + detected cache levels vs reported sizes
│   ├── plot_bandwidth.py
│   └── run_perf.sh           # perf stat wrapper, degrades gracefully
├── collector/
│   ├── __init__.py
│   ├── collector.py          # CLI: python -m collector.collector --out snapshot.json [--root /]
│   ├── readers/              # one module per subsystem: cpu, cache, memory, thermal, pcie, ecc, storage, platform
│   └── schema.py             # snapshot schema + validation (shared with fleet/)
├── fleet/
│   ├── __init__.py
│   ├── simulator.py          # generates fleet snapshots + ground_truth.csv
│   ├── faults.py             # fault injection functions, one per fault type
│   ├── validator.py          # rules + Isolation Forest + root-cause attribution
│   ├── rules.py
│   └── evaluate.py           # scores validator vs ground truth, ablations
├── reports/                  # generated outputs (gitignore large files; keep final PNGs + report md)
├── tests/
│   ├── fixtures/fake_sysfs/  # fake /sys and /proc trees for collector tests
│   ├── test_collector.py
│   ├── test_simulator.py
│   └── test_validator.py
├── docs/
│   ├── LEARNING.md
│   ├── INTERVIEW.md
│   ├── WALKTHROUGH.md
│   ├── DECISIONS.md
│   └── architecture.md       # mermaid diagram of probe → collector → fleet → validator → report
└── .github/workflows/ci.yml
```

## 5. Phases and acceptance criteria

### Phase 0 — Setup
- Repo skeleton, `requirements.txt`, `.gitignore`, ruff config, empty `docs/LEARNING.md` and `docs/INTERVIEW.md`.
- Top-level `Makefile` with placeholder targets.
- **Done when:** `make test` runs (even with 0 tests) and the first commit is pushed.

### Phase 1 — Memory latency probe
- `probe/latency.cpp`. If a `latency.cpp` already exists in `probe/`, build on it.
  - Build a single random cycle with **Sattolo's algorithm**, so every slot is visited and there are no short cycles. Stride the array by cache line (64 B) so each step touches a new line.
  - Sizes go from 4 KB to `--max-mb` (default 256) in powers of 2, plus midpoints (e.g. 48 KB, 96 KB) for better resolution.
  - `--repeats N` (default 5): report median, p10 and p90 ns per access. Do a warm-up pass before timing.
  - Pin to one CPU with `sched_setaffinity` (`--cpu K`, default 0).
  - Prevent dead-code elimination (consume the final pointer).
  - Output CSV: `size_bytes,ns_median,ns_p10,ns_p90`.
- `scripts/plot_latency.py`:
  - Log2 x-axis (with KB/MB labels) and ns on the y-axis, with a p10–p90 band.
  - Read the reported cache sizes from `/sys/devices/system/cpu/cpu0/cache/index*/` (fall back to `lscpu -J`) and draw them as vertical lines.
  - **Automatic level detection:** find the plateaus/knees (e.g. where the latency ratio between consecutive sizes exceeds a threshold), then output the inferred levels with their latency to `reports/latency_levels.json`.
  - Save `reports/latency_<hostname-hash>.png`.
- **Done when:** the plot's knees line up with the reported L1/L2 sizes, and `docs/LEARNING.md` explains why.

### Phase 2 — Bandwidth and perf counters
- `probe/bandwidth.cpp`: sequential read (sum into an accumulator), write and copy over buffers from 16 KB to 1 GB (capped by MemAvailable). Report GB/s. Add an optional `--threads N` using `std::thread`, with each thread working on its own buffer.
- Latency extensions:
  - `--pattern random|sequential` shows the prefetcher's effect.
  - `--hugepages` uses `madvise(MADV_HUGEPAGE)` to test whether the jump that comes before the L3 size disappears (a TLB-miss hypothesis).
- `scripts/run_perf.sh`: runs `perf stat -e cycles,instructions,cache-misses,cache-references,dTLB-load-misses` on a given probe run. If perf is unavailable or `perf_event_paranoid` blocks it, print a clear explanation and exit 0.
- **Done when:** there are bandwidth numbers, a random-vs-sequential comparison, and a written explanation in `docs/LEARNING.md` of every step in the measured latency curve. That includes the early jump (around 16 MB on one VM) below the listed L3 size, with evidence (huge pages and/or perf) for or against the TLB explanation.

### Phase 3 — Health collector
- `collector/` CLI: `python -m collector.collector --out snapshot.json [--root PATH] [--with-probe]`.
- `--root` prefixes every sysfs/procfs path so tests can use `tests/fixtures/fake_sysfs/`.
- Readers (each returns a dict and never raises; failures become `{"available": false, "reason": ...}`):
  - **cpu:** model, sockets/cores/threads, flags of interest (avx2, avx512f, etc.), current and max frequency per core from `cpufreq`.
  - **cache:** per-level size, line size and sharing, from `/sys/devices/system/cpu/cpu0/cache/`.
  - **memory:** MemTotal/MemAvailable; DIMM info via `dmidecode -t memory` only if running as root.
  - **thermal:** `/sys/class/thermal/thermal_zone*/temp` and `/sys/class/hwmon/*`.
  - **pcie:** for each `/sys/bus/pci/devices/*`, read `current_link_speed`, `max_link_speed`, `current_link_width` and `max_link_width`; set `downtrained: true` if current < max.
  - **ecc:** `/sys/devices/system/edac/mc/mc*/ce_count` and `ue_count`.
  - **storage:** `lsblk -J`; `smartctl` health if it's available.
  - **platform:** kernel, OS, virtualization (`systemd-detect-virt` or the hypervisor flag), timestamp, `host_id` (a SHA-256 hash of the hostname, never the raw name).
  - **probe (optional, with `--with-probe`):** run quick latency and bandwidth probes and embed a summary (inferred L1/L2/L3/DRAM ns, read GB/s).
- `collector/schema.py`: one schema (versioned, `schema_version: 1`) with a `validate(snapshot)` function. **The simulator must emit exactly this schema.**
- Tests: the fake sysfs tree covers a healthy machine, a downtrained PCIe link, nonzero ECC counts, and missing interfaces.
- **Done when:** `snapshot.json` is produced on this real machine, passes schema validation, and the tests pass.

### Phase 4 — Simulated fleet + validator  ← the centrepiece
**Simulator** (`fleet/simulator.py`, `fleet/faults.py`):
- CLI: `--n 200 --fault-rate 0.12 --seed S --out-dir data/fleet_S/`.
- 3 hardware SKUs (e.g. `sku-a`, `sku-b`, `sku-c`), each with baseline metrics. **Seed `sku-a`'s baseline from the real Phase 1–2 measurements in `reports/`**; derive the others with documented, plausible ratios.
- Each server snapshot (same schema as the collector) includes:
  - Probe summary: L1/L2/L3/DRAM latency and read bandwidth.
  - CPU frequency under load, and max temperature under load.
  - 14 days of daily ECC CE counts plus a UE count.
  - PCIe devices: a NIC (Gen4 x16) and an NVMe drive (Gen4 x4), with current/max link fields.
- Healthy noise is realistic: about 2–3% Gaussian on performance metrics, CE counts mostly 0 with rare isolated 1–2s, and occasional single-measurement glitches (to test robustness).
- **Fault types** (one function each in `faults.py`, all documented with the real-world cause):
  1. `MEMORY_ECC_DEGRADATION`: CE count trending upward over the 14 days.
  2. `MEMORY_UE`: one or more uncorrectable errors (critical).
  3. `PCIE_LINK_DOWNTRAIN`: NIC trains at x8 instead of x16, or Gen3 instead of Gen4.
  4. `THERMAL_THROTTLING`: high max temperature, a 15–30% drop in loaded frequency, and slightly lower bandwidth.
  5. `MEMORY_CONFIG_SLOW`: DIMMs running below rated speed, giving DRAM latency +10–20% and bandwidth −20–30%.
  6. `SUBTLE_PERF_DEGRADATION`: a 5–8% shift across several perf metrics that only shows up against the SKU baseline (this tests the ML stage).
  - About 5% of faulty servers get 2 faults at once.
- Write `ground_truth.csv` (`host_id,fault_types`) separately. **The validator must never read it.**
- Fully reproducible from the seed.

**Validator** (`fleet/validator.py`, `fleet/rules.py`):
- **Stage 1, rules** (deterministic, each one explainable):
  - PCIe current < max.
  - UE > 0.
  - CE trend slope above a threshold (linear fit over the 14 days) or a CE total above a threshold.
  - Temperature above a threshold together with a frequency ratio drop.
  - Per-SKU **robust z-score** (median/MAD) with |z| > 4 on each perf metric.
- **Stage 2, ML:** an `IsolationForest` per SKU on robust-normalized perf features, to catch anomalies the rules miss.
- **Root-cause attribution:** map the triggered rules and the most deviant features to one of the 6 labels, or `UNKNOWN_PERF_ANOMALY`. Give a severity (`critical` / `warning`) and a recommended action (e.g. "replace DIMM / run memtest", "reseat card, inspect riser, retrain link", "check cooling and fan curves", "verify DIMM speed in BIOS/SPD").
- Outputs:
  - `reports/fleet_results.csv` (per host: flagged, causes, severity, evidence).
  - `reports/fleet_report.md`: a human-readable triage report, worst first, with evidence values.
  - Plots: latency vs bandwidth scatter per SKU coloured by verdict; CE trend lines for flagged hosts.
- It also runs on a single real `snapshot.json` and produces a health report for one machine.

**Evaluation** (`fleet/evaluate.py`):
- **Tune thresholds on seed 1 only. Report final numbers on seed 2 (held out).** Document this in the report.
- Metrics: detection precision/recall/F1 overall and per fault type; root-cause accuracy on true positives; a confusion matrix (fault type × predicted cause).
- **Ablation:** rules only vs ML only vs combined. Show which fault types each stage catches; the ML stage should be what catches `SUBTLE_PERF_DEGRADATION`.
- Write `reports/evaluation.md` and a confusion-matrix PNG.
- **Done when:** `make fleet evaluate` runs end to end from a clean checkout, and the held-out results and ablation are in `reports/evaluation.md`.

### Phase 5 — Polish and ship
- `README.md`:
  - A one-paragraph pitch.
  - An architecture diagram (mermaid).
  - Quickstart (`make all`).
  - Real results: my latency plot and detected cache levels; bandwidth.
  - Fleet results: held-out precision/recall and the ablation table.
  - **An explicit "What's real vs simulated" section.**
  - Limitations, and how this would scale to a real fleet (agents per host, central store, per-SKU baselines, burn-in/stress tests such as stress-ng, memtester and fio).
- `.github/workflows/ci.yml` (ubuntu-latest):
  - Build the probe and run it in a quick mode (small sizes, few repeats).
  - ruff, then pytest.
  - Run the fleet simulation and evaluation on a small fleet.
  - Upload `reports/` as an artifact.
  - Add the CI badge to the README.
- `docs/INTERVIEW.md` final pass: a 60-second project pitch, the 3 hardest technical questions with answers, and "what I'd do with more time".
- Write 2–3 resume bullets in `docs/INTERVIEW.md` that use **only the numbers actually produced in reports/**.
- **Done when:** CI is green, the README renders well on GitHub, and all docs in `docs/` are complete.

## 6. Quality bar
- `make all` works from a clean clone in a Codespace in under about 5 minutes.
- All tests pass and ruff is clean before the final commit.
- No crashes on missing hardware interfaces.
- Every generated number traces back to a script and a seed.
- Code is commented well enough that I can walk an interviewer through any file.
