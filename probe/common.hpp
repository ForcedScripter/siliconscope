// common.hpp — small helpers shared by the latency and bandwidth probes.
//
// Everything here is deliberately plain C++17 + POSIX so the probes build with
// `g++ -O2` on any Linux box (Codespace, WSL2, CI runner, bare metal).
#pragma once

#include <sched.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

namespace ss {

constexpr size_t kCacheLine = 64;          // bytes; x86 and most ARM server cores
constexpr size_t kHugePage = 2u << 20;     // 2 MiB transparent huge page on x86-64

// ---------------------------------------------------------------------------
// Timing
// ---------------------------------------------------------------------------

// steady_clock is monotonic (never jumps backwards with NTP), which is what a
// benchmark needs. On Linux it maps to clock_gettime(CLOCK_MONOTONIC), ~20 ns
// overhead — negligible because we time millions of accesses per sample.
inline double now_ns() {
    using namespace std::chrono;
    return static_cast<double>(
        duration_cast<nanoseconds>(steady_clock::now().time_since_epoch()).count());
}

// ---------------------------------------------------------------------------
// Argument parsing: "--key value" pairs and bare "--flag" switches.
// ---------------------------------------------------------------------------

class Args {
public:
    Args(int argc, char** argv) {
        for (int i = 1; i < argc; ++i) {
            std::string a = argv[i];
            if (a.rfind("--", 0) != 0) continue;  // ignore stray positional args
            std::string key = a.substr(2);
            // A key followed by a non "--" token takes it as its value; otherwise it's a flag.
            if (i + 1 < argc && std::string(argv[i + 1]).rfind("--", 0) != 0) {
                values_[key] = argv[++i];
            } else {
                values_[key] = "1";
            }
        }
    }
    bool has(const std::string& k) const { return values_.count(k) > 0; }
    std::string str(const std::string& k, const std::string& def) const {
        auto it = values_.find(k);
        return it == values_.end() ? def : it->second;
    }
    long num(const std::string& k, long def) const {
        auto it = values_.find(k);
        return it == values_.end() ? def : std::stol(it->second);
    }

private:
    std::map<std::string, std::string> values_;
};

// ---------------------------------------------------------------------------
// CPU pinning
// ---------------------------------------------------------------------------

// Pin the calling thread to one logical CPU. Without pinning the scheduler can
// migrate us mid-measurement to a core with a cold L1/L2, which shows up as
// noise (and, on hybrid CPUs, as a different core type entirely).
inline bool pin_to_cpu(int cpu) {
    cpu_set_t set;
    CPU_ZERO(&set);
    CPU_SET(cpu, &set);
    if (sched_setaffinity(0, sizeof(set), &set) != 0) {
        std::fprintf(stderr, "warn: could not pin to cpu %d (continuing unpinned)\n", cpu);
        return false;
    }
    return true;
}

// Logical CPUs ordered so that each *physical core* appears once before any SMT
// sibling: e.g. on a 4-core/8-thread part where cpu0/cpu1 share a core, the
// order is 0,2,4,6,1,3,5,7. Multi-thread runs use this so 4 threads land on 4
// cores; naively pinning to cpus 0-3 would put them on 2 cores, sharing L1/L2.
// Falls back to 0..n-1 if sysfs topology is missing.
inline std::vector<int> cpus_one_per_core_first() {
    const int n = static_cast<int>(sysconf(_SC_NPROCESSORS_ONLN));
    std::vector<int> first, siblings;
    std::vector<std::pair<std::string, std::string>> seen;  // (package, core)
    for (int c = 0; c < n; ++c) {
        std::string base = "/sys/devices/system/cpu/cpu" + std::to_string(c) + "/topology/";
        std::ifstream pkg_f(base + "physical_package_id"), core_f(base + "core_id");
        std::string pkg, core;
        if (!(pkg_f >> pkg) || !(core_f >> core)) {
            pkg = "?";
            core = std::to_string(c);  // unknown topology: treat each CPU as its own core
        }
        auto key = std::make_pair(pkg, core);
        if (std::find(seen.begin(), seen.end(), key) == seen.end()) {
            seen.push_back(key);
            first.push_back(c);
        } else {
            siblings.push_back(c);
        }
    }
    first.insert(first.end(), siblings.begin(), siblings.end());
    return first;
}

// ---------------------------------------------------------------------------
// Memory availability
// ---------------------------------------------------------------------------

// Returns MemAvailable from /proc/meminfo in bytes, or 0 if it can't be read.
// MemAvailable (not MemFree) is the kernel's estimate of what can be allocated
// without swapping, because page cache can be reclaimed.
inline uint64_t mem_available_bytes() {
    std::ifstream f("/proc/meminfo");
    std::string line;
    while (std::getline(f, line)) {
        if (line.rfind("MemAvailable:", 0) == 0) {
            std::istringstream ss(line.substr(13));
            uint64_t kb = 0;
            ss >> kb;
            return kb * 1024;
        }
    }
    return 0;
}

// Clamp a requested working-set size so we never use more than `fraction` of
// MemAvailable. Prints a warning when it has to shrink the request.
inline size_t cap_to_available(size_t requested, double fraction = 0.5) {
    uint64_t avail = mem_available_bytes();
    if (avail == 0) return requested;  // unknown: trust the caller's limit
    auto cap = static_cast<size_t>(static_cast<double>(avail) * fraction);
    if (requested > cap) {
        std::fprintf(stderr, "warn: capping %zu MB to %zu MB (MemAvailable=%llu MB)\n",
                     requested >> 20, cap >> 20,
                     static_cast<unsigned long long>(avail >> 20));
        return cap;
    }
    return requested;
}

// ---------------------------------------------------------------------------
// Allocation
// ---------------------------------------------------------------------------

// Allocate `bytes` aligned to 2 MiB. Alignment to the huge-page size matters
// only when `hugepages` is true: madvise(MADV_HUGEPAGE) asks the kernel to back
// the region with 2 MiB transparent huge pages, so one TLB entry covers 512x
// more memory. It is a hint — the kernel may ignore it (THP disabled, or
// fragmentation), so callers should treat the result as "maybe huge pages".
inline void* alloc_buffer(size_t bytes, bool hugepages) {
    size_t rounded = (bytes + kHugePage - 1) / kHugePage * kHugePage;
    void* p = nullptr;
    if (posix_memalign(&p, kHugePage, rounded) != 0) return nullptr;
    if (hugepages && madvise(p, rounded, MADV_HUGEPAGE) != 0) {
        std::fprintf(stderr, "warn: madvise(MADV_HUGEPAGE) failed; using 4 KiB pages\n");
    }
    return p;
}

// ---------------------------------------------------------------------------
// Statistics
// ---------------------------------------------------------------------------

// Nearest-rank percentile on a copy of the samples (q in [0, 1]). With only a
// handful of repeats, nearest-rank is easier to reason about than interpolation.
inline double percentile(std::vector<double> v, double q) {
    if (v.empty()) return 0.0;
    std::sort(v.begin(), v.end());
    size_t idx = static_cast<size_t>(q * static_cast<double>(v.size() - 1) + 0.5);
    return v[std::min(idx, v.size() - 1)];
}

}  // namespace ss
