# SiliconScope top-level Makefile (phase 0 placeholders; filled in per phase).
# Override the interpreter with:  make test PYTHON=/path/to/venv/bin/python
PYTHON ?= python3

.PHONY: all probe collect fleet evaluate report test lint clean

all: probe collect fleet evaluate report

probe:
	@echo "probe: not implemented yet (phase 1)"

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
