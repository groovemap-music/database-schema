#!/usr/bin/env bash
# Build the Neo4j baseline for gm-database-schema-9c8.1 and measure the same
# three reads against it.
#
# Throwaway spike harness. The image is the digest the repository's own
# integration tier pins, so the baseline is the Neo4j this project already runs
# rather than whatever `:latest` resolved to on the day.
#
# The graph is built with `neo4j-admin database import full` rather than with
# LOAD CSV or driver writes. At ten million relationships the bulk importer is
# the only loader that finishes in a sane time, and the resulting store is the
# one a real backfill would produce. The CSV it reads comes from the same
# generator, in the same order, as the documents loaded into PostgreSQL.
set -euo pipefail

NEO4J_IMAGE="${NEO4J_IMAGE:-neo4j:2026-community@sha256:dbc377fb9cd8fe8dabc19d3041b197d5ca0ef8bae514cea175b8df265e5b7a76}"
NEO4J_CONTAINER="${NEO4J_CONTAINER:-gm9c81-neo4j}"
NEO4J_DATA_VOLUME="${NEO4J_DATA_VOLUME:-gm9c81-neo4j-data}"
NEO4J_PASSWORD="${NEO4J_PASSWORD:-spike-password}"
NEO4J_HEAP="${NEO4J_HEAP:-2G}"
NEO4J_PAGECACHE="${NEO4J_PAGECACHE:-2G}"
ITERATIONS="${ITERATIONS:-5}"

scale="${1:?usage: run-neo4j.sh <scale> <outdir> <csvdir> <seed_artist> <seed_label>}"
outdir="${2:?usage: run-neo4j.sh <scale> <outdir> <csvdir> <seed_artist> <seed_label>}"
csvdir="${3:?usage: run-neo4j.sh <scale> <outdir> <csvdir> <seed_artist> <seed_label>}"
seed_artist="${4:?seed artist id, matching the PostgreSQL run}"
seed_label="${5:?seed label id, matching the PostgreSQL run}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$outdir" "$csvdir"

# ---------------------------------------------------------------------------
# Generate and import
# ---------------------------------------------------------------------------

echo "generating neo4j CSV at scale $scale into $csvdir"
python3 "$here/generate.py" --target neo4j --scale "$scale" --outdir "$csvdir" 2>&1 | tee "$outdir/neo4j-generate-$scale.txt"

docker rm --force "$NEO4J_CONTAINER" >/dev/null 2>&1 || true
docker volume rm --force "$NEO4J_DATA_VOLUME" >/dev/null 2>&1 || true
docker volume create "$NEO4J_DATA_VOLUME" >/dev/null

echo "importing"
# `--id-type=string` because every id in this catalog is a Discogs id carried as
# text, or a genre/style name. `--skip-duplicate-nodes` and
# `--skip-bad-relationships` are OFF: a dropped row would silently change the
# cardinality the timings are being compared at, and the import should fail
# loudly instead.
/usr/bin/time -p docker run --rm \
    --volume "$NEO4J_DATA_VOLUME:/data" \
    --volume "$csvdir:/import:ro" \
    --env NEO4J_ACCEPT_LICENSE_AGREEMENT=yes \
    "$NEO4J_IMAGE" \
    neo4j-admin database import full \
        --id-type=string \
        --overwrite-destination \
        --nodes=Artist=/import/artists.csv \
        --nodes=Label=/import/labels.csv \
        --nodes=Master=/import/masters.csv \
        --nodes=Release=/import/releases.csv \
        --nodes=Genre=/import/genres.csv \
        --nodes=Style=/import/styles.csv \
        --relationships=BY=/import/by_artist.csv \
        --relationships=ON=/import/on_label.csv \
        --relationships=IS=/import/in_genre.csv \
        --relationships=IS=/import/in_style.csv \
        --relationships=DERIVED_FROM=/import/derived_from.csv \
        --relationships=BY=/import/master_by_artist.csv \
        --relationships=IS=/import/master_in_genre.csv \
        --relationships=IS=/import/master_in_style.csv \
        --relationships=/import/mb_relationships.csv \
        -- neo4j 2>&1 | tail -40 | tee "$outdir/neo4j-import-$scale.txt"
# `--relationships` takes a variable number of files and the MusicBrainz file
# passes no type prefix, because its rows carry a `:TYPE` column — the ported
# Cypher matches an untyped relationship, so the store has to hold several types
# in one space. That makes the option greedy enough to swallow the trailing
# database name, which is what the `--` above stops.

echo "starting server"
docker run --detach --name "$NEO4J_CONTAINER" \
    --publish 127.0.0.1:57687:7687 \
    --volume "$NEO4J_DATA_VOLUME:/data" \
    --env "NEO4J_AUTH=neo4j/$NEO4J_PASSWORD" \
    --env "NEO4J_server_memory_heap_initial__size=$NEO4J_HEAP" \
    --env "NEO4J_server_memory_heap_max__size=$NEO4J_HEAP" \
    --env "NEO4J_server_memory_pagecache_size=$NEO4J_PAGECACHE" \
    "$NEO4J_IMAGE" >/dev/null

shell() {
    docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "$@"
}

for _ in $(seq 1 90); do
    if shell "RETURN 1" >/dev/null 2>&1; then break; fi
    sleep 2
done
shell "RETURN 1" >/dev/null

# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------
#
# The bulk importer builds no schema indexes, and every one of the three reads
# starts from a `{id: ...}` lookup. Without these the baseline would be measuring
# a label scan of a million nodes, which would flatter PostgreSQL for the wrong
# reason. These are the same lookup indexes the graph enricher creates.
shell <<'CYPHER'
CREATE INDEX artist_id IF NOT EXISTS FOR (a:Artist) ON (a.id);
CREATE INDEX label_id IF NOT EXISTS FOR (l:Label) ON (l.id);
CREATE INDEX release_id IF NOT EXISTS FOR (r:Release) ON (r.id);
CREATE INDEX master_id IF NOT EXISTS FOR (m:Master) ON (m.id);
CREATE INDEX genre_name IF NOT EXISTS FOR (g:Genre) ON (g.name);
CREATE INDEX style_name IF NOT EXISTS FOR (s:Style) ON (s.name);
CALL db.awaitIndexes(600);
CYPHER

{
    echo "scale: $scale"
    echo "seed artist: $seed_artist   seed label: $seed_label"
    echo "heap: $NEO4J_HEAP   pagecache: $NEO4J_PAGECACHE"
    shell --format plain "CALL dbms.components() YIELD name, versions, edition RETURN name, versions, edition;"
    shell --format plain "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS nodes ORDER BY label;"
    shell --format plain "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS rels ORDER BY type;"
} | tee "$outdir/neo4j-stats-$scale.txt"

# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------
#
# cypher-shell reports server-side time as "ready to start consuming query after
# N ms, results consumed after another M ms". Their sum is the query's wall clock
# inside the server, which is the number comparable to psql's `\timing` — both
# exclude the client's own startup and both include returning every row.

params() {
    echo ":param artist_id => '$seed_artist';"
    echo ":param label_id => '$seed_label';"
    echo ":param discogs_id => '$seed_artist';"
    echo ":param depth => 2;"
    echo ":param limit => 50;"
}

measure() {
    local query="$1"
    local label="neo4j-$query-$scale"
    local cypher="$here/queries/$query.cypher"
    local target="$outdir/$label.txt"

    { echo "== $label"; echo "seed artist: $seed_artist   seed label: $seed_label"; } > "$target"

    # Warm-up, discarded.
    { params; cat "$cypher"; } | shell --format plain >/dev/null

    echo "-- PROFILE" >> "$target"
    { params; echo "PROFILE"; cat "$cypher"; } | shell --format verbose >> "$target" 2>&1

    echo "-- wall clock, $ITERATIONS runs" >> "$target"
    for _ in $(seq 1 "$ITERATIONS"); do
        { params; cat "$cypher"; } | shell --format verbose 2>&1 \
            | grep -E 'ready to start consuming query after' >> "$target" || true
    done

    echo "$label: $(grep -c 'ready to start consuming' "$target") timed runs"
}

for query in q1-collaborators q2-label-dna q3-mb-relationships; do
    measure "$query"
done

echo "neo4j results in $outdir"
