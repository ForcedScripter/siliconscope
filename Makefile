# SiliconScope top-level Makefile (phase 0 placeholders; filled in per phase).
# Override the interpreter with:  make test PYTHON=/path/to/venv/bin/python
PYTHON ?= python3
# host_id = first 12 hex of sha256(hostname); never write the raw hostname to reports/.
HOST_ID := $(shell printf '%s' "$$(hostname)" | sha256sum | cut -c1-12)
MAX_MB  ?= 256
REPEATS ?= 5

.PHONY: all probe collect fleet evaluate report test lint clean

all: probe collect fleet evaluate report

# Build the C++ probes, run the latency sweep, plot it and detect cache levels.
probe:
	$(MAKE) -C probe
	mkdir -p reports
	./probe/build/latency --max-mb $(MAX_MB) --repeats $(REPEATS) > reports/latency_$(HOST_ID).csv
	$(PYTHON) -m scripts.plot_latency reports/latency_$(HOST_ID).csv

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
