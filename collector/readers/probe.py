"""Probe reader (``--with-probe``): run quick latency + bandwidth probes and summarise.

While the probes run, a background thread samples CPU frequency and temperature
once per 0.5 s. That gives "under load" values — the ones that reveal thermal
throttling, which idle readings can't show.

Quick mode is ~15 s: latency up to 64 MB (3 repeats; well past an 8 MB L3, so
the last plateau is DRAM) and single-thread bandwidth up to 256 MB.
"""

from __future__ import annotations

import io
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd

from .cache import data_cache_sizes, read_cache
from .common import unavailable
from .cpu import read_cpufreq
from .thermal import read_thermal

DEFAULT_BIN_DIR = Path(__file__).resolve().parents[2] / "probe" / "build"


class LoadSampler(threading.Thread):
    """Samples mean CPU MHz and max temperature until stopped."""

    def __init__(self, root: str | Path, period_s: float = 0.5) -> None:
        super().__init__(daemon=True)
        self.root, self.period_s = root, period_s
        self.mhz: list[float] = []
        self.temps: list[float] = []
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            freq = read_cpufreq(self.root)
            if freq.get("available") and freq.get("cur_mhz_mean"):
                self.mhz.append(freq["cur_mhz_mean"])
            thermal = read_thermal(self.root)
            if thermal.get("available") and thermal.get("max_temp_c") is not None:
                self.temps.append(thermal["max_temp_c"])
            self._stop_event.wait(self.period_s)

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=2)


def _run_csv(cmd: list[str], timeout: float) -> pd.DataFrame:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True)
    return pd.read_csv(io.StringIO(proc.stdout))


def read_probe(root: str | Path = "/", bin_dir: Path = DEFAULT_BIN_DIR,
               quick: bool = True) -> dict[str, Any]:
    """Run the probes and return per-level latency, read GB/s and loaded freq/temp."""
    latency, bandwidth = bin_dir / "latency", bin_dir / "bandwidth"
    if not latency.exists() or not bandwidth.exists():
        return unavailable(f"probe binaries not built in {bin_dir} (run `make -C probe`)")
    # Imported lazily: the summarisers live with the plotting scripts.
    from scripts.plot_bandwidth import summarize as bw_summary
    from scripts.plot_latency import summarize as lat_summary

    max_lat, max_bw, reps = (64, 256, 3) if quick else (256, 1024, 5)
    sampler = LoadSampler(root)
    sampler.start()
    try:
        lat_df = _run_csv([str(latency), "--max-mb", str(max_lat), "--repeats", str(reps)], 600)
        bw_df = _run_csv([str(bandwidth), "--max-mb", str(max_bw), "--repeats", str(reps)], 600)
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        return unavailable(f"probe run failed: {exc}")
    finally:
        sampler.stop()

    reported = data_cache_sizes(read_cache(root))
    lat = lat_summary(lat_df, reported)
    bw = bw_summary(bw_df, reported.get("L3"))
    read = bw.get("threads_1", {}).get("read", {})
    return {
        "available": True,
        "mode": "quick" if quick else "full",
        "l1_ns": lat["l1_ns"], "l2_ns": lat["l2_ns"], "l3_ns": lat["l3_ns"],
        "dram_ns": lat["dram_ns"],
        "read_gbps": read.get("dram_gbps"),
        "peak_read_gbps": read.get("peak_gbps"),
        "loaded_cpu_mhz": round(sum(sampler.mhz) / len(sampler.mhz), 1) if sampler.mhz else None,
        "loaded_max_temp_c": max(sampler.temps) if sampler.temps else None,
        "load_samples": len(sampler.mhz),
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
