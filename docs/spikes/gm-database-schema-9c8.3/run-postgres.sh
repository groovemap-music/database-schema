#!/usr/bin/env bash
# Measure the path prototypes against the loaded catalog.
#
# Throwaway spike harness for gm-database-schema-9c8.3. ./README.md documents the
# whole sequence; this script assumes the catalog is loaded and `augment.sql`,
# `edges.sql`, `bfs.sql` and `adjacency.sql` have been applied.
#
# Endpoints are HARDCODED, not chosen by degree at run time. The sibling spike
# picked its seeds with `ORDER BY degree DESC OFFSET n` and no tiebreak, so a
# rerun could silently measure a different vertex. Every id below comes from one
# exhaustive breadth-first search out of artist 5665 — 9c8.1's seed artist — by
# the rule stated in the spike document, and is then written down.
#
# Three tables come out of this, because three different questions are being
# asked and one grid over (case x cap x variant) would answer none of them well:
#
#   A  cost against the distance actually found, at the product's own cap of 10
#   B  cost against the DEPTH CAP when there is nothing to find, which is the
#      only case in which the cap is what decides the work
#   C  cost of bounded traversal at *1..n
set -euo pipefail

PG_CONTAINER="${PG_CONTAINER:-gm9c83-pg}"
PGPASSWORD="${PGPASSWORD:-spike-password}"
out="${1:?usage: run-postgres.sh <results-dir>}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$out"

psql_run() {
    docker exec -i -e PGPASSWORD="$PGPASSWORD" "$PG_CONTAINER" \
        psql -U groovemap -d groovemap -v ON_ERROR_STOP=1 "$@"
}

# case | source kind,key | target kind,key | true distance
CASES=(
    "d1-artist-artist|a,5665|a,9458|1"
    "d1-artist-release|a,5665|r,3638|1"
    "d2-artist-artist|a,5665|a,1|2"
    "d3-artist-artist|a,5665|a,2|3"
    "d4-artist-artist|a,5665|a,9|4"
    "d5-artist-artist|a,55563|a,4814|5"
    "d6-artist-artist|a,55563|a,32509|6"
    "unreachable|a,5665|a,103111|none"
)
# Every id above comes from two exhaustive searches, and the two roots are
# chosen rather than found: artist 5665 is gm-database-schema-9c8.1's seed, and
# artist 55563 is the lowest-numbered degree-1 artist on the far rim of 5665's
# search. From 5665 the whole 1,390,450-vertex component falls inside four
# levels, so cases at distance 5 and 6 have to start from the rim. From 55563
# the eccentricity is 6 and 557 vertices sit at that distance. Nothing in this
# catalog is at distance 7 or beyond from either root, which is the fact the
# Verdict turns on: the fifteen Genre vertices carry about 137,000 edges each,
# any two releases are within four hops through one of them, and an artist is
# one hop from a release.
# There is no case at distance 5 or beyond, and that absence is a result rather
# than an omission. One exhaustive search out of artist 5665 reaches all
# 1,390,450 vertices of the component in four levels, so no vertex in this
# catalog is at distance 5, 6, 8 or 10 from it. The fifteen Genre vertices carry
# about 137,000 edges each; any two releases are within four hops through one of
# them, and an artist is one hop from a release. The spike document works the
# consequence through — a depth cap above 4 cannot change an answer here, it can
# only change how long a miss takes to admit it.

# `temp_file_limit` is a disk guard, and for p1 it is also the measurement.
# `WITH RECURSIVE` always materializes its whole result before the outer
# `ORDER BY depth LIMIT 1` can look at it, and p1's result is every simple path
# up to the cap. Left unbounded it writes that to disk until the volume fills:
# the first attempt at this spike took the host from 6.0 GB free to 2.6 GB in
# under two minutes on ONE statement. Bounded, the backend raises
# "temporary file size exceeds temp_file_limit" instead, which says the same
# thing in a form that can be reported and does not endanger the machine.
TEMP_FILE_LIMIT="${TEMP_FILE_LIMIT:-512MB}"

one_run() {
    local sql="$1" budget="$2" raw
    if ! raw="$(psql_run -q \
                    -c "SET statement_timeout = '${budget}'" \
                    -c "SET temp_file_limit = '${TEMP_FILE_LIMIT}'" \
                    -c '\timing on' -c "$sql" 2>&1)"; then
        if grep -q 'temp_file_limit' <<<"$raw"; then echo "over-temp-limit"; else echo timeout; fi
        return
    fi
    awk '/^Time: /{ t = $2 } END { print (t == "" ? "timeout" : t) }' <<<"$raw"
}

# One warm-up, discarded, then timed runs: five when the warm-up came back
# inside three seconds, three inside thirty, two beyond that. Five runs of a
# two-minute exhaustion would be ten minutes of wall clock spent sharpening a
# median that is already unambiguous.
timed() {
    local variant="$1" case_name="$2" cap="$3" sql="$4" budget="$5"
    local warm runs ms i
    warm="$(one_run "$sql" "$budget")"
    if [[ "$warm" == "timeout" || "$warm" == "over-temp-limit" ]]; then
        printf '%s\t%s\t%s\t-\t%s\n' "$variant" "$case_name" "$cap" "$warm" | tee -a "$out/timings.tsv"
        return 1
    fi
    runs="$(awk -v w="$warm" 'BEGIN { print (w < 3000 ? 5 : (w < 30000 ? 3 : 2)) }')"
    for ((i = 1; i <= runs; i++)); do
        ms="$(one_run "$sql" "$budget")"
        printf '%s\t%s\t%s\t%d\t%s\n' "$variant" "$case_name" "$cap" "$i" "$ms" | tee -a "$out/timings.tsv"
    done
    return 0
}

node_id() { psql_run -tAc "SELECT node_id FROM path.node WHERE kind = '${1%%,*}' AND key = '${1##*,}'"; }

p1_sql() { # sk skey tk tkey cap
    sed -e "s/:'sk'/'$1'/g" -e "s/:'skey'/'$2'/g" -e "s/:'tk'/'$3'/g" \
        -e "s/:'tkey'/'$4'/g" -e "s/:depth/$5/g" "$here/queries/p1-naive-recursive-cte.sql"
}

printf 'variant\tcase\tdepth_cap\trun\tms\n' > "$out/timings.tsv"

# ---------------------------------------------------------------------------
# Table A — cost against the distance found, at the product's own cap of 10
# ---------------------------------------------------------------------------

echo "== table A: cap 10, every case =="
for entry in "${CASES[@]}"; do
    IFS='|' read -r name src dst _d <<<"$entry"
    sk="${src%%,*}"; skey="${src##*,}"; tk="${dst%%,*}"; tkey="${dst##*,}"
    s_id="$(node_id "$src")"; t_id="$(node_id "$dst")"
    # p1 is bounded by the product's own 120 s Neo4j transaction timeout:
    # "did not finish inside the budget" is the measurement, not a number. Two
    # cases, not eight: the answer does not vary by case, and the cap sweep in
    # table B is where the interesting part of it lives.
    case "$name" in
        d1-artist-artist | d2-artist-artist)
            timed p1-naive-cte "$name" 10 "$(p1_sql "$sk" "$skey" "$tk" "$tkey" 10)" 120s || true ;;
    esac
    timed p2-bfs-uni  "$name" 10 "SELECT found, depth, visited FROM path.shortest_uni('$sk','$skey','$tk','$tkey',10) AS t" 900s || true
    timed p3-bfs-bi   "$name" 10 "SELECT found, depth, visited FROM path.shortest_bi('$sk','$skey','$tk','$tkey',10) AS t"  900s || true
    timed p4-adj-bi   "$name" 10 "SELECT found, depth, visited FROM path.adj_shortest_bi($s_id,$t_id,10) AS t"              900s || true
done

# ---------------------------------------------------------------------------
# Table B — cost against the depth cap when there is nothing to find
# ---------------------------------------------------------------------------
#
# Caps 5 and above are one measurement, not four: the component out of artist
# 5665 is exhausted at depth 6, so the loop leaves at the same place whether the
# cap said 6, 8 or 10. Cap 10 is measured and the identity is stated rather than
# paid for three more times.

echo "== table B: the unreachable pair against the cap =="
u_s="$(node_id a,5665)"; u_t="$(node_id a,103111)"
for cap in 1 2 3 4 10; do
    # Cap 10 is skipped for the one-ended search and stated instead: the
    # component is exhausted at level 5, so caps 5 and above do the work of cap
    # 4 plus one empty level.
    [[ "$cap" == 10 ]] || timed p2-bfs-uni unreachable-cap "$cap" "SELECT found, visited FROM path.shortest_uni('a','5665','a','103111',$cap) AS t" 900s || true
    timed p3-bfs-bi  unreachable-cap "$cap" "SELECT found, visited FROM path.shortest_bi('a','5665','a','103111',$cap) AS t"  900s || true
    timed p4-adj-bi  unreachable-cap "$cap" "SELECT found, visited FROM path.adj_shortest_bi($u_s,$u_t,$cap) AS t"            900s || true
done

# The naive CTE against the cap, on the single easiest artist-to-artist case.
# Escalation stops at the first timeout: the recursion is monotone in the cap, so
# a cap that does not finish tells us every larger cap does not finish either.
echo "== table B: the naive recursive CTE against the cap =="
for cap in 1 2 3 4 5 6 8 10; do
    timed p1-naive-cte d2-artist-artist "$cap" "$(p1_sql a 5665 a 1 "$cap")" 120s || break
done

# ---------------------------------------------------------------------------
# Table C — bounded traversal, get_explore_traversal's *1..n
# ---------------------------------------------------------------------------

echo "== table C: bounded traversal =="
for hops in 1 2 3; do
    timed p5-traverse explore-artist-5665 "$hops" "SELECT count(*) FROM path.traverse('a','5665',$hops)" 900s || true
done

# ---------------------------------------------------------------------------
# Answers, so every timing is attached to a result rather than to a duration,
# and so the two searches can be checked against each other
# ---------------------------------------------------------------------------

echo "== answers =="
{
    echo "case,variant,found,depth,visited,level_sizes"
    for entry in "${CASES[@]}"; do
        IFS='|' read -r name src dst _d <<<"$entry"
        sk="${src%%,*}"; skey="${src##*,}"; tk="${dst%%,*}"; tkey="${dst##*,}"
        s_id="$(node_id "$src")"; t_id="$(node_id "$dst")"
        psql_run -tAF, -c "SELECT '$name','p2-bfs-uni', found, depth, visited, level_sizes FROM path.shortest_uni('$sk','$skey','$tk','$tkey',10) AS t"
        psql_run -tAF, -c "SELECT '$name','p3-bfs-bi',  found, depth, visited, level_sizes FROM path.shortest_bi('$sk','$skey','$tk','$tkey',10) AS t"
        psql_run -tAF, -c "SELECT '$name','p4-adj-bi',  found, depth, visited, level_sizes FROM path.adj_shortest_bi($s_id,$t_id,10) AS t"
    done
} > "$out/answers.csv"

echo "wrote $out/timings.tsv and $out/answers.csv"
