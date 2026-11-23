"""Cache topology reader: /sys/devices/system/cpu/cpu0/cache/index*/.

Each ``indexN`` directory describes one cache seen by cpu0: its level (1/2/3),
type (Data / Instruction / Unified), size, line size, associativity, and which
CPUs share it (``shared_cpu_list``; e.g. L3 is usually shared by every core).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import is_real_root, parse_size, read_int, read_text, rpath, run_json_tool, unavailable

CACHE_DIR = "/sys/devices/system/cpu/cpu0/cache"


def _read_index(root: str | Path, index_dir: str) -> dict[str, Any]:
    """Read one indexN directory into a dict."""
    base = f"{CACHE_DIR}/{index_dir}"
    return {
        "level": read_int(root, f"{base}/level"),
        "type": read_text(root, f"{base}/type"),
        "size_bytes": parse_size(read_text(root, f"{base}/size")),
        "line_bytes": read_int(root, f"{base}/coherency_line_size"),
        "ways": read_int(root, f"{base}/ways_of_associativity"),
        "shared_cpu_list": read_text(root, f"{base}/shared_cpu_list"),
    }


def _from_lscpu() -> dict[str, Any]:
    """Fallback when sysfs cache info is missing: ``lscpu -J`` sizes (totals only)."""
    data, reason = run_json_tool(["lscpu", "-J"])
    if data is None:
        return unavailable(f"no cache sysfs and lscpu unavailable ({reason})")
    names = {"L1d cache": (1, "Data"), "L1i cache": (1, "Instruction"),
             "L2 cache": (2, "Unified"), "L3 cache": (3, "Unified")}
    levels = []
    for entry in data.get("lscpu", []):
        key = entry.get("field", "").rstrip(":")
        if key in names:
            level, ctype = names[key]
            # lscpu reports the *total* across instances, e.g. "192 KiB (4 instances)";
            # divide to get a per-core figure comparable with sysfs.
            raw = str(entry.get("data", ""))
            total = parse_size(raw.split("(")[0])
            n = 1
            if "instance" in raw:
                try:
                    n = int(raw.split("(")[1].split()[0])
                except (IndexError, ValueError):
                    n = 1
            size = total // n if total else None
            levels.append({"level": level, "type": ctype, "size_bytes": size,
                           "line_bytes": None, "ways": None, "shared_cpu_list": None})
    if not levels:
        return unavailable("lscpu returned no cache fields")
    return {"available": True, "source": "lscpu", "levels": levels}


def read_cache(root: str | Path = "/") -> dict[str, Any]:
    """Return per-level cache info for cpu0, sorted by level."""
    try:
        cache_dir = rpath(root, CACHE_DIR)
        indices = sorted(p.name for p in cache_dir.glob("index*")) if cache_dir.is_dir() else []
        if not indices:
            if is_real_root(root):
                return _from_lscpu()
            return unavailable(f"{CACHE_DIR} not present")
        levels = [_read_index(root, idx) for idx in indices]
        levels.sort(key=lambda c: (c["level"] or 0, c["type"] or ""))
        return {"available": True, "source": "sysfs", "levels": levels}
    except Exception as exc:  # noqa: BLE001 — a reader must never raise
        return unavailable(f"cache reader error: {exc}")


def data_cache_sizes(cache: dict[str, Any]) -> dict[str, int]:
    """Reduce a cache reading to {"L1": bytes, "L2": bytes, "L3": bytes} for data caches.

    Instruction caches are skipped: the pointer chase only exercises data loads.
    """
    out: dict[str, int] = {}
    if not cache.get("available"):
        return out
    for c in cache["levels"]:
        if c.get("type") == "Instruction" or not c.get("level") or not c.get("size_bytes"):
            continue
        out[f"L{c['level']}"] = int(c["size_bytes"])
    return out
