// latency.cpp — measure load-to-use memory latency vs working-set size.
//
// Technique: "pointer chasing". We build a linked list inside a buffer where
// each node lives on its own 64-byte cache line, and the order of nodes is a
// single random cycle. Walking it (p = p->next) creates a chain of *dependent*
// loads: the CPU can't issue load N+1 until load N returns its address. So
// time / steps = average latency of one load from wherever that working set
// fits (L1, L2, L3 or DRAM). Randomising the order defeats the hardware
// prefetcher, which would otherwise hide the latency.
//
// Usage:
//   latency [--max-mb 256] [--min-kb 4] [--repeats 5] [--cpu 0] [--seed 42]
// Output (stdout, CSV):
//   size_bytes,ns_median,ns_p10,ns_p90
#include <cstdint>
#include <cstdio>
#include <numeric>
#include <random>
#include <vector>

#include "common.hpp"

namespace {

// One node per cache line, so every hop touches a *new* line. If two nodes
// shared a line, half the hops would hit a line we just loaded and we would
// under-report latency.
struct alignas(ss::kCacheLine) Node {
    Node* next;
    char pad[ss::kCacheLine - sizeof(Node*)];
};
static_assert(sizeof(Node) == ss::kCacheLine, "Node must be exactly one cache line");

// Written at the end so the compiler can't prove the chase loop is dead code
// and delete it. `volatile` forces the store to actually happen.
volatile uintptr_t g_sink = 0;

// Sattolo's algorithm: a Fisher–Yates variant that picks j in [0, i) instead of
// [0, i]. That single change guarantees the result is ONE cycle through all n
// elements (a "cyclic permutation"). Plain Fisher–Yates produces random
// permutations that usually split into several short cycles, and a short cycle
// would keep the chase inside a small, cache-resident subset of the buffer.
std::vector<uint32_t> sattolo_cycle(size_t n, std::mt19937_64& rng) {
    std::vector<uint32_t> a(n);
    std::iota(a.begin(), a.end(), 0u);
    for (size_t i = n - 1; i > 0; --i) {
        std::uniform_int_distribution<size_t> pick(0, i - 1);
        std::swap(a[i], a[pick(rng)]);
    }
    return a;  // a[i] = index of the node that follows node i
}

// Link the first n nodes of `nodes` in the order given by `next_index`.
void link_nodes(Node* nodes, const std::vector<uint32_t>& next_index) {
    for (size_t i = 0; i < next_index.size(); ++i) nodes[i].next = &nodes[next_index[i]];
}

// Follow the chain `steps` times. The loop body is a single dependent load, so
// its cost is the memory latency (loop overhead overlaps with the wait).
Node* chase(Node* p, size_t steps) {
    for (size_t i = 0; i < steps; ++i) p = p->next;
    return p;
}

// Working-set sizes: powers of two from min to max, plus the 1.5x midpoint
// between each pair (4K, 6K, 8K, 12K, ...). Midpoints matter because real
// caches are often not powers of two (e.g. a 48 KB L1d or a 1.25 MB L2).
std::vector<size_t> make_sizes(size_t min_bytes, size_t max_bytes) {
    std::vector<size_t> sizes;
    for (size_t s = min_bytes; s <= max_bytes; s *= 2) {
        sizes.push_back(s);
        size_t mid = s + s / 2;
        if (mid <= max_bytes) sizes.push_back(mid);
    }
    return sizes;
}

}  // namespace

int main(int argc, char** argv) {
    ss::Args args(argc, argv);
    const size_t max_bytes_req = static_cast<size_t>(args.num("max-mb", 256)) << 20;
    const size_t min_bytes = static_cast<size_t>(args.num("min-kb", 4)) << 10;
    const int repeats = static_cast<int>(args.num("repeats", 5));
    const int cpu = static_cast<int>(args.num("cpu", 0));
    std::mt19937_64 rng(static_cast<uint64_t>(args.num("seed", 42)));

    ss::pin_to_cpu(cpu);
    const size_t max_bytes = ss::cap_to_available(max_bytes_req);

    // One buffer sized for the largest working set; smaller sets use a prefix.
    auto* nodes = static_cast<Node*>(ss::alloc_buffer(max_bytes, /*hugepages=*/false));
    if (!nodes) {
        std::fprintf(stderr, "error: failed to allocate %zu MB\n", max_bytes >> 20);
        return 1;
    }

    std::printf("size_bytes,ns_median,ns_p10,ns_p90\n");
    for (size_t size : make_sizes(min_bytes, max_bytes)) {
        const size_t n = size / ss::kCacheLine;
        link_nodes(nodes, sattolo_cycle(n, rng));

        // Enough hops that each sample takes milliseconds (timer noise ~0), but
        // at least one full lap so every line in the set is exercised.
        const size_t steps = std::max<size_t>(n, size_t{1} << 21);

        // Warm-up lap: pulls the set into the caches (and TLB) it fits in, so we
        // measure steady state rather than first-touch page faults.
        Node* p = chase(&nodes[0], n);

        std::vector<double> ns_per_access;
        for (int r = 0; r < repeats; ++r) {
            double t0 = ss::now_ns();
            p = chase(p, steps);
            double t1 = ss::now_ns();
            ns_per_access.push_back((t1 - t0) / static_cast<double>(steps));
        }
        g_sink += reinterpret_cast<uintptr_t>(p);  // consume the result

        std::printf("%zu,%.3f,%.3f,%.3f\n", size, ss::percentile(ns_per_access, 0.5),
                    ss::percentile(ns_per_access, 0.1), ss::percentile(ns_per_access, 0.9));
        std::fflush(stdout);
    }
    free(nodes);
    return 0;
}
