"""Helpers shared by every reader.

Every reader takes a ``root`` path that is prepended to absolute sysfs/procfs
paths. In production ``root`` is ``/``; in tests it points at
``tests/fixtures/fake_sysfs/<scenario>``, which lets us test against a fake
machine without real hardware.

The golden rule: **readers never raise**. Missing files, permission errors and
missing tools all turn into ``{"available": False, "reason": "..."}`` so one
broken interface never takes down the whole snapshot.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any


def rpath(root: str | Path, abs_path: str) -> Path:
    """Map an absolute path like ``/sys/x`` to ``<root>/sys/x``."""
    return Path(root) / abs_path.lstrip("/")


def read_text(root: str | Path, abs_path: str) -> str | None:
    """Read and strip a small text file, or return None if it can't be read."""
    try:
        return rpath(root, abs_path).read_text(encoding="utf-8", errors="replace").strip()
    except (OSError, ValueError):
        return None


def read_int(root: str | Path, abs_path: str) -> int | None:
    """Read a file containing a single integer (common in sysfs)."""
    text = read_text(root, abs_path)
    if text is None:
        return None
    try:
        return int(text.split()[0])
    except (ValueError, IndexError):
        return None


def unavailable(reason: str) -> dict[str, Any]:
    """The standard 'this interface is not here' result."""
    return {"available": False, "reason": reason}


def is_real_root(root: str | Path) -> bool:
    """True when reading the live machine (so running external tools makes sense)."""
    return Path(root).resolve() == Path("/").resolve()


def run_tool(args: list[str], timeout: float = 10.0) -> tuple[str | None, str]:
    """Run an external command; return (stdout, error_reason).

    Returns ``(None, reason)`` if the tool is missing, fails or times out, so
    callers can pass ``reason`` straight into :func:`unavailable`.
    """
    if shutil.which(args[0]) is None:
        return None, f"{args[0]} not installed"
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"{args[0]} failed: {exc}"
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout).strip().splitlines()
        return None, f"{args[0]} exited {proc.returncode}: {msg[0] if msg else 'no output'}"
    return proc.stdout, ""


def run_json_tool(args: list[str], timeout: float = 10.0) -> tuple[Any | None, str]:
    """Like :func:`run_tool` but parses stdout as JSON."""
    out, reason = run_tool(args, timeout)
    if out is None:
        return None, reason
    try:
        return json.loads(out), ""
    except json.JSONDecodeError as exc:
        return None, f"{args[0]} produced invalid JSON: {exc}"


def host_id(hostname: str | None = None) -> str:
    """First 12 hex chars of SHA-256(hostname).

    We never store the raw hostname (it can leak org/location naming schemes);
    a truncated hash is still unique enough to tell machines apart in a fleet
    and matches ``printf %s "$(hostname)" | sha256sum | cut -c1-12`` in the Makefile.
    """
    name = hostname if hostname is not None else socket.gethostname()
    return hashlib.sha256(name.encode()).hexdigest()[:12]


_SIZE_RE = re.compile(r"^\s*([\d.]+)\s*([KMGT]?)(I?B)?\s*$", re.IGNORECASE)
_UNIT = {"": 1, "K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}


def parse_size(text: str | None) -> int | None:
    """Parse sysfs/lscpu sizes like ``48K``, ``1280K``, ``8 MiB`` into bytes.

    Cache sizes in sysfs use binary units (K = 1024), so we do too.
    """
    if not text:
        return None
    m = _SIZE_RE.match(text)
    if not m:
        return None
    return int(float(m.group(1)) * _UNIT[m.group(2).upper()])
