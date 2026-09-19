#!/usr/bin/env bash
# Measure the three ported reads against both PostgreSQL declarations.
#
# Throwaway spike harness for gm-database-schema-9c8.1. Assumes the container
# named by $PG_CONTAINER is already running with the product schema applied and
# the catalog loaded; ./README.md documents the whole sequence.
#
# Each (backend, query) pair gets one warm-up run, then one
# EXPLAIN (ANALYZE, BUFFERS) capture, then $ITERATIONS timed runs. The warm-up is
# not decoration: the first execution of a plan over a cold shared_buffers is
# measuring the page cache, and the question here is what a hot API read costs.
# Timings are reported individually so the spread is visible rather than hidden
# behind a mean.
set -euo pipefail

PG_CONTAINER="${PG_CONTAINER:-gm9c81-pg}"
PGPASSWORD="${PGPASSWORD:-spike-password}"
ITERATIONS="${ITERATIONS:-5}"
# A read family that cannot finish in this budget has already answered the
# question, so the timeout is recorded as the result rather than waited out.
STATEMENT_TIMEOUT="${STATEMENT_TIMEOUT:-900s}"

scale="${1:?usage: run-postgres.sh <scale> <outdir>}"
outdir="${2:?usage: run-postgres.sh <scale> <outdir>}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$outdir"

# psql runs INSIDE the container, so a host path handed to `-f` is not visible to
# it. Every script this harness runs is therefore fed on stdin as `-f -`.
psql_run() {
    docker exec -i -e PGPASSWORD="$PGPASSWORD" "$PG_CONTAINER" \
        psql -U groovemap -d groovemap -v ON_ERROR_STOP=1 "$@"
}

# ---------------------------------------------------------------------------
# Seed selection
# ---------------------------------------------------------------------------
#
# Seeds are chosen by DEGREE rather than hardcoded, so the same script produces a
# representative read at either scale.
#
# The artist sits at the 90th percentile of the release-degree distribution among
# artists that ALSO carry a MusicBrainz relationship. Three constraints force
# that choice. The single busiest artist is a pathological hub whose two-hop
# neighbourhood is most of the catalog, so it measures a read nobody issues
# twice. The median artist appears on one or two releases, so it measures nothing
# at all. And an artist with no MusicBrainz edge turns the third read family into
# a measurement of how fast an empty result comes back.
seed_artist="$(psql_run -tAc "
    WITH linked AS (
        SELECT DISTINCT discogs_artist_id FROM graph_mat.mb_artist
        WHERE discogs_artist_id IS NOT NULL
          AND mbid IN (SELECT source_mbid FROM graph_mat.mb_rel_artist_artist
                       UNION SELECT target_mbid FROM graph_mat.mb_rel_artist_artist)
    ),
    degrees AS (
        SELECT e.artist_id, count(*) AS degree
        FROM graph_mat.by_artist e
        JOIN linked ON linked.discogs_artist_id::text = e.artist_id
        GROUP BY e.artist_id
    )
    SELECT artist_id FROM degrees
    ORDER BY degree DESC
    OFFSET (SELECT (count(*) * 0.10)::bigint FROM degrees)
    LIMIT 1")"
seed_label="$(psql_run -tAc "
    SELECT label_id FROM graph_mat.on_label GROUP BY label_id ORDER BY count(*) DESC LIMIT 1")"

{
    echo "scale: $scale"
    echo "seed artist: $seed_artist"
    echo "seed artist by_artist degree: $(psql_run -tAc "SELECT count(*) FROM graph_mat.by_artist WHERE artist_id = '$seed_artist'")"
    echo "seed label: $seed_label"
    echo "seed label on_label degree: $(psql_run -tAc "SELECT count(*) FROM graph_mat.on_label WHERE label_id = '$seed_label'")"
    echo "seed artist mb degree: $(psql_run -tAc "
        SELECT count(*) FROM graph_mat.mb_rel_artist_artist e
        WHERE e.source_mbid IN (SELECT mbid FROM graph_mat.mb_artist WHERE discogs_artist_id = $seed_artist)
           OR e.target_mbid IN (SELECT mbid FROM graph_mat.mb_artist WHERE discogs_artist_id = $seed_artist)")"
    echo "server version: $(psql_run -tAc 'SHOW server_version')"
    echo "shared_buffers: $(psql_run -tAc 'SHOW shared_buffers')"
    echo "work_mem: $(psql_run -tAc 'SHOW work_mem')"
    echo "max_parallel_workers_per_gather: $(psql_run -tAc 'SHOW max_parallel_workers_per_gather')"
} | tee "$outdir/seeds-$scale.txt"

# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

measure() {
    local backend="$1" graph="$2" artist_relation="$3" query="$4"
    local label="$backend-$query-$scale"
    local sql="$here/queries/$query.sql"
    local target="$outdir/$label.txt"

    local -a vars=(
        -v "graph=$graph"
        -v "artist_relation=$artist_relation"
        -v "seed_artist=$seed_artist"
        -v "seed_label=$seed_label"
        -v "seed_artist_id=$seed_artist"
        -v collab_limit=50
    )

    {
        echo "== $label"
        echo "graph: $graph"
        echo "seed artist: $seed_artist   seed label: $seed_label"
    } > "$target"

    # Warm-up. Failure here (most often a statement timeout) is the result, so it
    # is recorded and the remaining phases are skipped rather than retried.
    if ! { echo "SET statement_timeout = '$STATEMENT_TIMEOUT';"; cat "$sql"; } \
            | psql_run "${vars[@]}" -f - > /dev/null 2> "$outdir/$label.err"; then
        {
            echo "-- warm-up FAILED after $STATEMENT_TIMEOUT"
            cat "$outdir/$label.err"
        } >> "$target"
        echo "$label: FAILED (see $target)"
        return 0
    fi
    rm -f "$outdir/$label.err"

    echo "-- EXPLAIN (ANALYZE, BUFFERS)" >> "$target"
    {
        echo "SET statement_timeout = '$STATEMENT_TIMEOUT';"
        echo "EXPLAIN (ANALYZE, BUFFERS, COSTS ON, TIMING ON)"
        cat "$sql"
    } | psql_run "${vars[@]}" -f - >> "$target" 2>&1

    echo "-- wall clock, $ITERATIONS runs" >> "$target"
    {
        echo "SET statement_timeout = '$STATEMENT_TIMEOUT';"
        echo "\\timing on"
        for _ in $(seq 1 "$ITERATIONS"); do
            echo "\\o /dev/null"
            cat "$sql"
            echo "\\o"
        done
    } | psql_run "${vars[@]}" -f - 2>&1 | grep '^Time:' >> "$target"

    echo "$label: $(grep -c '^Time:' "$target") timed runs"
}

for query in q1-collaborators q2-label-dna q3-mb-relationships; do
    measure views "graph.catalog" "graph.artist" "$query"
    measure materialized "graph_mat.catalog" "graph_mat.artist" "$query"
done

echo "postgres results in $outdir"
