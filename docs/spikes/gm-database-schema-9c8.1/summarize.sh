#!/usr/bin/env bash
# Collapse a results directory into the timing table used in the spike document.
#
# Throwaway spike harness for gm-database-schema-9c8.1.
#
# Reports the MEDIAN of the timed runs. A mean over five runs on a laptop is at
# the mercy of whatever else the machine did during run three; the median says
# what the read costs when nothing unusual happens, which is the number a
# capacity decision should turn on. The full run list is printed beside it so the
# spread stays visible.
set -euo pipefail

outdir="${1:?usage: summarize.sh <outdir> <scale>}"
scale="${2:?usage: summarize.sh <outdir> <scale>}"

median() {
    sort -n | awk '{ value[NR] = $1 }
        END {
            if (NR == 0) { print "n/a"; exit }
            if (NR % 2) { printf "%.1f", value[(NR + 1) / 2] }
            else { printf "%.1f", (value[NR / 2] + value[NR / 2 + 1]) / 2 }
        }'
}

# psql prints `Time: 12.345 ms` per run, after the `-- wall clock` marker that
# separates the timed runs from the EXPLAIN capture above them.
postgres_times() {
    local file="$1"
    [[ -f "$file" ]] || return 0
    sed -n '/-- wall clock/,$p' "$file" | awk '/^Time:/ { print $2 }'
}

# cypher-shell reports server-side time in two halves — planning-to-first-row and
# consuming the rest. Their sum is the figure comparable to psql's `\timing`:
# both start when the statement is submitted and end when the last row is out.
neo4j_times() {
    local file="$1"
    [[ -f "$file" ]] || return 0
    # ready to start consuming query after N ms, results consumed after another M ms
    #  1     2  3     4         5     6     7 8  9       10       11    12      13 14
    sed -n '/-- wall clock/,$p' "$file" \
        | awk '/ready to start consuming query after/ { print $7 + $13 }'
}

printf '%-22s %-14s %10s   %s\n' query backend "median ms" "runs (ms)"
printf '%-22s %-14s %10s   %s\n' ---------------------- -------------- ---------- ---------
for query in q1-collaborators q2-label-dna q3-mb-relationships; do
    for backend in views materialized neo4j; do
        file="$outdir/$backend-$query-$scale.txt"
        [[ "$backend" == neo4j ]] && file="$outdir/neo4j-$query-$scale.txt"

        if [[ -f "$file" ]] && grep -q 'warm-up FAILED' "$file"; then
            printf '%-22s %-14s %10s   %s\n' "$query" "$backend" "TIMEOUT" "exceeded the statement budget"
            continue
        fi

        if [[ "$backend" == neo4j ]]; then
            times="$(neo4j_times "$file" | tr '\n' ' ')"
            value="$(neo4j_times "$file" | median)"
        else
            times="$(postgres_times "$file" | tr '\n' ' ')"
            value="$(postgres_times "$file" | median)"
        fi
        printf '%-22s %-14s %10s   %s\n' "$query" "$backend" "$value" "${times:-not measured}"
    done
done
