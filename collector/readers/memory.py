"""Memory reader: /proc/meminfo totals + DIMM details from ``dmidecode -t memory``.

dmidecode reads the SMBIOS tables the firmware publishes. It needs root, and in
VMs the tables describe virtual DIMMs (or nothing useful). The interesting field
for fleet health is **configured speed vs rated speed**: a DIMM running below
its rated speed (BIOS setting, mixed DIMMs, training fallback) costs bandwidth.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .common import is_real_root, read_text, run_tool, unavailable


def parse_meminfo(text: str) -> dict[str, int]:
    """/proc/meminfo lines like ``MemTotal:  3897416 kB`` -> {"MemTotal": bytes}."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"^(\w+):\s+(\d+)(?:\s+kB)?", line)
        if m:
            out[m.group(1)] = int(m.group(2)) * (1024 if "kB" in line else 1)
    return out


def _speed(value: str | None) -> int | None:
    """'3200 MT/s' or '3200 MHz' -> 3200; 'Unknown' -> None."""
    if not value:
        return None
    m = re.match(r"(\d+)", value)
    return int(m.group(1)) if m else None


def _size_bytes(value: str | None) -> int | None:
    """'16 GB' / '16384 MB' -> bytes; 'No Module Installed' -> None."""
    if not value:
        return None
    m = re.match(r"(\d+)\s*(KB|MB|GB|TB)", value)
    if not m:
        return None
    return int(m.group(1)) << {"KB": 10, "MB": 20, "GB": 30, "TB": 40}[m.group(2)]


def parse_dmidecode(text: str) -> list[dict[str, Any]]:
    """Parse ``dmidecode -t memory`` output into one dict per populated DIMM slot."""
    modules = []
    for block in text.split("\n\n"):
        if "Memory Device" not in block:
            continue
        fields = {}
        for line in block.splitlines():
            if ":" in line and line.startswith("\t") and not line.startswith("\t\t"):
                k, _, v = line.strip().partition(":")
                fields[k.strip()] = v.strip()
        size = _size_bytes(fields.get("Size"))
        if size is None:
            continue  # empty slot
        rated = _speed(fields.get("Speed"))
        configured = _speed(fields.get("Configured Memory Speed")
                            or fields.get("Configured Clock Speed"))
        modules.append({
            "locator": fields.get("Locator"),
            "size_bytes": size,
            "type": fields.get("Type"),
            "rated_speed_mts": rated,
            "configured_speed_mts": configured,
            "below_rated_speed": bool(rated and configured and configured < rated),
            "manufacturer": fields.get("Manufacturer"),
        })
    return modules


def read_dimms(root: str | Path) -> dict[str, Any]:
    if not is_real_root(root):
        return unavailable("dmidecode is only run against the live system")
    if os.geteuid() != 0:
        return unavailable("dmidecode needs root (run the collector with sudo for DIMM info)")
    out, reason = run_tool(["dmidecode", "-t", "memory"])
    if out is None:
        return unavailable(reason)
    modules = parse_dmidecode(out)
    if not modules:
        return unavailable("dmidecode reported no populated DIMMs (typical in VMs)")
    return {"available": True, "modules": modules,
            "any_below_rated_speed": any(m["below_rated_speed"] for m in modules)}


def read_memory(root: str | Path = "/") -> dict[str, Any]:
    """MemTotal / MemAvailable plus DIMM info when possible."""
    try:
        text = read_text(root, "/proc/meminfo")
        if text is None:
            return unavailable("/proc/meminfo not readable")
        info = parse_meminfo(text)
        return {"available": True,
                "mem_total_bytes": info.get("MemTotal"),
                "mem_available_bytes": info.get("MemAvailable"),
                "dimms": read_dimms(root)}
    except Exception as exc:  # noqa: BLE001
        return unavailable(f"memory reader error: {exc}")
