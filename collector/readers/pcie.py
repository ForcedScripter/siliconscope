"""PCIe reader: link speed/width for every device in /sys/bus/pci/devices.

When a PCIe link comes up, the two ends *train*: they negotiate the highest
speed (generation) and width (lanes) both support. If a lane is damaged, a card
is badly seated or a riser is marginal, training falls back to fewer lanes
(x16 -> x8) or a lower generation (Gen4 16 GT/s -> Gen3 8 GT/s). The device
still works — just with half the bandwidth — which is why it's a classic silent
fleet issue. sysfs exposes both sides of the comparison:

    current_link_speed  "16.0 GT/s PCIe"   max_link_speed  "16.0 GT/s PCIe"
    current_link_width  "8"                max_link_width  "16"   -> downtrained

Caveat: some devices legitimately drop link speed at idle to save power (e.g.
GPUs), so a production rule should whitelist device classes or check under load.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .common import read_text, rpath, unavailable

# PCI class code prefixes -> a role name the fleet validator understands.
CLASS_ROLES = {"0x0200": "nic", "0x0108": "nvme", "0x0300": "gpu", "0x0302": "accelerator",
               "0x0604": "bridge", "0x0106": "sata"}

# GT/s per lane -> PCIe generation (for readable reports).
GEN_BY_SPEED = {2.5: 1, 5.0: 2, 8.0: 3, 16.0: 4, 32.0: 5, 64.0: 6}


def parse_speed(text: str | None) -> float | None:
    """'16.0 GT/s PCIe' -> 16.0; 'Unknown' / missing -> None."""
    if not text:
        return None
    m = re.match(r"([\d.]+)\s*GT/s", text)
    return float(m.group(1)) if m else None


def parse_width(text: str | None) -> int | None:
    """'16' or 'x16' -> 16; '0' (link down) stays 0."""
    if not text:
        return None
    m = re.search(r"(\d+)", text)
    return int(m.group(1)) if m else None


def _device(root: str | Path, bdf: str) -> dict[str, Any] | None:
    base = f"/sys/bus/pci/devices/{bdf}"
    cur_s = parse_speed(read_text(root, f"{base}/current_link_speed"))
    max_s = parse_speed(read_text(root, f"{base}/max_link_speed"))
    cur_w = parse_width(read_text(root, f"{base}/current_link_width"))
    max_w = parse_width(read_text(root, f"{base}/max_link_width"))
    if cur_s is None and max_s is None and cur_w is None and max_w is None:
        return None  # no link fields: root complex parts, virtual functions, etc.
    pci_class = read_text(root, f"{base}/class") or ""
    downtrained = bool((cur_s is not None and max_s is not None and cur_s < max_s)
                       or (cur_w is not None and max_w is not None and cur_w < max_w))
    return {
        "bdf": bdf,
        "class": pci_class,
        "role": CLASS_ROLES.get(pci_class[:6], "other"),
        "vendor": read_text(root, f"{base}/vendor"),
        "device": read_text(root, f"{base}/device"),
        "current_speed_gts": cur_s, "max_speed_gts": max_s,
        "current_gen": GEN_BY_SPEED.get(cur_s) if cur_s else None,
        "max_gen": GEN_BY_SPEED.get(max_s) if max_s else None,
        "current_width": cur_w, "max_width": max_w,
        "downtrained": downtrained,
    }


def read_pcie(root: str | Path = "/") -> dict[str, Any]:
    """Every PCIe device with link fields, flagging downtrained links."""
    try:
        base = rpath(root, "/sys/bus/pci/devices")
        if not base.is_dir():
            return unavailable("/sys/bus/pci/devices not present")
        devices = [d for d in (_device(root, p.name) for p in sorted(base.iterdir())) if d]
        if not devices:
            return unavailable("no PCIe devices expose link speed/width "
                               "(typical in VMs: devices are virtual/paravirtual)")
        return {"available": True, "devices": devices,
                "any_downtrained": any(d["downtrained"] for d in devices)}
    except Exception as exc:  # noqa: BLE001
        return unavailable(f"pcie reader error: {exc}")
