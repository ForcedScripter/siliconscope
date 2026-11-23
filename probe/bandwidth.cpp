// bandwidth.cpp — sequential memory bandwidth (read / write / copy) vs buffer size.
//
// Unlike the latency probe, these loops issue *independent* accesses in address
// order: the core keeps many loads/stores in flight and the prefetcher streams
// lines ahead, so we measure how many bytes per second each level can deliver.
//
//   read  : sum every 8-byte word into an accumulator (bytes counted = size)
//   write : store a value to every word                (bytes counted = size)
//   copy  : dst[i] = src[i]                            (bytes counted = 2 x size:
//           read + write, the STREAM benchmark convention)
//
// Note: at -O2 GCC may turn the write/copy loops into memset/memcpy calls. That
// is fine — those library routines are what real programs use to move memory.
//
// --threads N runs N threads, each pinned to its own CPU with its own private
// buffers (no shared cache lines); the reported GB/s is the aggregate.
//
// Usage:
//   bandwidth [--min-kb 16] [--max-mb 1024] [--max-total-mb 1024] [--repeats 5]
//             [--threads 1] [--cpu 0]
// Output (stdout, CSV):
//   op,threads,size_bytes,gbps_median,gbps_p10,gbps_p90
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

#include "common.hpp"

namespace {

enum class Op { Read, Write, Copy };
const char* op_name(Op op) { return op == Op::Read ? "read" : op == Op::Write ? "write" : "copy"; }

volatile uint64_t g_sink = 0;  // defeats dead-code elimination of the read loop

// Eight independent accumulators. With a single `sum += buf[i]` every add waits
// for the previous one (a serial dependency chain), so the loop measures adder
// latency (~1 word/cycle) rather than memory. Separate chains let the core issue
// 2 loads per cycle and keep the memory system busy. `words` is always a
// multiple of 8 because sizes are powers of two >= 16 KB.
uint64_t do_read(const uint64_t* buf, size_t words) {
    uint64_t s0 = 0, s1 = 0, s2 = 0, s3 = 0, s4 = 0, s5 = 0, s6 = 0, s7 = 0;
    for (size_t i = 0; i < words; i += 8) {
        s0 += buf[i];
        s1 += buf[i + 1];
        s2 += buf[i + 2];
        s3 += buf[i + 3];
        s4 += buf[i + 4];
        s5 += buf[i + 5];
        s6 += buf[i + 6];
        s7 += buf[i + 7];
    }
    return s0 + s1 + s2 + s3 + s4 + s5 + s6 + s7;
}

void do_write(uint64_t* buf, size_t words, uint64_t value) {
    for (size_t i = 0; i < words; ++i) buf[i] = value;
}

void do_copy(uint64_t* dst, const uint64_t* src, size_t words) {
    for (size_t i = 0; i < words; ++i) dst[i] = src[i];
}

// One thread's private buffers and its measured time.
struct Worker {
    uint64_t* src = nullptr;
    uint64_t* dst = nullptr;
    double elapsed_ns = 0;
};

// Run `passes` passes of `op` over `size` bytes on one worker; record elapsed ns.
void run_passes(Op op, Worker& w, size_t size, size_t passes) {
    const size_t words = size / sizeof(uint64_t);
    uint64_t acc = 0;
    double t0 = ss::now_ns();
    for (size_t p = 0; p < passes; ++p) {
        switch (op) {
            case Op::Read: acc += do_read(w.src, words); break;
            case Op::Write: do_write(w.src, words, p); break;
            case Op::Copy: do_copy(w.dst, w.src, words); break;
        }
    }
    w.elapsed_ns = ss::now_ns() - t0;
    // Consume results so the compiler must perform every pass.
    g_sink = g_sink + acc + w.src[words / 2] + (w.dst ? w.dst[words / 2] : 0);
}

// One timed sample across all threads. Threads spin on `go` so they start
// together; aggregate bandwidth = total bytes / slowest thread's time.
// A single thread runs on `cpu0`; N threads use one CPU per physical core first.
double sample_gbps(Op op, std::vector<Worker>& workers, size_t size, size_t passes, int cpu0,
                   const std::vector<int>& cpu_order) {
    std::atomic<int> ready{0};
    std::atomic<bool> go{false};
    std::vector<std::thread> threads;
    for (size_t t = 0; t < workers.size(); ++t) {
        const int cpu = workers.size() == 1 ? cpu0 : cpu_order[t % cpu_order.size()];
        threads.emplace_back([&, t, cpu] {
            ss::pin_to_cpu(cpu);
            ready.fetch_add(1);
            while (!go.load(std::memory_order_acquire)) {
            }
            run_passes(op, workers[t], size, passes);
        });
    }
    while (ready.load() < static_cast<int>(workers.size())) {
    }
    go.store(true, std::memory_order_release);
    for (auto& th : threads) th.join();

    double slowest = 0;
    for (auto& w : workers) slowest = std::max(slowest, w.elapsed_ns);
    const double bytes_per_pass = static_cast<double>(size) * (op == Op::Copy ? 2.0 : 1.0);
    const double total = bytes_per_pass * static_cast<double>(passes * workers.size());
    return total / slowest;  // bytes per ns == GB/s (1e9 bytes/s)
}

}  // namespace

int main(int argc, char** argv) {
    ss::Args args(argc, argv);
    const size_t min_bytes = static_cast<size_t>(args.num("min-kb", 16)) << 10;
    const size_t max_bytes = static_cast<size_t>(args.num("max-mb", 1024)) << 20;
    const int repeats = static_cast<int>(args.num("repeats", 5));
    const int nthreads = static_cast<int>(std::max(1L, args.num("threads", 1)));
    const int cpu0 = static_cast<int>(args.num("cpu", 0));
    // Total memory this run may hold at once: the spec's ~1 GB budget, further
    // capped at half of MemAvailable.
    const size_t budget =
        ss::cap_to_available(static_cast<size_t>(args.num("max-total-mb", 1024)) << 20);

    // Aim for ~512 MB of traffic per sample: long enough to be stable, short
    // enough that a full sweep stays in seconds.
    constexpr size_t kBytesPerSample = size_t{512} << 20;
    const std::vector<int> cpu_order = ss::cpus_one_per_core_first();
    if (nthreads > 1) {
        std::fprintf(stderr, "info: %d threads on cpus", nthreads);
        for (int t = 0; t < nthreads; ++t) std::fprintf(stderr, " %d", cpu_order[t % cpu_order.size()]);
        std::fprintf(stderr, " (one per physical core first)\n");
    }

    std::printf("op,threads,size_bytes,gbps_median,gbps_p10,gbps_p90\n");
    for (size_t size = min_bytes; size <= max_bytes; size *= 2) {
        const bool fits_one = size * nthreads <= budget;
        const bool fits_two = 2 * size * nthreads <= budget;  // copy needs src + dst
        if (!fits_one) {
            std::fprintf(stderr, "info: stopping at %zu MB x %d threads (budget %zu MB)\n",
                         size >> 20, nthreads, budget >> 20);
            break;
        }

        std::vector<Worker> workers(nthreads);
        for (auto& w : workers) {
            w.src = static_cast<uint64_t*>(ss::alloc_buffer(size, false));
            w.dst = fits_two ? static_cast<uint64_t*>(ss::alloc_buffer(size, false)) : nullptr;
            // Touch every page now so page faults don't land in the timed region.
            std::memset(w.src, 1, size);
            if (w.dst) std::memset(w.dst, 0, size);
        }

        const size_t passes = std::max<size_t>(1, kBytesPerSample / size);
        for (Op op : {Op::Read, Op::Write, Op::Copy}) {
            if (op == Op::Copy && !fits_two) continue;
            // Warm-up sample (discarded): faults in caches/TLB and wakes the cores up.
            sample_gbps(op, workers, size, std::max<size_t>(1, passes / 8), cpu0, cpu_order);
            std::vector<double> gbps;
            for (int r = 0; r < repeats; ++r) {
                gbps.push_back(sample_gbps(op, workers, size, passes, cpu0, cpu_order));
            }
            std::printf("%s,%d,%zu,%.3f,%.3f,%.3f\n", op_name(op), nthreads, size,
                        ss::percentile(gbps, 0.5), ss::percentile(gbps, 0.1),
                        ss::percentile(gbps, 0.9));
            std::fflush(stdout);
        }
        for (auto& w : workers) {
            free(w.src);
            free(w.dst);
        }
    }
    return 0;
}
