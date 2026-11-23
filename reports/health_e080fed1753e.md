# Health report — host e080fed1753e

Source: `collector` snapshot collected 2026-11-23T13:12:15+00:00. Platform: Ubuntu 26.04 LTS / kernel 6.6.114.1-microsoft-standard-WSL2 / virtualization: wsl.

| Check | Status | Detail |
|---|---|---|
| ECC uncorrectable errors | not evaluated | ecc unavailable: EDAC not present (no ECC memory controller driver; normal for laptops and VMs) |
| ECC correctable-error trend | not evaluated | ecc unavailable: EDAC not present (no ECC memory controller driver; normal for laptops and VMs) |
| PCIe link training | not evaluated | pcie unavailable: no PCIe devices expose link speed/width (typical in VMs: devices are virtual/paravirtual) |
| Thermal throttling | not evaluated | thermal unavailable: no thermal_zone or hwmon temperature sensors (common in VMs: the host owns the sensors) |
| l1_ns | pass | 1.30 vs reference 1.30 (-0%; tolerance ±15%) |
| l2_ns | pass | 3.68 vs reference 4.74 (-22%; tolerance ±15%) |
| l3_ns | pass | 15.05 vs reference 14.05 (+7%; tolerance ±15%) |
| dram_ns | pass | 94.79 vs reference 94.05 (+1%; tolerance ±15%) |
| read_gbps | pass | 15.98 vs reference 13.73 (+16%; tolerance ±15%) |

Reference baseline: `sku-a` from reports/sku_baselines.json (sku-a perf = this project's full canonical probe run). The collector runs a *quick* probe (fewer sizes and repeats), so deviations within a few percent are measurement noise.

