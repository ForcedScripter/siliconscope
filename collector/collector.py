"""SiliconScope health collector.

Usage:
    python -m collector.collector --out snapshot.json [--root /] [--with-probe]

Runs every reader, assembles one schema-v1 snapshot (see ``collector/schema.py``),
validates it and writes JSON. Each reader degrades to
``{"available": false, "reason": ...}`` on missing hardware, so this works the
same on bare metal, in a VM, in CI, or against a fake sysfs tree (``--root``).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from collector.readers.cache import read_cache
from collector.readers.common import unavailable
from collector.readers.cpu import read_cpu
from collector.readers.ecc import read_ecc
from collector.readers.memory import read_memory
from collector.readers.pcie import read_pcie
from collector.readers.platform import read_platform
from collector.readers.storage import read_storage
from collector.readers.thermal import read_thermal
from collector.schema import SCHEMA_VERSION, validate

READERS = {
    "platform": read_platform, "cpu": read_cpu, "cache": read_cache, "memory": read_memory,
    "thermal": read_thermal, "pcie": read_pcie, "ecc": read_ecc, "storage": read_storage,
}


def collect(root: str | Path = "/", with_probe: bool = False,
            now: datetime | None = None) -> dict[str, Any]:
    """Build a snapshot dict for the machine at ``root``."""
    sections = {name: fn(root) for name, fn in READERS.items()}
    if with_probe:
        from collector.readers.probe import read_probe
        sections["probe"] = read_probe(root)
    else:
        sections["probe"] = unavailable("not requested (use --with-probe)")
    return {
        "schema_version": SCHEMA_VERSION,
        "host_id": sections["platform"].get("host_id", "unknown"),
        "collected_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
        "source": "collector",
        "sku": None,
        **sections,
    }


def _status_line(name: str, sec: dict[str, Any]) -> str:
    status = "ok" if sec.get("available") else f"unavailable: {sec.get('reason', '')}"
    return f"  {name:<9} {status}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Collect a hardware-health snapshot.")
    ap.add_argument("--out", type=Path, required=True, help="where to write snapshot JSON")
    ap.add_argument("--root", default="/", help="prefix for /sys and /proc (tests: fake tree)")
    ap.add_argument("--with-probe", action="store_true", help="also run quick memory probes")
    args = ap.parse_args(argv)

    snap = collect(args.root, args.with_probe)
    errors = validate(snap)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(snap, indent=2) + "\n")

    print(f"snapshot for host {snap['host_id']} -> {args.out}")
    for name in ["platform", "cpu", "cache", "memory", "thermal", "pcie", "ecc", "storage",
                 "probe"]:
        print(_status_line(name, snap[name]))
    if errors:
        print("SCHEMA ERRORS:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 1
    print(f"schema: valid (v{SCHEMA_VERSION})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
