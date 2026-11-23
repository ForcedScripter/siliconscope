# Interview prep

All numbers quoted here are in [`reports/`](../reports/). Fleet numbers are SIMULATED, so say so.

## 60-second pitch

> "SiliconScope is a small version of what a release-to-production silicon team builds, in three
> parts. First, C++ microbenchmarks measure a machine's real memory hierarchy. A pointer chase
> over a Sattolo random cycle gives latency per level. On my Tiger Lake laptop under WSL2 that's
> 1.3 ns for L1, 4.7 for L2, 14 for L3 and 94 for DRAM, found automatically by a change-point fit.
> I used perf counters to explain each step. For example, a latency jump below the 8 MB L3 looked
> like TLB misses, but page walks were near zero there, while LLC misses climbed. So it's
> effective L3 capacity, not the TLB. Second, a Linux collector reads caches, PCIe link training,
> ECC counters, thermals and DIMM speed into one versioned JSON schema that degrades gracefully
> on VMs. Third, I simulate 200 servers in that same schema with six injected silicon faults. A
> validator using robust-z rules plus a per-SKU Isolation Forest flags them, names the root cause
> and recommends an action. I tuned it on one seed and tested it on a held-out one: F1 0.964,
> every root cause correct. The ablation shows the rules catch ECC and PCIe faults, while the ML
> stage catches a 5–8 % multi-metric slowdown that thresholds miss."

## Resume bullets (numbers only from `reports/`)

- Built C++17 memory microbenchmarks (Sattolo pointer-chase latency; multi-threaded
  read/write/copy bandwidth) with automatic change-point cache-level detection. Measured
  L1/L2/L3/DRAM at 1.30/4.74/14.05/94.05 ns and in-cache read bandwidth of 72→206 GB/s (1→4 cores).
  Used perf counters to show an early sub-L3 latency jump was LLC capacity, not TLB
  (0.003 page walks/hop vs LLC misses rising to 1.05/hop).
- Wrote a Linux hardware-health collector (PCIe link-training state, EDAC ECC counters,
  thermals, DIMM speed, cache topology) emitting a versioned JSON schema that degrades
  gracefully on VMs, tested hardware-free against fake sysfs trees in GitHub Actions CI.
- Designed a fleet validator (per-SKU robust-z rules + Isolation Forest + root-cause
  attribution) for 200 simulated servers with 6 injected silicon fault types. On a held-out
  fleet: precision 1.000, recall 0.931, F1 0.964, 27/27 root causes correct. The ML stage raised
  subtle-degradation recall from 0.333 to 0.854 (pooled over 9 more held-out fleets).

---

## The 3 hardest questions (with answers)

### 1. "Your latency jumps at 3–6 MB, but the L3 is 8 MB. How do you know it isn't the TLB?"

Two independent pieces of evidence ([`reports/perf_tlb.md`](../reports/perf_tlb.md)):

1. **perf page-walk counts are near zero there**: 0.003 walks/hop at 4 MB and 0.035 at 6 MB,
   while *LLC misses* per hop go from 0.039 at 2 MB to 0.26 at 4 MB and 1.05 at 6 MB.
2. **Huge pages don't remove the jump** (4 MB: 31.6 vs 37.6 ns), and the run was verified to
   be THP-backed (`AnonHugePages` = 65536 kB for 64 MB).

So the core effectively gets ~4–6 MB of L3. The L3 is shared with the other cores, the iGPU and
the Windows host. It's also non-inclusive with adaptive replacement, and random 4 KiB physical
pages cause set conflicts.

The TLB *does* show up later: above ~8 MB (the STLB reach), walks climb to 0.89/hop at 64 MB.
There, huge pages didn't cut the walk count (0.891 vs 0.890) but made each walk cheaper
(119.5 → 97.1 ns). I explain that as Hyper-V backing guest memory with 4 KiB host pages:
the TLB caches the combined translation at the smaller page size, but the guest half of the
2-D walk gets one level shorter.

### 2. "Your fleet is simulated, so isn't 0.964 F1 meaningless?"

It's meaningful for what it tests, and I'm explicit about the limits.

**What it shows:**
- The *pipeline* works end to end on the exact schema a real collector emits.
- The rules and the ML stage are complementary (the ablation).
- Tuning didn't leak: thresholds were chosen on seed 1 only, design used separate dev seeds,
  and the score holds across 9 more held-out seeds (F1 0.963 ± 0.022).

**What it doesn't show:** real-world accuracy. Real faults are intermittent, correlated by
rack or batch, and confounded by workload.

**What I did to keep it honest:**
- Fault signatures follow the physics (e.g. throttling slows L1 in ns by 1/clock-ratio).
- sku-a is seeded from real measurements.
- Healthy noise includes measurement glitches modelled on a real glitch I hit.

**Next step on real data:** run the collector on burn-in hosts, use repair tickets as labels,
and re-measure precision and recall.

### 3. "Why Isolation Forest? Why not just a lower z threshold?"

`SUBTLE_PERF_DEGRADATION` moves 3–4 metrics by 5–8 % each. With ~2.5 % part-to-part noise,
that is only 2–3 σ per metric. To catch it with per-metric thresholds you'd need |z| ≈ 2.
With 5 metrics × 200 hosts, that fires on dozens of healthy hosts.

The signal is that *several* metrics are bad *at once*, which is a joint property. The Isolation
Forest sees all features together, and I added a composite "mean positive z" feature that turns
four small shifts into one large value.

The ablation confirms it: pooled SUBTLE recall is 0.833 for ML only vs 0.333 for rules only,
while precision stays high. A Mahalanobis distance or one-class SVM would also work. I chose IF
because it's unsupervised, cheap, scale-free once features are z-scored, and easy to explain:
anomalies are isolated in fewer random splits.

---

## Likely questions by component

### Probe (C++)

**Q: Why pointer chasing?** Each load's address comes from the previous load, so loads can't
overlap. Time per hop = load-to-use latency. Independent loads would measure throughput instead.

**Q: Why Sattolo's algorithm?** It guarantees a single cycle through all nodes. A plain shuffle
creates several cycles, and a short one would stay in L1 and under-report latency.

**Q: Why one node per 64-byte line?** So every hop touches a new cache line. Otherwise some
hops would hit lines that are already loaded.

**Q: How do you stop the compiler deleting the loop?** The final pointer is added into a
`volatile` global. The loop body is a dependent chain the compiler can't skip.

**Q: Why p10 for detection and median for reporting?** On a VM, noise only *adds* time, so p10
is closer to the hardware cost and gives cleaner knees. The median is the honest typical value.

**Q: Why did your first bandwidth "read" give 16 GB/s in L1?** `sum += x` is a serial dependency
chain, so it measured adder latency. 8 independent accumulators fixed it (72 GB/s peak).

**Q: Why is single-thread DRAM bandwidth (13.7 GB/s) so far below 4 threads (34.3 GB/s)?**
Little's law. One core has ~12–16 fill buffers, so bytes in flight ÷ ~90 ns latency caps it at
≈ 11 GB/s plus prefetch. More cores means more misses in flight.

**Q: Why is DRAM write slower than read?** Write-allocate: a store to an uncached line first
reads it (RFO), then writes it back later. That's about 2× the traffic. Non-temporal stores
would avoid it.

**Q: Why pin threads one per physical core?** vCPUs 0 and 1 are SMT siblings sharing L1/L2.
CPUs 0–3 would be only 2 cores.

### Collector

**Q: How do you test hardware code without hardware?** Every path goes through
`rpath(root, "/sys/...")`. Tests point `root` at fake trees: healthy, degraded (x8 NIC,
CE = 37 / UE = 1, 97 °C) and minimal (almost nothing). External tools are tested by
monkeypatching and with fixture outputs.

**Q: What happens on a VM?** Thermal, PCIe and ECC return
`{"available": false, "reason": ...}`. The snapshot still validates, and the health report
says "not evaluated", never "pass".

**Q: How do you detect a downtrained PCIe link?** Compare `current_link_speed/width` with
`max_link_speed/width` in sysfs. Caveat: GPUs down-clock at idle, so the rule only looks at
NICs and NVMe drives.

**Q: CE vs UE?** A CE is a single-bit error that ECC corrected; a rising CE *rate* predicts
failure. A UE is a detected but uncorrectable error: data loss, so the host is drained and the
DIMM replaced.

**Q: Why hash the hostname?** Hostnames leak datacenter, rack and team naming. A truncated
SHA-256 is stable and unique enough for a fleet.

**Q: What bug did you find in your own measurement?** The collector's sampler thread was on
CPU 0's hyperthread sibling and inflated L1 from 1.30 to 1.79 ns. The fix: keep the agent off
the measured core's *physical* core.

### Fleet / validator

**Q: Why median/MAD instead of mean/std?** Faulty hosts drag the mean toward themselves and
inflate the std, which masks them. The median and MAD barely move. 1.4826 × MAD estimates σ
for Gaussian data.

**Q: Why per SKU?** Different hardware has different normals: sku-b's DRAM is 1.2× sku-a's
by design.

**Q: How do you avoid fooling yourself in evaluation?**
- Dev seeds (101+) for building.
- Seed 1 for tuning (1000 hosts, so each fault type has examples).
- Seed 2 held out for the headline, plus 3–11 for variance.
- Thresholds frozen in `reports/thresholds.json`.
- The validator never reads ground truth, enforced by an AST test.

**Q: How do you handle a single glitched measurement?** The probe reports 3 samples and the
validator takes the median, so one bad run can't move it (tested).

**Q: How does root-cause attribution work?** Rules name their cause directly. Perf anomalies
are named by pattern: DRAM + bandwidth bad with normal caches → slow DIMM config; clock low
with slow L1/L2 → throttling; several mild shifts → subtle degradation. It also explains away:
UE subsumes CE, and throttling subsumes slow caches.

**Q: What are the failure modes?**
- SUBTLE hosts with only small shifts are missed: 2 of 5 on seed 2.
- A second fault hidden behind throttling.
- Isolation Forest false positives on small SKUs: the robustness seeds show combined
  precision 0.960 ± 0.040, below seed 2's 1.000.

**Q: Precision vs recall: which matters more here?** Recall for UE (data corruption); precision
for perf warnings (each false positive drains capacity and wastes technician time). That's why
UE is critical and perf causes are warnings with a "re-probe first" action.

---

## What I'd do with more time

- **Real data.** Run the collector on bare-metal servers (EDAC, PCIe, sensors exposed) and
  collect a few weeks of CE history.
- **Probe rigour.** Count only the timed region with perf (`--control` / libpfm). Add an
  `rdtsc`-based timer. Use non-temporal stores for write bandwidth. Probe each NUMA node
  (local vs remote DRAM).
- **Loaded-latency curves.** Latency vs injected bandwidth (as Intel MLC does) to see queueing
  at the memory controller.
- **Validator.** Use time series instead of single snapshots (drift detection), fleet-wide
  correlation (rack or batch effects), and per-feature SHAP-style explanations for ML flags.
- **Burn-in integration.** stress-ng, memtester and fio results as snapshot sections; a
  pass/fail gate before production.
- **Operations.** A collector daemon with a central store, ticket creation with evidence, and
  repair outcomes fed back as labels.
