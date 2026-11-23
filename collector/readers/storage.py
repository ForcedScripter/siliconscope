"""Storage reader: block devices from ``lsblk -J`` and SMART health from ``smartctl``.

SMART (Self-Monitoring, Analysis and Reporting Technology) is the drive's own
health report: an overall PASSED/FAILED verdict plus counters such as media
errors and percentage of endurance used. ``smartctl`` usually needs root and real
disks; in WSL/VMs the disks are virtual and SMART is unavailable.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .common import is_real_root, run_json_tool, unavailable


def _smart(dev: str) -> dict[str, Any]:
    if os.geteuid() != 0:
        return unavailable("smartctl needs root")
    data, reason = run_json_tool(["smartctl", "-H", "-j", f"/dev/{dev}"])
    if data is None:
        return unavailable(reason)
    status = data.get("smart_status", {})
    if "passed" not in status:
        return unavailable("device reports no SMART status (virtual disk?)")
    return {"available": True, "passed": bool(status["passed"])}


def read_storage(root: str | Path = "/") -> dict[str, Any]:
    """Whole-disk block devices with size/rotational/transport and SMART verdict."""
    try:
        if not is_real_root(root):
            return unavailable("lsblk/smartctl are only run against the live system")
        data, reason = run_json_tool(["lsblk", "-J", "-b", "-d", "-o",
                                      "NAME,TYPE,SIZE,ROTA,MODEL,TRAN"])
        if data is None:
            return unavailable(reason)
        devices = []
        for d in data.get("blockdevices", []):
            if d.get("type") != "disk":
                continue
            devices.append({"name": d.get("name"), "size_bytes": int(d.get("size") or 0),
                            "rotational": str(d.get("rota")) in ("1", "True", "true"),
                            "model": d.get("model"), "transport": d.get("tran"),
                            "smart": _smart(d.get("name", ""))})
        return {"available": True, "devices": devices}
    except Exception as exc:  # noqa: BLE001
        return unavailable(f"storage reader error: {exc}")
