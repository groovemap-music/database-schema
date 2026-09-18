#!/usr/bin/env bash
# Capture the plans behind the timings, and keep them.
#
# gm-database-schema-9c8.1's reviewer could not audit its Neo4j access counts
# because the PROFILE capture had been discarded. Everything this script produces
# is written to the results directory and the load-bearing parts are pasted into
# the spike document.
#
# Two captures, because the two prototypes need different instruments:
#
#   p1  is one statement, so EXPLAIN (ANALYZE, BUFFERS) sees all of it. Only the
#       caps at which it finishes can be captured at all; above those the
#       statement never produces a plan because it never produces a result.
#   p2/p3/p4  are PL/pgSQL loops, so EXPLAIN of the call shows a Function Scan
#       and nothing else. `auto_explain` with `log_nested_statements` is what
#       reaches the per-level expansion inside, which is the statement whose cost
#       the whole spike is about.
set -euo pipefail

PG_CONTAINER="${PG_CONTAINER:-gm9c83-pg}"
PGPASSWORD="${PGPASSWORD:-spike-password}"
out="${1:?usage: explain-postgres.sh <results-dir>}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$out"

psql_run() {
    docker exec -i -e PGPASSWORD="$PGPASSWORD" "$PG_CONTAINER" \
        psql -U groovemap -d groovemap -v ON_ERROR_STOP=1 "$@"
}

p1_sql() {
    sed -e "s/:'sk'/'$1'/g" -e "s/:'skey'/'$2'/g" -e "s/:'tk'/'$3'/g" \
        -e "s/:'tkey'/'$4'/g" -e "s/:depth/$5/g" "$here/queries/p1-naive-recursive-cte.sql"
}

# ---------------------------------------------------------------------------
# p1, at the caps where it returns
# ---------------------------------------------------------------------------

: > "$out/explain-p1.txt"
for cap in 1 2 3 4; do
    {
        echo "== p1-naive-cte  a:5665 -> a:1  cap $cap"
        psql_run -q \
            -c "SET statement_timeout = '120s'" \
            -c "SET temp_file_limit = '512MB'" \
            -c "EXPLAIN (ANALYZE, BUFFERS) $(p1_sql a 5665 a 1 "$cap")" 2>&1 || true
        # A cap the statement cannot finish has no plan to capture, and the
        # error it raises instead is the thing worth writing down.
        echo
    } >> "$out/explain-p1.txt"
done

# ---------------------------------------------------------------------------
# The per-level expansion, through auto_explain
# ---------------------------------------------------------------------------
#
# The container log is the sink, so the marker below is what separates one
# capture from the previous contents of that log.

marker="gm9c83-explain-$(date +%s)"
psql_run -q -c "DO \$\$ BEGIN RAISE LOG '$marker start'; END \$\$"

capture() { # label statement
    psql_run -q \
        -c "LOAD 'auto_explain'" \
        -c "SET auto_explain.log_min_duration = 0" \
        -c "SET auto_explain.log_analyze = on" \
        -c "SET auto_explain.log_buffers = on" \
        -c "SET auto_explain.log_timing = on" \
        -c "SET auto_explain.log_nested_statements = on" \
        -c "SET auto_explain.log_format = text" \
        -c "SET statement_timeout = '900s'" \
        -c "DO \$\$ BEGIN RAISE LOG '$marker $1'; END \$\$" \
        -c "$2" > /dev/null 2>&1 || true
}

# Distance 4, both ends ordinary: the case where the frontier crosses the Genre
# vertices in both directions and bidirectional search still has to pay for it.
capture "p2-uni-d4"   "SELECT found, depth FROM path.shortest_uni('a','5665','a','9',10) AS t"
capture "p3-bi-d4"    "SELECT found, depth FROM path.shortest_bi('a','5665','a','9',10) AS t"
capture "p4-adjbi-d4" "SELECT found, depth FROM path.adj_shortest_bi(
                           (SELECT node_id FROM path.node WHERE kind='a' AND key='5665'),
                           (SELECT node_id FROM path.node WHERE kind='a' AND key='9'), 10) AS t"
# Distance 5 from the rim, where searching from both ends is worth sixty times
# searching from one.
capture "p3-bi-d5"    "SELECT found, depth FROM path.shortest_bi('a','55563','a','4814',10) AS t"
# The traversal at the hop count that falls over.
capture "p5-traverse-3" "SELECT count(*) FROM path.traverse('a','5665',3)"

psql_run -q -c "DO \$\$ BEGIN RAISE LOG '$marker end'; END \$\$"

# `awk` leaves the pipe early once it has the closing marker, so `docker logs`
# takes a SIGPIPE that `pipefail` would otherwise treat as a failure.
docker logs "$PG_CONTAINER" 2>&1 | awk -v m="$marker" '
    index($0, m " start") { on = 1 }
    on { print }
    index($0, m " end") { found = 1; exit }
    END { exit 0 }' > "$out/explain-levels.txt" || true

echo "wrote $out/explain-p1.txt ($(wc -l < "$out/explain-p1.txt") lines)"
echo "wrote $out/explain-levels.txt ($(wc -l < "$out/explain-levels.txt") lines)"
