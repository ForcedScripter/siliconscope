"""Thermal reader: /sys/class/thermal zones and /sys/class/hwmon sensors.

Both report millidegrees Celsius (``45000`` = 45.0 °C). thermal_zone covers
ACPI/SoC zones (e.g. ``x86_pkg_temp`` = CPU package); hwmon covers driver
sensors (``coretemp`` per-core, ``nvme`` drive, ``k10temp`` on AMD). VMs usually
expose neither, because the hypervisor owns the physical sensors.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import read_int, read_text, rpath, unavailable


def _zones(root: str | Path) -> list[dict[str, Any]]:
    base = rpath(root, "/sys/class/thermal")
    zones = []
    for z in sorted(base.glob("thermal_zone*")) if base.is_dir() else []:
        milli = read_int(root, f"/sys/class/thermal/{z.name}/temp")
        zones.append({"name": z.name, "kind": "thermal_zone",
                      "type": read_text(root, f"/sys/class/thermal/{z.name}/type"),
                      "temp_c": milli / 1000 if milli is not None else None})
    return zones


def _hwmon(root: str | Path) -> list[dict[str, Any]]:
    base = rpath(root, "/sys/class/hwmon")
    sensors = []
    for h in sorted(base.glob("hwmon*")) if base.is_dir() else []:
        chip = read_text(root, f"/sys/class/hwmon/{h.name}/name") or h.name
        for t in sorted(h.glob("temp*_input")):
            idx = t.name.split("_")[0]  # "temp1"
            milli = read_int(root, f"/sys/class/hwmon/{h.name}/{t.name}")
            label = read_text(root, f"/sys/class/hwmon/{h.name}/{idx}_label") or idx
            sensors.append({"name": f"{chip}/{label}", "kind": "hwmon", "type": chip,
                            "temp_c": milli / 1000 if milli is not None else None})
    return sensors


def read_thermal(root: str | Path = "/") -> dict[str, Any]:
    """All temperature sensors and the hottest reading."""
    try:
        zones = _zones(root) + _hwmon(root)
        if not zones:
            return unavailable("no thermal_zone or hwmon temperature sensors "
                               "(common in VMs: the host owns the sensors)")
        temps = [z["temp_c"] for z in zones if z["temp_c"] is not None]
        return {"available": True, "zones": zones, "max_temp_c": max(temps) if temps else None}
    except Exception as exc:  # noqa: BLE001
        return unavailable(f"thermal reader error: {exc}")
