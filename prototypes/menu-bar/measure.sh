#!/bin/sh
# PROTOTYPE, NOT SHIPPED. Idle cost of a running process, for the #111 spike:
#   ./measure.sh <pid> <seconds>
# CPU is the CPU time the process used over the window divided by the window
# (so a mostly-idle process is not rounded to 0.0 by a single `ps` sample);
# memory is the resident size (RSS) and the physical footprint at the end.
set -eu
pid=$1
window=${2:-180}

cputime() {
    # [[dd-]hh:]mm:ss.cc -> seconds
    ps -o time= -p "$pid" | awk -F'[:-]' '{ s = 0; for (i = 1; i <= NF; i++) s = s * 60 + $i; print s }'
}

c0=$(cputime)
peak=0
i=0
while [ "$i" -lt "$window" ]; do
    rss=$(ps -o rss= -p "$pid" | tr -d ' ')
    [ "$rss" -gt "$peak" ] && peak=$rss
    sleep 5
    i=$((i + 5))
done
c1=$(cputime)
rss=$(ps -o rss= -p "$pid" | tr -d ' ')
footprint=$(footprint -p "$pid" 2>/dev/null | sed -n 's/.*Footprint: //p' | head -1 || true)
wakeups=$(top -l 2 -s 1 -pid "$pid" -stats pid,idlew,cpu 2>/dev/null | tail -1 || true)
echo "pid=$pid window=${window}s cpu_seconds=$(echo "$c1 - $c0" | bc) \
cpu_percent=$(echo "scale=4; 100 * ($c1 - $c0) / $window" | bc) \
rss_kb=$rss peak_rss_kb=$peak footprint=${footprint:-?} top=[$wakeups]"
