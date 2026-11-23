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
   `MIN_GAIN = 0.35` was chosen by looking at the one real sweep: it yields 6 plateaus, whose L1/L2
   boundaries match sysfs.

10. **Plateaus are named by the reported cache that holds their *first* size**; repeated names
    get `#2`, `#3`. — Alternative: name by their last size. — A plateau starts right after the
    previous level overflows, so its start identifies the serving cache. Naming by the last size
    mislabelled the 6–16 MB plateau as "DRAM".

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
