"""The SiliconScope snapshot schema (version 1) and its validator.

One snapshot = one machine's hardware health at one point in time. The
collector produces snapshots from a real Linux box; ``fleet/simulator.py``
produces SIMULATED snapshots in exactly the same shape, so the validator in
``fleet/`` can't tell (and doesn't care) where a snapshot came from.

Shape::

    {
      "schema_version": 1,
      "host_id": "e080fed1753e",          # sha256(hostname)[:12], never the raw name
      "collected_at": "2026-11-23T12:00:00+00:00",
      "source": "collector" | "simulator",
      "sku": "sku-a" | null,              # hardware SKU (fleet); null for ad-hoc collection
      "platform": {...}, "cpu": {...}, "cache": {...}, "memory": {...},
      "thermal": {...}, "pcie": {...}, "ecc": {...}, "storage": {...}, "probe": {...}
    }

Every section is a dict with ``available: bool``. If ``available`` is False the
section only needs a ``reason`` string; this is how missing hardware interfaces
(very common on VMs) are represented without crashing anything downstream.

We hand-roll validation instead of using a JSON-Schema library: the rules are
short, and readable code is easier to defend in an interview than a 300-line
schema document.
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = 1
SECTIONS = ["platform", "cpu", "cache", "memory", "thermal", "pcie", "ecc", "storage", "probe"]

NUM = (int, float)
OPT_NUM = (int, float, type(None))
OPT_STR = (str, type(None))
OPT_INT = (int, type(None))

# Required fields per section when ``available`` is True: name -> allowed types.
SECTION_FIELDS: dict[str, dict[str, tuple[type, ...]]] = {
    "platform": {"kernel": OPT_STR, "os": OPT_STR, "virtualization": OPT_STR},
    "cpu": {"model": OPT_STR, "sockets": OPT_INT, "cores": OPT_INT, "threads": OPT_INT,
            "flags": (dict,), "freq": (dict,)},
    "cache": {"levels": (list,)},
    "memory": {"mem_total_bytes": OPT_INT, "mem_available_bytes": OPT_INT, "dimms": (dict,)},
    "thermal": {"zones": (list,), "max_temp_c": OPT_NUM},
    "pcie": {"devices": (list,), "any_downtrained": (bool,)},
    "ecc": {"controllers": (list,), "ce_total": (int,), "ue_total": (int,),
            "ce_daily": (list, type(None))},
    "storage": {"devices": (list,)},
    "probe": {"l1_ns": OPT_NUM, "l2_ns": OPT_NUM, "l3_ns": OPT_NUM, "dram_ns": OPT_NUM,
              "read_gbps": OPT_NUM, "loaded_cpu_mhz": OPT_NUM, "loaded_max_temp_c": OPT_NUM},
}

# Required fields for items inside list-valued fields.
ITEM_FIELDS: dict[tuple[str, str], dict[str, tuple[type, ...]]] = {
    ("cache", "levels"): {"level": OPT_INT, "type": OPT_STR, "size_bytes": OPT_INT},
    ("pcie", "devices"): {"bdf": (str,), "current_speed_gts": OPT_NUM,
                          "max_speed_gts": OPT_NUM, "current_width": OPT_INT,
                          "max_width": OPT_INT, "downtrained": (bool,)},
    ("ecc", "controllers"): {"mc": (str,), "ce_count": (int,), "ue_count": (int,)},
    ("thermal", "zones"): {"name": (str,), "temp_c": OPT_NUM},
}


def _check_type(errors: list[str], where: str, value: Any, types: tuple[type, ...]) -> None:
    # bool is a subclass of int in Python; don't let True pass as a number.
    if isinstance(value, bool) and bool not in types:
        errors.append(f"{where}: expected {_names(types)}, got bool")
    elif not isinstance(value, types):
        errors.append(f"{where}: expected {_names(types)}, got {type(value).__name__}")


def _names(types: tuple[type, ...]) -> str:
    return "|".join("null" if t is type(None) else t.__name__ for t in types)


def _validate_section(errors: list[str], name: str, sec: Any) -> None:
    if not isinstance(sec, dict):
        errors.append(f"{name}: section must be an object")
        return
    if not isinstance(sec.get("available"), bool):
        errors.append(f"{name}.available: must be a bool")
        return
    if not sec["available"]:
        if not isinstance(sec.get("reason"), str):
            errors.append(f"{name}.reason: required string when available is false")
        return
    for field, types in SECTION_FIELDS[name].items():
        if field not in sec:
            errors.append(f"{name}.{field}: missing")
            continue
        _check_type(errors, f"{name}.{field}", sec[field], types)
        item_spec = ITEM_FIELDS.get((name, field))
        if item_spec and isinstance(sec[field], list):
            for i, item in enumerate(sec[field]):
                if not isinstance(item, dict):
                    errors.append(f"{name}.{field}[{i}]: must be an object")
                    continue
                for ifield, itypes in item_spec.items():
                    if ifield not in item:
                        errors.append(f"{name}.{field}[{i}].{ifield}: missing")
                    else:
                        _check_type(errors, f"{name}.{field}[{i}].{ifield}", item[ifield], itypes)
    if name == "ecc" and isinstance(sec.get("ce_daily"), list):
        for i, v in enumerate(sec["ce_daily"]):
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                errors.append(f"ecc.ce_daily[{i}]: must be a non-negative int")


def validate(snapshot: Any) -> list[str]:
    """Return a list of human-readable problems; an empty list means valid."""
    errors: list[str] = []
    if not isinstance(snapshot, dict):
        return ["snapshot must be a JSON object"]
    if snapshot.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version: expected {SCHEMA_VERSION}, "
                      f"got {snapshot.get('schema_version')!r}")
    for key, types in {"host_id": (str,), "collected_at": (str,), "source": (str,),
                       "sku": OPT_STR}.items():
        if key not in snapshot:
            errors.append(f"{key}: missing")
        else:
            _check_type(errors, key, snapshot[key], types)
    if snapshot.get("source") not in ("collector", "simulator"):
        errors.append("source: must be 'collector' or 'simulator'")
    for name in SECTIONS:
        if name not in snapshot:
            errors.append(f"{name}: missing section")
        else:
            _validate_section(errors, name, snapshot[name])
    return errors


def assert_valid(snapshot: Any) -> None:
    """Raise ValueError listing every problem (used by writers before saving)."""
    errors = validate(snapshot)
    if errors:
        raise ValueError("invalid snapshot:\n  " + "\n  ".join(errors))
