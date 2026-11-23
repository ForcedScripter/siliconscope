# TLB experiment (real measurement, `make perf`)

| size (MB) | page | ns/hop | dTLB misses/hop | LLC misses/hop |
|---:|---|---:|---:|---:|
| 1 | 4KiB | 6.47 | 0.001 | 0.005 |
| 1 | 2MiB | 5.56 | 0.000 | 0.005 |
| 2 | 4KiB | 18.51 | 0.002 | 0.039 |
| 2 | 2MiB | 14.36 | 0.001 | 0.023 |
| 4 | 4KiB | 29.14 | 0.003 | 0.262 |
| 4 | 2MiB | 24.04 | 0.010 | 0.352 |
| 6 | 4KiB | 60.13 | 0.035 | 1.051 |
| 6 | 2MiB | 48.12 | 0.025 | 0.805 |
| 16 | 4KiB | 90.51 | 0.552 | 1.753 |
| 16 | 2MiB | 91.72 | 0.559 | 1.725 |
| 64 | 4KiB | 119.52 | 0.891 | 1.949 |
| 64 | 2MiB | 97.13 | 0.890 | 1.897 |

Per-hop counts include the probe's setup (building the cycle), so they slightly overstate the chase itself — equally for both page sizes.
