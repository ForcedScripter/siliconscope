# SiliconScope top-level Makefile (phase 0 placeholders; filled in per phase).
# Override the interpreter with:  make test PYTHON=/path/to/venv/bin/python
PYTHON ?= python3
# host_id = first 12 hex of sha256(hostname); never write the raw hostname to reports/.
HOST_ID := $(shell printf '%s' "$$(hostname)" | sha256sum | cut -c1-12)
MAX_MB  ?= 256
REPEATS ?= 5

.PHONY: all probe perf collect fleet evaluate report test lint clean

all: probe collect fleet evaluate report

BW_MAX_MB ?= 1024
THREADS   ?= 4
LAT       := ./probe/build/latency --max-mb $(MAX_MB) --repeats $(REPEATS)
BW        := ./probe/build/bandwidth --max-mb $(BW_MAX_MB) --repeats $(REPEATS)

# Build the C++ probes and run every memory experiment on this machine:
#   latency (random, 4 KiB pages)  -> cache levels       [phase 1]
#   latency (random, huge pages)   -> TLB hypothesis      [phase 2]
#   latency (sequential)           -> prefetcher effect   [phase 2]
#   bandwidth (1 and N threads)    -> GB/s per level      [phase 2]
probe:
	$(MAKE) -C probe
	mkdir -p reports
	$(LAT) > reports/latency_$(HOST_ID).csv
	$(LAT) --hugepages > reports/latency_hugepages_$(HOST_ID).csv
	$(LAT) --pattern sequential > reports/latency_sequential_$(HOST_ID).csv
	$(BW) --threads 1 > reports/bandwidth_$(HOST_ID).csv
	$(BW) --threads $(THREADS) | tail -n +2 >> reports/bandwidth_$(HOST_ID).csv
	$(PYTHON) -m scripts.plot_latency reports/latency_$(HOST_ID).csv \
		--compare reports/latency_hugepages_$(HOST_ID).csv:"random, 2 MiB huge pages" \
		          reports/latency_sequential_$(HOST_ID).csv:"sequential (prefetcher on)"
	$(PYTHON) -m scripts.plot_bandwidth reports/bandwidth_$(HOST_ID).csv

# TLB experiment: perf counters at fixed sizes, 4 KiB vs 2 MiB pages.
# Exits cleanly (with an explanatory note) if perf counters aren't available.
perf:
	$(MAKE) -C probe
	$(PYTHON) -m scripts.perf_tlb

collect:
	@echo "collect: not implemented yet (phase 3)"

fleet:
	@echo "fleet: not implemented yet (phase 4)"

evaluate:
	@echo "evaluate: not implemented yet (phase 4)"

report:
	@echo "report: not implemented yet (phase 4)"

lint:
	$(PYTHON) -m ruff check .

# pytest exits 5 when no tests are collected; treat that as success in phase 0.
test:
	$(PYTHON) -m pytest -q || [ $$? -eq 5 ]

clean:
	rm -rf probe/build data .pytest_cache .ruff_cache
