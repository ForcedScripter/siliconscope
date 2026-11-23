#!/usr/bin/env bash
# run_perf.sh — wrap a probe run in `perf stat`, degrading gracefully.
#
# Usage:
#   scripts/run_perf.sh [--out FILE] -- <command> [args...]
#   e.g. scripts/run_perf.sh --out reports/perf_latency_64mb.txt -- \
#            ./probe/build/latency --min-kb 65536 --max-mb 64 --repeats 3
#
# Hardware counters need (a) a perf binary matching the running kernel, (b) a
# PMU exposed to this (possibly virtual) machine, and (c) permission:
# /proc/sys/kernel/perf_event_paranoid <= 2 for user-space-only counting.
# If any of these is missing we print why and exit 0, so `make` keeps going.
set -u

EVENTS="cycles,instructions,cache-misses,cache-references,dTLB-load-misses"
OUT=""
if [[ "${1:-}" == "--out" ]]; then OUT="$2"; shift 2; fi
if [[ "${1:-}" == "--" ]]; then shift; fi
if [[ $# -eq 0 ]]; then
    echo "usage: $0 [--out FILE] -- <command> [args...]" >&2
    exit 2
fi

explain_and_exit() {
    echo "run_perf: perf counters unavailable: $1" >&2
    echo "run_perf: running the command without counters instead." >&2
    "$@" >/dev/null
    exit 0
}

# 1) Find a perf binary. Ubuntu's /usr/bin/perf is a wrapper that refuses to run
#    when no linux-tools package matches `uname -r` (always true on WSL2 and many
#    cloud kernels), so also try any installed versioned binary directly.
PERF=""
if command -v perf >/dev/null 2>&1 && perf --version >/dev/null 2>&1; then
    PERF="perf"
else
    for p in /usr/lib/linux-tools/*/perf /usr/lib/linux-tools-*/perf; do
        if [[ -x "$p" ]] && "$p" --version >/dev/null 2>&1; then PERF="$p"; break; fi
    done
fi
[[ -z "$PERF" ]] && explain_and_exit "no working perf binary for kernel $(uname -r)" "$@"

# 2) Permission check. paranoid 3/4 (Debian/Ubuntu patches) block unprivileged use.
PARANOID=$(cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null || echo "unknown")
if [[ "$PARANOID" =~ ^[0-9]+$ ]] && (( PARANOID > 2 )) && [[ $EUID -ne 0 ]]; then
    explain_and_exit "perf_event_paranoid=$PARANOID (>2) and not root" "$@"
fi

# 3) Run it. perf prints "<not supported>" for events the (virtual) PMU lacks;
#    we keep the output either way so the reader can see exactly what happened.
TMP=$(mktemp)
"$PERF" stat -e "$EVENTS" -x, -o "$TMP" -- "$@" >/dev/null
RC=$?
if [[ $RC -ne 0 ]]; then
    explain_and_exit "perf stat exited $RC: $(head -c 300 "$TMP")" "$@"
fi
report() {
    echo "# perf: $PERF ($("$PERF" --version 2>/dev/null))"
    echo "# kernel: $(uname -r)  perf_event_paranoid=$PARANOID"
    echo "# command: $*"
    echo "# columns: value,unit,event,run_time,pct_running(,metric,...)"
    grep -v '^#' "$TMP" | grep -v '^$'
}
if [[ -n "$OUT" ]]; then report "$@" > "$OUT"; else report "$@"; fi
if grep -q "not supported\|not counted" "$TMP"; then
    echo "run_perf: some events are <not supported>/<not counted> — the (virtual) PMU" \
         "does not expose them. Results are partial." >&2
fi
rm -f "$TMP"
exit 0
