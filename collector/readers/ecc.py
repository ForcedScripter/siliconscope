"""ECC reader: EDAC memory-controller error counters.

ECC memory stores extra check bits with every word. On a read, the memory
controller can:
  * **correct** a single-bit error -> a *CE* (correctable error). The program
    never notices, but the count is a health signal: a DIMM whose CE rate keeps
    climbing is wearing out and will eventually throw an...
  * **UE** (uncorrectable error): multi-bit corruption it can only detect. The
    kernel usually kills the affected process or panics. One UE = replace the part.

Linux's EDAC subsystem exposes the counters per memory controller:
``/sys/devices/system/edac/mc/mc<N>/ce_count`` and ``ue_count``.
Laptops (non-ECC RAM) and VMs normally have no EDAC driver at all.

The collector takes one reading; *trends* (``ce_daily``) need history, so the
collector leaves ``ce_daily`` null and the fleet simulator fills 14 days.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import read_int, rpath, unavailable

EDAC_DIR = "/sys/devices/system/edac/mc"


def read_ecc(root: str | Path = "/") -> dict[str, Any]:
    """CE/UE counts per memory controller plus totals."""
    try:
        base = rpath(root, EDAC_DIR)
        mcs = sorted(p.name for p in base.glob("mc[0-9]*")) if base.is_dir() else []
        if not mcs:
            return unavailable("EDAC not present (no ECC memory controller driver; "
                               "normal for laptops and VMs)")
        controllers = []
        for mc in mcs:
            ce = read_int(root, f"{EDAC_DIR}/{mc}/ce_count")
            ue = read_int(root, f"{EDAC_DIR}/{mc}/ue_count")
            controllers.append({"mc": mc, "ce_count": ce or 0, "ue_count": ue or 0,
                                "counts_readable": ce is not None and ue is not None})
        return {"available": True, "controllers": controllers,
                "ce_total": sum(c["ce_count"] for c in controllers),
                "ue_total": sum(c["ue_count"] for c in controllers),
                "ce_daily": None}
    except Exception as exc:  # noqa: BLE001
        return unavailable(f"ecc reader error: {exc}")
