# Architecture

```mermaid
flowchart LR
    subgraph real["REAL (this machine)"]
        P["probe/ (C++)<br/>latency.cpp · bandwidth.cpp"]
        S["scripts/<br/>plot_latency · plot_bandwidth · perf_tlb"]
        C["collector/<br/>8 sysfs/procfs readers + quick probe"]
        P -- CSV --> S
        S -- "latency_levels.json<br/>bandwidth_summary.json" --> R1[("reports/")]
        P -- quick run --> C
        C -- "snapshot.json (schema v1)" --> R1
    end
    subgraph sim["SIMULATED"]
        SIM["fleet/simulator.py<br/>+ faults.py"]
        V["fleet/validator.py<br/>rules.py → Isolation Forest → attribution"]
        E["fleet/evaluate.py<br/>tune seed 1 · test seed 2 · ablation"]
        GT[("ground_truth.csv")]
        SIM -- "snapshots/*.json (same schema)" --> V
        SIM --> GT
        V -- fleet_results.csv --> E
        GT --> E
    end
    R1 -- "sku-a baseline (real numbers)" --> SIM
    R1 -- "real snapshot" --> V
    V -- "fleet_report.md · health_<host>.md · plots" --> R2[("reports/")]
    E -- "evaluation.md · confusion_matrix.png" --> R2
```

## Data flow in one paragraph

The C++ **probes** measure this machine's memory hierarchy. The **plot scripts** turn the
CSVs into detected cache levels and bandwidth summaries in `reports/`. The **collector** reads
Linux health interfaces (plus a quick probe) into one schema-v1 **snapshot**. The **simulator**
seeds `sku-a` from the real `reports/` numbers and writes hundreds of SIMULATED snapshots in
the *same* schema, with ground truth kept in a separate file. The **validator** reads only
snapshots: deterministic rules first, then a per-SKU Isolation Forest, then root-cause
attribution with severity and action. **evaluate** is the only component that reads ground
truth. It tunes on seed 1 and scores held-out seed 2 (plus seeds 3–11).

## Why the schema is the contract

`collector/schema.py` is imported by both sides. The collector validates before writing; the
simulator calls `assert_valid` on every host; tests check both. The validator can therefore run
unchanged on a real machine (`make report`) or a simulated fleet (`make fleet`). In a real
deployment the simulator would simply be replaced by per-host agents.
