# Learning notes

Beginner-friendly explanations of every concept SiliconScope uses, each linked
to the code where it appears. Numbers quoted here come from the real runs in
[`reports/`](../reports/) on the development machine (Intel i5-1135G7 "Tiger
Lake", 4 cores / 8 threads, running Ubuntu under **WSL2 / Hyper-V**). Simulated
numbers are always labelled SIMULATED.

**Contents**

1. [Caches and the memory hierarchy](#1-caches-and-the-memory-hierarchy)
2. [Pointer chasing and Sattolo's algorithm](#2-pointer-chasing-and-sattolos-algorithm)
3. [Reading our latency curve, step by step](#3-reading-our-latency-curve-step-by-step)
4. [Automatic level detection (change points)](#4-automatic-level-detection-change-points)
5. [sysfs and procfs](#5-sysfs-and-procfs)

<!-- PHASE2+ sections are appended below as they are built. -->

---

## 1. Caches and the memory hierarchy

A CPU core can execute several instructions per nanosecond, but DRAM takes
roughly 100 ns to answer a load. Caches are small, fast SRAM memories that keep
recently used data close to the core:

| Level | Typical size (this CPU, from sysfs) | Who shares it | Rough latency |
|---|---|---|---|
| L1 data | 48 KB | one core (and its 2 hyperthreads) | 4–5 cycles |
| L2 | 1.25 MB | one core | ~14 cycles |
| L3 (LLC) | 8 MB | all cores | 40–70 cycles |
| DRAM | GBs | whole machine | 100+ ns |

Data moves between levels in fixed **cache lines** of 64 bytes
(`coherency_line_size` in sysfs). Loading one byte brings in its whole line.

**Why measure it?** A silicon validation team checks that a new chip (or a
server in the fleet) actually delivers the latency and bandwidth its design
promises. A DIMM at the wrong speed, a mis-set BIOS option or a faulty part
shows up as a shifted curve.

Where: [`probe/latency.cpp`](../probe/latency.cpp), [`collector/readers/cache.py`](../collector/readers/cache.py).

## 2. Pointer chasing and Sattolo's algorithm

To measure *latency* (not throughput) we need loads that cannot overlap. Modern
cores run dozens of independent loads in parallel ("memory-level parallelism"),
which hides latency. Pointer chasing makes every load depend on the previous
one:

```cpp
for (i = 0; i < steps; ++i) p = p->next;   // the address of load i+1 IS the data of load i
```

The core can't start the next load until the current one returns, so
`elapsed / steps` = latency of one load.

Three design details in [`probe/latency.cpp`](../probe/latency.cpp):

- **One node per 64-byte line.** `struct alignas(64) Node`. Every hop touches a
  new line; otherwise neighbouring hops would hit a line we already loaded.
- **Random order defeats the prefetcher.** Hardware prefetchers detect
  sequential and strided access and fetch ahead. A random order gives them
  nothing to predict. (Phase 2 shows what happens with a sequential order.)
- **Sattolo's algorithm, not plain shuffle.** Fisher–Yates shuffle picks swap
  partner `j` from `[0, i]`; Sattolo picks from `[0, i)`. That tiny change
  guarantees the permutation is **one single cycle** through all `n` nodes.
  A plain random permutation usually breaks into several cycles; if we started
  in a short one, we'd loop over a few nodes that sit in L1 and badly
  under-report latency.

Other measurement hygiene:

- **Warm-up lap** before timing, so we measure steady state, not page faults.
- **Pinning** with `sched_setaffinity` ([`probe/common.hpp`](../probe/common.hpp))
  so the OS doesn't migrate us to a core with a cold cache mid-run.
- **Repeats + percentiles.** 5 timed runs per size; we report median, p10, p90.
- **Dead-code elimination.** If the result `p` were never used, `-O2` could
  delete the whole loop. We add it into a `volatile` global (`g_sink`).
- **Enough steps.** At least 2²¹ hops per sample, so a sample lasts
  milliseconds and the ~20 ns cost of reading the clock is irrelevant.

## 3. Reading our latency curve, step by step

![latency curve](../reports/latency_e080fed1753e.png)

Real run (`make probe`, 4 KB → 256 MB, 5 repeats, pinned to CPU 0), as detected
in [`reports/latency_levels.json`](../reports/latency_levels.json):

| Detected plateau | Sizes | Median latency |
|---|---|---|
| L1 | 4 KB – 48 KB | 1.35 ns |
| L2 | 64 KB – 1 MB | 3.79 ns |
| L3 | 1.5 MB – 2 MB | 12.1 ns |
| L3#2 | 3 MB – 4 MB | 28.5 ns |
| L3#3 | 6 MB – 16 MB | 80.9 ns |
| DRAM | 24 MB – 256 MB | 127.4 ns |

**Why the L1 and L2 knees line up with sysfs.** Up to 48 KB the whole cycle
fits in the 48 KB L1d, so every hop is an L1 hit (~1.3 ns ≈ 5 cycles at
~4 GHz turbo). At 64 KB the set no longer fits; with random order nearly every
hop misses L1 and is served by L2 (~3.8 ns ≈ 14–16 cycles). The same happens
between 1 MB and 1.5 MB, around the 1.25 MB L2. The knees land *exactly* where
sysfs says because L1 and L2 are private to the core: nothing else competes for
them.

**Why there's a gentle rise inside L2 (256 KB → 1 MB: 3.7 → 5–7 ns).** That's
the TLB, not the cache. The first-level data TLB holds 64 entries × 4 KB pages
= 256 KB of "reach". Beyond 256 KB, random hops start to miss that first-level
TLB and pay a few extra cycles for a second-level TLB (STLB) lookup. See
[TLB and huge pages](#tlb-and-huge-pages) (Phase 2) for the experiment that
tests this.

The L3 sub-steps (3 MB, 6 MB) and why the jump comes *below* the 8 MB L3 are
explained, with evidence, in Phase 2's section below.

## 4. Automatic level detection (change points)

[`scripts/plot_latency.py`](../scripts/plot_latency.py) turns the curve into
levels without being told the cache sizes:

1. Take log(p10 latency) per size. **Log** because each level is a
   multiplicative jump (×3, ×3, ×10). **p10** because noise on a VM (other
   tenants, the Windows host) only ever *adds* time, so low percentiles are
   closer to the true cost.
2. Fit the best **piecewise-constant** ("staircase") function with *k* steps by
   dynamic programming: `dp[s][j]` = least squared error covering the first `j`
   points with `s` steps. O(k·n²) for n ≈ 33 points is instant.
3. Add steps while each new step removes ≥ 35 % of the remaining error
   (`MIN_GAIN`); stop when an extra step would only be fitting noise.
4. Boundaries between steps are the **knees**. Each plateau is then *named*
   L1/L2/L3/DRAM by checking which sysfs-reported cache holds its first size.
5. `match_knees` checks every reported cache size has a knee within half an
   octave (×1.41, our grid resolution). For this run: L1 ✅ (0.21 octaves),
   L2 ✅ (0.03 octaves), L3 ❌ (0.71: the big jumps happen *below* 8 MB).

Why not "ratio between neighbours > threshold" (the simple approach)? On this
machine the L3 region rises smoothly from 11 to 80 ns, so a neighbour-ratio
rule fires at almost every point there. Fitting the whole staircase at once is
more robust. Decision logged in [`DECISIONS.md`](DECISIONS.md).

## 5. sysfs and procfs

Linux exposes kernel and hardware state as files:

- **procfs** (`/proc`): process and kernel info. `/proc/meminfo`'s
  `MemAvailable` is the kernel's estimate of memory we can use without
  swapping. The probe reads it before allocating
  ([`probe/common.hpp`](../probe/common.hpp) `cap_to_available`).
- **sysfs** (`/sys`): the kernel's device model. Each cache of cpu0 is a
  directory `/sys/devices/system/cpu/cpu0/cache/indexN/` with files `level`,
  `type`, `size`, `coherency_line_size`, `ways_of_associativity`,
  `shared_cpu_list` ([`collector/readers/cache.py`](../collector/readers/cache.py)).

Because they're just files, we can **fake** them in tests: copy the directory
shape into `tests/fixtures/fake_sysfs/…` and point the reader's `root` there.
