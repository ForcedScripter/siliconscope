"""Platform reader: kernel, OS, virtualization and identity.

The hostname is never stored; ``host_id`` is a SHA-256 prefix of it (see
``common.host_id``). Under a fake root (tests) we read ``<root>/etc/hostname`` so
results are deterministic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import host_id, is_real_root, read_text, run_tool, unavailable


def _os_name(root: str | Path) -> str | None:
    text = read_text(root, "/etc/os-release")
    for line in (text or "").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return None


def detect_virtualization(root: str | Path, kernel: str | None) -> str | None:
    """Best-effort hypervisor name, or 'none' on bare metal, or None if unknown.

    Order: systemd-detect-virt (most precise) -> WSL kernel string -> the
    ``hypervisor`` CPU flag, which every mainstream hypervisor sets in CPUID.
    """
    if is_real_root(root):
        out, _ = run_tool(["systemd-detect-virt"])
        # systemd-detect-virt exits 1 and prints "none" on bare metal; run_tool
        # treats non-zero as failure, so we only accept a positive answer here.
        if out and out.strip():
            return out.strip()
    if kernel and "microsoft" in kernel.lower():
        return "wsl"
    cpuinfo = read_text(root, "/proc/cpuinfo")
    if cpuinfo is None:
        return None
    for line in cpuinfo.splitlines():
        if line.startswith("flags"):
            return "hypervisor (unknown)" if " hypervisor" in line else "none"
    return None


def read_platform(root: str | Path = "/") -> dict[str, Any]:
    try:
        kernel = read_text(root, "/proc/sys/kernel/osrelease")
        hostname = None if is_real_root(root) else (read_text(root, "/etc/hostname") or "fake")
        return {"available": True,
                "kernel": kernel,
                "os": _os_name(root),
                "virtualization": detect_virtualization(root, kernel),
                "host_id": host_id(hostname)}
    except Exception as exc:  # noqa: BLE001
        return unavailable(f"platform reader error: {exc}")
