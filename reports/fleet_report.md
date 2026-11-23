# Fleet triage report (SIMULATED fleet)

> All hosts in this report are **SIMULATED** (`fleet/simulator.py`, `data/fleet_2`). The sku-a baseline is seeded from real measurements of the dev machine.

**200 hosts validated — 27 flagged** (5 critical, 22 warning).

Thresholds (tuned on seed 1 only): `ce_slope_per_day=0.5`, `ce_total=10`, `temp_c=86.0`, `freq_ratio=0.9`, `z_abs=4.0`, `if_score=0.59`

## Summary by cause

| Cause | Hosts |
|---|---:|
| THERMAL_THROTTLING | 7 |
| PCIE_LINK_DOWNTRAIN | 6 |
| MEMORY_UE | 5 |
| MEMORY_CONFIG_SLOW | 3 |
| SUBTLE_PERF_DEGRADATION | 3 |
| MEMORY_ECC_DEGRADATION | 3 |

## Triage list (worst first)

### 1. `5ae29a64d41d` (sku-a) — **CRITICAL**: MEMORY_UE

- **Evidence:** UE count = 2
- **Action:** Drain host; replace the DIMM (EDAC per-DIMM counts identify the slot); run memtester/memtest86 before returning to service.
- Rules fired: `ecc_ue`; ML flag: False (score 0.4473)

### 2. `6ea67fe61c27` (sku-a) — **CRITICAL**: MEMORY_UE

- **Evidence:** UE count = 1
- **Action:** Drain host; replace the DIMM (EDAC per-DIMM counts identify the slot); run memtester/memtest86 before returning to service.
- Rules fired: `ecc_ue`; ML flag: False (score 0.4469)

### 3. `7cc37cfcd6d6` (sku-a) — **CRITICAL**: MEMORY_UE

- **Evidence:** UE count = 1
- **Action:** Drain host; replace the DIMM (EDAC per-DIMM counts identify the slot); run memtester/memtest86 before returning to service.
- Rules fired: `ecc_ue`; ML flag: False (score 0.4258)

### 4. `86e97e172a79` (sku-b) — **CRITICAL**: MEMORY_UE

- **Evidence:** UE count = 1
- **Action:** Drain host; replace the DIMM (EDAC per-DIMM counts identify the slot); run memtester/memtest86 before returning to service.
- Rules fired: `ecc_ue`; ML flag: False (score 0.373)

### 5. `b9b3657e3945` (sku-a) — **CRITICAL**: MEMORY_UE

- **Evidence:** UE count = 1
- **Action:** Drain host; replace the DIMM (EDAC per-DIMM counts identify the slot); run memtester/memtest86 before returning to service.
- Rules fired: `ecc_ue`; ML flag: False (score 0.3778)

### 6. `a2690b711ff2` (sku-a) — **WARNING**: THERMAL_THROTTLING

- **Evidence:** loaded temp = 95.1 °C (>86.0), loaded freq = 2674 MHz = 0.70x SKU median (<0.9)
- **Action:** Check fans and fan curves, heatsink seating/paste, airflow and blanking panels; compare with rack inlet temperature.
- Rules fired: `perf_robust_z;thermal_throttle`; ML flag: True (score 0.7927)

### 7. `653a29c979cd` (sku-a) — **WARNING**: THERMAL_THROTTLING

- **Evidence:** loaded temp = 95.4 °C (>86.0), loaded freq = 2834 MHz = 0.75x SKU median (<0.9)
- **Action:** Check fans and fan curves, heatsink seating/paste, airflow and blanking panels; compare with rack inlet temperature.
- Rules fired: `perf_robust_z;thermal_throttle`; ML flag: True (score 0.7526)

### 8. `fdece85d1ed6` (sku-c) — **WARNING**: THERMAL_THROTTLING

- **Evidence:** loaded temp = 97.8 °C (>86.0), loaded freq = 2680 MHz = 0.79x SKU median (<0.9)
- **Action:** Check fans and fan curves, heatsink seating/paste, airflow and blanking panels; compare with rack inlet temperature.
- Rules fired: `perf_robust_z;thermal_throttle`; ML flag: True (score 0.7573)

### 9. `00c18b59b356` (sku-c) — **WARNING**: MEMORY_CONFIG_SLOW

- **Evidence:** read_gbps = 11.53 GB/s vs SKU median 17.25 (robust z = +10.7) ; IsolationForest score 0.720 (>= 0.59); top deviations: read_gbps z=+10.7, dram_ns z=+2.8, l2_ns z=+1.3
- **Action:** Verify DIMM speed and population in BIOS/SPD (dmidecode 'Configured Memory Speed' vs rated); fix mixed DIMMs.
- Rules fired: `perf_robust_z`; ML flag: True (score 0.7204)

### 10. `f9de4463ea7a` (sku-c) — **WARNING**: THERMAL_THROTTLING

- **Evidence:** loaded temp = 96.7 °C (>86.0), loaded freq = 2798 MHz = 0.83x SKU median (<0.9)
- **Action:** Check fans and fan curves, heatsink seating/paste, airflow and blanking panels; compare with rack inlet temperature.
- Rules fired: `perf_robust_z;thermal_throttle`; ML flag: True (score 0.7588)

### 11. `58367b7d78e5` (sku-b) — **WARNING**: THERMAL_THROTTLING

- **Evidence:** loaded temp = 92.8 °C (>86.0), loaded freq = 2417 MHz = 0.81x SKU median (<0.9)
- **Action:** Check fans and fan curves, heatsink seating/paste, airflow and blanking panels; compare with rack inlet temperature.
- Rules fired: `perf_robust_z;thermal_throttle`; ML flag: True (score 0.8312)

### 12. `0a7b3e3dc0f0` (sku-a) — **WARNING**: THERMAL_THROTTLING

- **Evidence:** loaded temp = 93.8 °C (>86.0), loaded freq = 3026 MHz = 0.80x SKU median (<0.9)
- **Action:** Check fans and fan curves, heatsink seating/paste, airflow and blanking panels; compare with rack inlet temperature.
- Rules fired: `perf_robust_z;thermal_throttle`; ML flag: True (score 0.7432)

### 13. `6edf27d78a8d` (sku-a) — **WARNING**: THERMAL_THROTTLING

- **Evidence:** loaded temp = 92.4 °C (>86.0), loaded freq = 3105 MHz = 0.82x SKU median (<0.9)
- **Action:** Check fans and fan curves, heatsink seating/paste, airflow and blanking panels; compare with rack inlet temperature.
- Rules fired: `perf_robust_z;thermal_throttle`; ML flag: True (score 0.7329)

### 14. `0a71e261439b` (sku-b) — **WARNING**: MEMORY_CONFIG_SLOW

- **Evidence:** dram_ns = 127.46 ns vs SKU median 112.19 (robust z = +4.8) ; read_gbps = 8.79 GB/s vs SKU median 11.60 (robust z = +7.5) ; IsolationForest score 0.730 (>= 0.59); top deviations: read_gbps z=+7.5, dram_ns z=+4.8, l3_ns z=-0.7
- **Action:** Verify DIMM speed and population in BIOS/SPD (dmidecode 'Configured Memory Speed' vs rated); fix mixed DIMMs.
- Rules fired: `perf_robust_z`; ML flag: True (score 0.7296)

### 15. `0591b130ab68` (sku-a) — **WARNING**: MEMORY_CONFIG_SLOW

- **Evidence:** dram_ns = 107.01 ns vs SKU median 93.89 (robust z = +4.2) ; read_gbps = 10.72 GB/s vs SKU median 13.68 (robust z = +7.0) ; IsolationForest score 0.693 (>= 0.59); top deviations: read_gbps z=+7.0, dram_ns z=+4.2, l2_ns z=+0.7
- **Action:** Verify DIMM speed and population in BIOS/SPD (dmidecode 'Configured Memory Speed' vs rated); fix mixed DIMMs.
- Rules fired: `perf_robust_z`; ML flag: True (score 0.6927)

### 16. `98f0b170e183` (sku-b) — **WARNING**: SUBTLE_PERF_DEGRADATION

- **Evidence:** l2_ns = 6.22 ns vs SKU median 5.40 (robust z = +5.3) ; IsolationForest score 0.665 (>= 0.59); top deviations: l2_ns z=+5.3, l3_ns z=+3.6, dram_ns z=-1.7
- **Action:** Diff BIOS/firmware/microcode against the SKU golden config; run burn-in (stress-ng) and re-probe.
- Rules fired: `perf_robust_z`; ML flag: True (score 0.6654)

### 17. `b91eb5275fa3` (sku-a) — **WARNING**: MEMORY_ECC_DEGRADATION

- **Evidence:** CE slope = +1.58/day (>0.5), 14-day total = 167 (>10?), last 3 days = [19, 17, 20]
- **Action:** Schedule DIMM replacement; run memtester; watch for UEs until replaced.
- Rules fired: `ecc_ce_trend`; ML flag: False (score 0.4331)

### 18. `1867570e21a1` (sku-a) — **WARNING**: PCIE_LINK_DOWNTRAIN

- **Evidence:** nic 0000-17-00.0: x8 @ 16.0 GT/s (capable of x16 @ 16.0 GT/s)
- **Action:** Reseat the card, inspect riser/cable and connector, retrain the link (hot reset / reboot); RMA the card or riser if it persists.
- Rules fired: `pcie_downtrain`; ML flag: False (score 0.3787)

### 19. `64fa7d8cf497` (sku-c) — **WARNING**: PCIE_LINK_DOWNTRAIN

- **Evidence:** nic 0000-17-00.0: x8 @ 16.0 GT/s (capable of x16 @ 16.0 GT/s)
- **Action:** Reseat the card, inspect riser/cable and connector, retrain the link (hot reset / reboot); RMA the card or riser if it persists.
- Rules fired: `pcie_downtrain`; ML flag: False (score 0.4484)

### 20. `94f092c59fdb` (sku-c) — **WARNING**: PCIE_LINK_DOWNTRAIN

- **Evidence:** nic 0000-17-00.0: x8 @ 16.0 GT/s (capable of x16 @ 16.0 GT/s)
- **Action:** Reseat the card, inspect riser/cable and connector, retrain the link (hot reset / reboot); RMA the card or riser if it persists.
- Rules fired: `pcie_downtrain`; ML flag: False (score 0.3881)

### 21. `d8d8e86f5b77` (sku-c) — **WARNING**: PCIE_LINK_DOWNTRAIN

- **Evidence:** nic 0000-17-00.0: x8 @ 16.0 GT/s (capable of x16 @ 16.0 GT/s)
- **Action:** Reseat the card, inspect riser/cable and connector, retrain the link (hot reset / reboot); RMA the card or riser if it persists.
- Rules fired: `pcie_downtrain`; ML flag: False (score 0.4024)

### 22. `dbec328d41b9` (sku-b) — **WARNING**: PCIE_LINK_DOWNTRAIN

- **Evidence:** nic 0000-17-00.0: x8 @ 16.0 GT/s (capable of x16 @ 16.0 GT/s)
- **Action:** Reseat the card, inspect riser/cable and connector, retrain the link (hot reset / reboot); RMA the card or riser if it persists.
- Rules fired: `pcie_downtrain`; ML flag: False (score 0.3958)

### 23. `ebae70773552` (sku-a) — **WARNING**: PCIE_LINK_DOWNTRAIN

- **Evidence:** nic 0000-17-00.0: x8 @ 16.0 GT/s (capable of x16 @ 16.0 GT/s)
- **Action:** Reseat the card, inspect riser/cable and connector, retrain the link (hot reset / reboot); RMA the card or riser if it persists.
- Rules fired: `pcie_downtrain`; ML flag: False (score 0.4038)

### 24. `3b5892468684` (sku-b) — **WARNING**: MEMORY_ECC_DEGRADATION

- **Evidence:** CE slope = +0.26/day (>0.5), 14-day total = 34 (>10?), last 3 days = [1, 3, 3]
- **Action:** Schedule DIMM replacement; run memtester; watch for UEs until replaced.
- Rules fired: `ecc_ce_trend`; ML flag: False (score 0.4141)

### 25. `3d941a0c7fa4` (sku-b) — **WARNING**: MEMORY_ECC_DEGRADATION

- **Evidence:** CE slope = +0.20/day (>0.5), 14-day total = 33 (>10?), last 3 days = [3, 1, 4]
- **Action:** Schedule DIMM replacement; run memtester; watch for UEs until replaced.
- Rules fired: `ecc_ce_trend`; ML flag: False (score 0.3698)

### 26. `36f65d401819` (sku-a) — **WARNING**: SUBTLE_PERF_DEGRADATION

- **Evidence:** IsolationForest score 0.634 (>= 0.59); top deviations: l1_ns z=+3.1, dram_ns z=+2.4, read_gbps z=+2.4
- **Action:** Diff BIOS/firmware/microcode against the SKU golden config; run burn-in (stress-ng) and re-probe.
- Rules fired: `-`; ML flag: True (score 0.634)

### 27. `7004f8df163d` (sku-a) — **WARNING**: SUBTLE_PERF_DEGRADATION

- **Evidence:** IsolationForest score 0.631 (>= 0.59); top deviations: l3_ns z=+3.5, l1_ns z=-1.9, l2_ns z=+1.9
- **Action:** Diff BIOS/firmware/microcode against the SKU golden config; run burn-in (stress-ng) and re-probe.
- Rules fired: `-`; ML flag: True (score 0.631)

