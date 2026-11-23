"""Generate the fake /sys and /proc trees used by the collector tests.

Run from the repo root:  python -m tests.fixtures.make_fake_sysfs
The generated trees are committed, so tests don't depend on running this; the
script just documents exactly how they were built and makes them easy to change.

Scenarios (each a directory under tests/fixtures/fake_sysfs/):
  healthy   2-socket-style server: caches, cpufreq, thermal, NIC Gen4 x16 + NVMe Gen4 x4
            at full link, EDAC with zero errors.
  degraded  same machine, but NIC trained at x8, ECC CE=37/UE=1, a hot CPU package.
  minimal   only /proc/cpuinfo and /proc/meminfo — every other interface missing
            (what a locked-down container or VM looks like).
"""

from __future__ import annotations

import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent / "fake_sysfs"

CPUINFO_CORE = """processor\t: {p}
vendor_id\t: GenuineIntel
model name\t: Intel(R) Xeon(R) Fake 8380 CPU @ 2.30GHz
physical id\t: 0
core id\t\t: {core}
cpu MHz\t\t: 2300.000
flags\t\t: fpu sse4_2 avx avx2 fma avx512f avx512bw avx512_vnni aes sha_ni

"""

MEMINFO = """MemTotal:       263921212 kB
MemFree:        200000000 kB
MemAvailable:   250000000 kB
"""

CACHES = [  # (index, level, type, size, ways, shared)
    (0, 1, "Data", "48K", 12, "0,4"),
    (1, 1, "Instruction", "32K", 8, "0,4"),
    (2, 2, "Unified", "1280K", 20, "0,4"),
    (3, 3, "Unified", "61440K", 12, "0-7"),
]


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def base_proc(root: Path) -> None:
    write(root, "/proc/cpuinfo", "".join(CPUINFO_CORE.format(p=p, core=p % 4) for p in range(8)))
    write(root, "/proc/meminfo", MEMINFO)


def full_tree(root: Path, nic_width: int, ce: int, ue: int, pkg_temp_milli: int) -> None:
    base_proc(root)
    write(root, "/proc/sys/kernel/osrelease", "6.8.0-fake-generic")
    write(root, "/etc/os-release", 'NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 24.04 LTS (fake)"')
    write(root, "/etc/hostname", "fake-server-01")
    for idx, level, ctype, size, ways, shared in CACHES:
        d = f"/sys/devices/system/cpu/cpu0/cache/index{idx}"
        write(root, f"{d}/level", str(level))
        write(root, f"{d}/type", ctype)
        write(root, f"{d}/size", size)
        write(root, f"{d}/coherency_line_size", "64")
        write(root, f"{d}/ways_of_associativity", str(ways))
        write(root, f"{d}/shared_cpu_list", shared)
    for cpu in range(8):
        d = f"/sys/devices/system/cpu/cpu{cpu}/cpufreq"
        write(root, f"{d}/scaling_cur_freq", "2300000")
        write(root, f"{d}/cpuinfo_max_freq", "3400000")
    write(root, "/sys/class/thermal/thermal_zone0/type", "x86_pkg_temp")
    write(root, "/sys/class/thermal/thermal_zone0/temp", str(pkg_temp_milli))
    write(root, "/sys/class/hwmon/hwmon0/name", "nvme")
    write(root, "/sys/class/hwmon/hwmon0/temp1_input", "41850")
    write(root, "/sys/class/hwmon/hwmon0/temp1_label", "Composite")
    # NIC: Gen4 x16 capable.
    nic = "/sys/bus/pci/devices/0000-17-00.0"
    write(root, f"{nic}/class", "0x020000")
    write(root, f"{nic}/vendor", "0x15b3")
    write(root, f"{nic}/device", "0x101d")
    write(root, f"{nic}/current_link_speed", "16.0 GT/s PCIe")
    write(root, f"{nic}/max_link_speed", "16.0 GT/s PCIe")
    write(root, f"{nic}/current_link_width", str(nic_width))
    write(root, f"{nic}/max_link_width", "16")
    # NVMe: Gen4 x4.
    nvme = "/sys/bus/pci/devices/0000-5e-00.0"
    write(root, f"{nvme}/class", "0x010802")
    write(root, f"{nvme}/vendor", "0x144d")
    write(root, f"{nvme}/device", "0xa80a")
    write(root, f"{nvme}/current_link_speed", "16.0 GT/s PCIe")
    write(root, f"{nvme}/max_link_speed", "16.0 GT/s PCIe")
    write(root, f"{nvme}/current_link_width", "4")
    write(root, f"{nvme}/max_link_width", "4")
    # Host bridge: no link fields -> must be skipped by the reader.
    write(root, "/sys/bus/pci/devices/0000-00-00.0/class", "0x060000")
    for mc, (c, u) in {"mc0": (ce, ue), "mc1": (0, 0)}.items():
        write(root, f"/sys/devices/system/edac/mc/{mc}/ce_count", str(c))
        write(root, f"/sys/devices/system/edac/mc/{mc}/ue_count", str(u))


def main() -> None:
    if BASE.exists():
        shutil.rmtree(BASE)
    full_tree(BASE / "healthy", nic_width=16, ce=0, ue=0, pkg_temp_milli=58000)
    full_tree(BASE / "degraded", nic_width=8, ce=37, ue=1, pkg_temp_milli=97000)
    base_proc(BASE / "minimal")
    print(f"wrote fake sysfs trees under {BASE}")


if __name__ == "__main__":
    main()
