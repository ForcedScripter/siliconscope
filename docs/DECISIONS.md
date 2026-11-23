# Decisions log

Each entry: **decision** — alternatives considered — reason.

## Environment

1. **Develop and measure in WSL2 Ubuntu on the author's Windows laptop, not a Codespace.**
   — Alternatives: a GitHub Codespace (the spec's assumption), or native Windows (MinGW).
   — The repo lives on a Windows machine. WSL2 is a real Linux kernel in a Hyper-V VM, so it
   matches the spec's "assume a VM" model: sysfs, `sched_setaffinity` and `madvise` work, while
   EDAC, thermal zones, real PCIe devices and perf counters are mostly missing. Native Windows
   lacks those Linux APIs. CI (GitHub Actions, `ubuntu-latest`) is the second environment.
   All numbers in `reports/` say which machine they came from.

2. **Python venv lives in `~/.venvs/siliconscope` (WSL home), selected with `make PYTHON=...`.**
   — Alternative: `.venv/` in the repo. — The repo is on a OneDrive-synced Windows drive mounted
   at `/mnt/c`, where tens of thousands of small venv files are slow and get synced to the cloud.
   The Makefile defaults to `python3`, so a Codespace or CI just uses its own interpreter.

3. **Pinned versions = the newest releases at build time** (numpy 2.5.5, pandas 3.1.1,
   matplotlib 3.11.2, scikit-learn 1.9.1, pytest 9.2.0, ruff 0.17.6).
   — Alternative: older "LTS-ish" pins. — These are the exact versions that produced the results.
   CI runs Python 3.12, which these releases support.

4. **GitHub repo created as private** (`ForcedScripter/siliconscope`).
   — Alternative: public. — Making something public is the owner's call. Flip it with
   `gh repo edit --visibility public` when ready to share it with recruiters.

5. **`host_id` = first 12 hex chars of SHA-256(hostname).**
   — Alternatives: the full 64-char hash, or a random UUID. — 12 hex chars (48 bits) is plenty to
   keep 200 hosts unique, is readable in reports and filenames, and stays stable across runs
   (a UUID would not). The Makefile computes the same value with `sha256sum`.

## Phase 1 — latency probe

6. **One buffer allocated at the largest size; smaller working sets use a prefix of it.**
   — Alternative: allocate per size. — Fewer page faults and less allocator noise. The warm-up
   lap touches only the prefix in use.

7. **Steps per sample = max(n_lines, 2²¹).** — Alternative: a fixed time budget per size.
   — A deterministic count is simpler to explain. 2²¹ hops is ≥ 2.6 ms even at L1 speed, far
   above timer resolution, and the 256 MB point (4 M lines × ~130 ns ≈ 0.5 s per repeat) keeps a
   full sweep at about 20 s.

8. **Nearest-rank percentiles over 5 repeats.** — Alternative: interpolated percentiles.
   — With 5 samples, p10 ≈ the minimum and p90 ≈ the maximum; nearest-rank makes that obvious.

9. **Level detection = optimal piecewise-constant fit of log(p10) via dynamic programming,
   adding steps while each removes ≥ 35 % of the remaining squared error.**
   — Alternatives: a ratio threshold between neighbouring sizes (the spec's example), or
   k-means on latency. — On this VM the L3 region ramps smoothly from 11 → 80 ns, so a neighbour
   ratio threshold fires at almost every point. The DP fit finds the staircase globally.
   `MIN_GAIN = 0.35` was chosen by looking at the first real sweep (6 plateaus); on the canonical
   sweep it yields 5 plateaus, and all of the L1/L2/L3 knees fall within half an octave of the
   sysfs sizes.

10. **Plateaus are named by the reported cache that strictly holds their *first* size**
    (`first_size < cache_size`); repeated names get `#2`, `#3`. — Alternative: name by their last
    size. — A plateau starts right after the previous level overflows, so its start identifies the
    serving cache. Naming by the last size mislabelled a 6–16 MB plateau as "DRAM". The comparison
    is strict because a working set equal to the cache can't fit alongside everything else.

11. **The probe summary (L1/L2/L3/DRAM ns) uses the first plateau carrying each name**,
    falling back to "latency at half the cache size". — Alternative: always sample at half the
    reported size. — Half-size samples land in TLB-affected regions (L3 at 4 MB = 30 ns vs the
    clean 12 ns plateau), which would misstate the cache's own latency.

12. **The cache sysfs reader (`collector/readers/cache.py`) was written in Phase 1, ahead of the
    rest of the collector.** — The latency plot needs reported cache sizes, and duplicating that
    parser in `scripts/` would let the two drift apart.

13. **`scripts/` is a Python package (`python -m scripts.plot_latency`).**
    — Alternative: standalone scripts that edit `sys.path`. — Plain imports of `collector.*` and a
    shared `scripts/plotstyle.py`, with no path hacks.

## Phase 2 — bandwidth, prefetcher, TLB, perf

14. **The read kernel uses 8 independent accumulators.** — Alternatives: a single `sum +=`, or
    `-O3 -march=native` auto-vectorisation. — A single accumulator is a serial dependency chain
    (it measured ~16 GB/s in L1, i.e. the adder). Explicit accumulators keep `-O2` as the spec
    requires and are easy to explain.

15. **Bandwidth memory budget = min(1 GB, ½ MemAvailable) in total, across all threads and
    buffers.** — Alternative: a 1 GB buffer per op as the spec literally says. — The spec also asks
    to stay under ~1 GB, so copy (2 buffers) stops at 512 MB, and 4 threads stop at 256 MB each.
    The DRAM figure uses sizes ≥ 4× L3 (≥ 32 MB), which the budget always covers.

16. **Multi-thread runs place threads one per physical core first** (from sysfs `core_id`).
    — Alternative: CPUs 0..N-1. — vCPUs 0 and 1 are SMT siblings here (`shared_cpu_list=0-1`), so
    CPUs 0–3 means only 2 cores, and we would be measuring shared-L1 contention, not scaling.

17. **`run_perf.sh` falls back to any versioned perf in `/usr/lib/linux-tools/*/perf`.**
    — Alternative: require a matching `linux-tools-$(uname -r)`. — WSL/cloud kernels never have a
    matching package, but a newer perf binary works fine with an older kernel's perf_event ABI.

18. **The TLB experiment normalises counters per pointer-chase hop and compares 4 KiB vs 2 MiB
    pages at identical sizes.** — Alternative: count only the timed loop (perf `--control` or
    libpfm inside the probe). — Wrapping the whole process is simpler. Setup overhead is identical
    for both page sizes, so the *comparison* is still valid.

19. **Canonical bandwidth numbers come from a re-run; the degraded run is kept in
    `reports/glitch/`.** — Alternative: silently keep the first run, or silently replace it. — The
    first run in `make probe` was 3–4× slower in-cache, then two immediate re-runs agreed with each
    other. Keeping both is honest, and makes a useful example of a single-measurement glitch.

## Phase 3 — collector

20. **A hand-rolled schema validator (`collector/schema.py`) instead of `jsonschema`.**
    — Alternatives: the jsonschema library, or pydantic models. — No extra dependency; the rules
    (versioned top level, sections that are either available-with-fields or unavailable-with-reason,
    typed list items) fit in ~100 readable lines.

21. **Every section uses the same `available` + `reason` envelope**, including sub-sections such
    as `cpu.freq` and `memory.dimms`. — Alternative: omit missing sections. — Consumers can tell
    "not measured" (with a reason) from "measured as zero", which matters for ECC.

22. **`ecc.ce_daily` is `null` from the collector and a 14-element list from the simulator.**
    — Alternative: have the collector keep a local history file. — One snapshot can't contain a
    trend, and persisting state would need a store, which the spec rules out.

23. **External tools (`dmidecode`, `lsblk`, `smartctl`, `systemd-detect-virt`) only run when
    `--root` is the real `/`.** — Alternative: put fixture outputs in the fake tree. — Running
    `lsblk` against a fake sysfs would mix real and fake data. Tool parsing is tested with
    fixtures (`dmidecode_memory.txt`) and monkeypatching instead.

24. **Fake PCI device directories are named `0000-17-00.0`, not `0000:17:00.0`.** — `:` is not
    a valid NTFS filename character, and this repo is checked out on Windows. The reader only
    iterates directory names, so the shape of the test is unchanged.

25. **The fake trees are generated by `tests/fixtures/make_fake_sysfs.py`, and the output is
    committed.** — Alternative: build them in `tmp_path` inside each test. — Committed files are
    browsable on GitHub, match the spec's `fake_sysfs/` layout, and the generator documents how
    they were made.

26. **`--with-probe` uses quick mode (latency ≤ 64 MB × 3 repeats, bandwidth ≤ 256 MB) and samples
    frequency/temperature in a background thread.** — Alternative: a separate stress-ng run. —
    ~15 s total, no extra dependency, and 64 MB is still 8× the L3, so the last plateau is DRAM.

27. **Missing cpufreq leaves `loaded_cpu_mhz` as `null`.** — Alternative: fall back to `cpu MHz`
    from `/proc/cpuinfo`. — In VMs that field is a static TSC value, not the real clock; reporting
    it as a "loaded frequency" would be fabricated precision.

28. **During `--with-probe`, the collector process moves off CPU 0's whole physical core**
    (CPU 0 and its SMT siblings). — Alternatives: leave it unpinned, or just avoid CPU 0. —
    Measured: unpinned → L1 1.79 ns; off CPU 0 only → 1.71 ns (CPU 1 is CPU 0's hyperthread);
    off the whole core → 1.30 ns, matching the standalone probe.

## Phase 4 — simulated fleet and validator

29. **The simulator draws "true" host metrics, then renders 3 probe samples per metric and
    reports their mean; the validator uses the median of the samples.** — Alternative: emit a
    single value per metric. — This is how the spec's "single-measurement glitches" can test
    robustness: a naive validator using the reported mean would flag glitched healthy hosts. The
    `samples` field is optional in schema v1, so real collector snapshots stay valid.

30. **SKU frequencies and temperatures are documented assumptions.** sku-a uses the dev CPU's
    published 4.2 GHz max / 3.8 GHz all-core turbo. — cpufreq and thermal sensors don't exist in
    WSL2, so there's nothing real to seed them from; the README labels them as assumptions.

31. **sku-b/sku-c perf = sku-a × fixed ratios** (sku-b: L3 ×1.6, DRAM ×1.2, bandwidth ×0.85;
    sku-c: L3 ×1.25, DRAM ×1.05, bandwidth ×1.25). — Alternative: invent absolute numbers. —
    Ratios keep every SKU traceable to the one real measurement; the reasoning for each is in
    `DERIVED_SKUS`.

32. **Thermal throttling scales L1/L2 ns by 1/clock-ratio, and L3 by about half that.** — A
    fault that only changed frequency would be physically inconsistent: cache latency is fixed in
    cycles.

33. **Simulated snapshots mark `memory.dimms` as unavailable** ("agent runs unprivileged"). —
    Alternative: include rated vs configured DIMM speed. — Fleet agents often run without root;
    and it makes MEMORY_CONFIG_SLOW a perf-pattern diagnosis rather than a trivial field lookup.

34. **The IF features are signed robust z-scores clipped at 0, plus a composite slowdown score.**
    — Alternative: raw metrics. — Raw metrics differ by SKU and scale; z-scores make features
    comparable. Clipping stops "faster than siblings" from looking anomalous. The composite score
    is what makes a 5–8 % multi-metric shift visible.

35. **Isolation Forest uses `max_samples = min(64, n)` for every SKU and fleet size.** — The
    default (`min(256, n)`) changes the score normalisation between a 1000-host tuning fleet and
    a 200-host test fleet, so a tuned threshold would not transfer. Decided on dev seeds, before
    any held-out result was seen.

36. **The tuning fleet (seed 1) has 1000 hosts; the held-out fleets have 200.** — Alternative:
    tune on 200 hosts. — With 200 hosts seed 1 had only 2 SUBTLE examples, and every IF threshold
    from 0.62 to 0.71 tied. The spec fixes *which seed* is used for tuning, not its size; test
    fleets keep the spec's 200 hosts.

37. **Tuning = per-rule-group grid search; ties resolved toward the centre of the tied
    region.** CE thresholds maximise CE-rule F1; thermal thresholds maximise thermal-rule F1; the
    IF threshold maximises combined detection F1. — Alternative: one joint grid. — The groups are
    independent and the IF scores don't depend on rule thresholds, so per-group search is
    exhaustive and fast. Centre-of-ties leaves margin on both sides.

38. **Attribution "explains away".** UE subsumes CE activity on the same host; throttling
    subsumes slow caches. A DRAM slowdown beyond what throttling can cause (bandwidth z ≥ 6) is
    still reported as a second fault. — Without this, every UE host would also be blamed for CE
    degradation, and every thermal host for a perf anomaly.

39. **Severity: `critical` only for MEMORY_UE; everything else is `warning`.** — Alternative:
    severity from magnitude. — UEs crash workloads or corrupt data today; the others degrade
    performance or predict future failure. The triage report sorts within a severity by magnitude.

40. **Evaluation adds nine extra held-out seeds (3–11) beside the spec's seed 2.** — A 200-host
    fleet has only 3–7 hosts per fault type, so a single seed's per-type recall moves in steps of
    15–30 %. Mean ± sd over ten fleets shows how stable the headline is.

41. **Confusion-matrix convention:** one entry per (host, true fault). The column is that fault
    if it was predicted, otherwise the host's primary predicted cause, otherwise `NOT_FLAGGED`.
    — This handles multi-fault hosts without double-counting healthy hosts.

42. **Development used dev seeds 101+ only; seeds 1–11 were generated only by `make fleet`.**
    — This keeps the held-out numbers honest: the first time held-out results were computed was
    the final `make fleet evaluate` run.

43. **Single-host health report: ±15 % tolerance against the sku-a reference.** — With one
    machine there is no fleet distribution for z-scores. Checks that need missing interfaces are
    reported as "not evaluated" with the reason, never as "pass".

## Phase 5 — shipping

44. **CI uses Python 3.12 and a quick probe** (latency ≤ 16 MB, bandwidth ≤ 64 MB, 2 repeats)
    written to `reports/ci/`, plus a smaller fleet (100 test hosts, 300 tuning hosts, seeds 2–4).
    — Keeps CI to a few minutes. The committed canonical results come from the dev machine; CI
    proves the code builds and runs and that perf degrades gracefully, and uploads `reports/` as
    an artifact.

45. **Resume bullets use only numbers present in `reports/`** (latency_levels.json,
    bandwidth_summary.json, perf_tlb.md, evaluation.md). Code-size facts (test counts, reader
    counts) are left out of the bullets for that reason.
