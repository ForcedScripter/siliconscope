"""Collector tests against fake sysfs/procfs trees (no real hardware needed)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from collector import collector
from collector.readers import common
from collector.readers.cache import data_cache_sizes, read_cache
from collector.readers.cpu import read_cpu
from collector.readers.ecc import read_ecc
from collector.readers.memory import parse_dmidecode, read_memory
from collector.readers.pcie import parse_speed, parse_width, read_pcie
from collector.readers.platform import read_platform
from collector.readers.storage import read_storage
from collector.readers.thermal import read_thermal
from collector.schema import validate

FIX = Path(__file__).parent / "fixtures"
HEALTHY = FIX / "fake_sysfs" / "healthy"
DEGRADED = FIX / "fake_sysfs" / "degraded"
MINIMAL = FIX / "fake_sysfs" / "minimal"


# --- healthy machine ---------------------------------------------------------

def test_cache_levels_healthy() -> None:
    cache = read_cache(HEALTHY)
    assert cache["available"] and cache["source"] == "sysfs"
    assert data_cache_sizes(cache) == {"L1": 48 * 1024, "L2": 1280 * 1024, "L3": 61440 * 1024}
    assert all(c["line_bytes"] == 64 for c in cache["levels"])


def test_cpu_topology_flags_and_freq() -> None:
    cpu = read_cpu(HEALTHY)
    assert cpu["model"].startswith("Intel(R) Xeon(R) Fake")
    assert (cpu["sockets"], cpu["cores"], cpu["threads"]) == (1, 4, 8)
    assert cpu["flags"]["avx512f"] and not cpu["flags"]["amx_tile"]
    assert cpu["freq"]["available"] and cpu["freq"]["max_mhz"] == 3400.0
    assert cpu["freq"]["cur_mhz_mean"] == 2300.0


def test_memory_meminfo() -> None:
    mem = read_memory(HEALTHY)
    assert mem["mem_total_bytes"] == 263921212 * 1024
    assert mem["mem_available_bytes"] == 250000000 * 1024
    assert mem["dimms"]["available"] is False  # never shells out under a fake root


def test_thermal_zones_and_hwmon() -> None:
    th = read_thermal(HEALTHY)
    names = {z["name"] for z in th["zones"]}
    assert {"thermal_zone0", "nvme/Composite"} <= names
    assert th["max_temp_c"] == 58.0


def test_pcie_healthy_links_not_downtrained() -> None:
    pcie = read_pcie(HEALTHY)
    roles = {d["role"]: d for d in pcie["devices"]}
    assert set(roles) == {"nic", "nvme"}  # host bridge without link fields is skipped
    assert roles["nic"]["current_width"] == 16 and roles["nic"]["max_gen"] == 4
    assert pcie["any_downtrained"] is False


def test_ecc_zero_counts() -> None:
    ecc = read_ecc(HEALTHY)
    assert ecc["available"] and ecc["ce_total"] == 0 and ecc["ue_total"] == 0
    assert ecc["ce_daily"] is None  # history is not available from one reading


def test_platform_hashes_hostname() -> None:
    plat = read_platform(HEALTHY)
    assert plat["host_id"] == common.host_id("fake-server-01")
    assert "fake-server-01" not in json.dumps(plat)
    assert plat["kernel"] == "6.8.0-fake-generic"
    assert plat["virtualization"] == "none"  # no 'hypervisor' flag in fake cpuinfo


def test_full_snapshot_healthy_is_valid() -> None:
    snap = collector.collect(HEALTHY)
    assert validate(snap) == []
    assert snap["source"] == "collector" and snap["probe"]["available"] is False


# --- degraded machine --------------------------------------------------------

def test_pcie_downtrained_link_detected() -> None:
    pcie = read_pcie(DEGRADED)
    nic = next(d for d in pcie["devices"] if d["role"] == "nic")
    assert nic["current_width"] == 8 and nic["max_width"] == 16
    assert nic["downtrained"] is True and pcie["any_downtrained"] is True


def test_ecc_nonzero_counts() -> None:
    ecc = read_ecc(DEGRADED)
    assert ecc["ce_total"] == 37 and ecc["ue_total"] == 1
    assert {c["mc"] for c in ecc["controllers"]} == {"mc0", "mc1"}


def test_degraded_snapshot_still_valid() -> None:
    snap = collector.collect(DEGRADED)
    assert validate(snap) == []
    assert snap["thermal"]["max_temp_c"] == 97.0


# --- missing interfaces --------------------------------------------------------

@pytest.mark.parametrize("reader", [read_cache, read_thermal, read_pcie, read_ecc])
def test_missing_interfaces_degrade_gracefully(reader) -> None:  # type: ignore[no-untyped-def]
    out = reader(MINIMAL)
    assert out["available"] is False and isinstance(out["reason"], str) and out["reason"]


def test_minimal_snapshot_valid_and_cli_writes_file(tmp_path: Path) -> None:
    out = tmp_path / "snap.json"
    rc = collector.main(["--out", str(out), "--root", str(MINIMAL)])
    snap = json.loads(out.read_text())
    assert rc == 0 and validate(snap) == []
    assert snap["cpu"]["available"] and snap["cpu"]["freq"]["available"] is False


def test_nonexistent_root_never_raises(tmp_path: Path) -> None:
    snap = collector.collect(tmp_path / "does-not-exist")
    assert validate(snap) == []
    assert not snap["cpu"]["available"] and not snap["memory"]["available"]


def test_storage_with_fake_root_is_unavailable() -> None:
    assert read_storage(HEALTHY)["available"] is False


def test_storage_parses_lsblk(monkeypatch: pytest.MonkeyPatch) -> None:
    from collector.readers import storage

    fake = {"blockdevices": [
        {"name": "nvme0n1", "type": "disk", "size": 1024, "rota": False, "model": "X",
         "tran": "nvme"},
        {"name": "loop0", "type": "loop", "size": 1, "rota": False, "model": None, "tran": None}]}
    monkeypatch.setattr(storage, "is_real_root", lambda root: True)
    monkeypatch.setattr(storage, "run_json_tool", lambda args, timeout=10.0: (
        (fake, "") if args[0] == "lsblk" else (None, "smartctl not installed")))
    out = storage.read_storage("/")
    assert [d["name"] for d in out["devices"]] == ["nvme0n1"]
    assert out["devices"][0]["smart"]["available"] is False


# --- parsers -----------------------------------------------------------------------

def test_parse_link_fields() -> None:
    assert parse_speed("16.0 GT/s PCIe") == 16.0 and parse_speed("8 GT/s") == 8.0
    assert parse_speed("Unknown") is None and parse_speed(None) is None
    assert parse_width("16") == 16 and parse_width("x8") == 8


def test_parse_dmidecode_flags_slow_dimm() -> None:
    mods = parse_dmidecode((FIX / "dmidecode_memory.txt").read_text())
    assert len(mods) == 2  # the empty slot is skipped
    slow = next(m for m in mods if m["locator"] == "DIMM_B1")
    assert slow["rated_speed_mts"] == 3200 and slow["configured_speed_mts"] == 2933
    assert slow["below_rated_speed"] is True
    assert slow["size_bytes"] == 32 << 30


def test_parse_size_units() -> None:
    assert common.parse_size("48K") == 48 * 1024
    assert common.parse_size("8 MiB") == 8 << 20
    assert common.parse_size("garbage") is None
