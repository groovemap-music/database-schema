#!/usr/bin/env bash
# Build the Neo4j baseline for gm-database-schema-gkt.1 from the SAME generator
# output the PostgreSQL side was loaded from.
#
# Throwaway spike harness. Build only — the measurement is `bench/runner.py`,
# which talks Bolt on one connection rather than spawning a cypher-shell per
# statement. gm-database-schema-9c8.3 could measure through a shell because its
# cheapest number was 11 ms; several numbers here are single-digit milliseconds
# and a process spawn would be most of them.
#
# The image is the digest `scripts/test-integration.sh` pins, which is the one
# both sibling spikes measured, so the three documents' ratios mean the same
# thing.
set -euo pipefail

NEO4J_IMAGE="${NEO4J_IMAGE:-neo4j:2026-community@sha256:dbc377fb9cd8fe8dabc19d3041b197d5ca0ef8bae514cea175b8df265e5b7a76}"
NEO4J_CONTAINER="${NEO4J_CONTAINER:-gmgkt1-neo4j}"
NEO4J_DATA_VOLUME="${NEO4J_DATA_VOLUME:-gmgkt1-neo4j-data}"
NEO4J_CSV_VOLUME="${NEO4J_CSV_VOLUME:-gmgkt1-csv}"
NEO4J_PASSWORD="${NEO4J_PASSWORD:-spike-password}"
NEO4J_PORT="${NEO4J_PORT:-57689}"
NEO4J_HEAP="${NEO4J_HEAP:-2G}"
NEO4J_PAGECACHE="${NEO4J_PAGECACHE:-2G}"

scale="${1:?usage: neo4j-build.sh <scale> <outdir> <csvdir>}"
outdir="${2:?usage: neo4j-build.sh <scale> <outdir> <csvdir>}"
csvdir="${3:?usage: neo4j-build.sh <scale> <outdir> <csvdir>}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
gen="$here/../gm-database-schema-9c8.1/generate.py"

mkdir -p "$outdir" "$csvdir"

if [[ ! -f "$csvdir/releases.csv" ]]; then
    echo "generating neo4j CSV at scale $scale into $csvdir"
    python3 "$gen" --target neo4j --scale "$scale" --outdir "$csvdir" \
        2>&1 | tee "$outdir/neo4j-generate-$scale.txt"
fi

docker rm --force "$NEO4J_CONTAINER" >/dev/null 2>&1 || true
docker volume rm --force "$NEO4J_DATA_VOLUME" "$NEO4J_CSV_VOLUME" >/dev/null 2>&1 || true
docker volume create "$NEO4J_DATA_VOLUME" >/dev/null
docker volume create "$NEO4J_CSV_VOLUME" >/dev/null

# Staged into a named volume rather than bind-mounted: a bind mount only works
# from a path Docker Desktop shares, and the importer sees an empty /import
# otherwise. `docker cp` does not care about file sharing.
echo "staging CSV into volume $NEO4J_CSV_VOLUME"
staging="$(docker create --volume "$NEO4J_CSV_VOLUME:/import" "$NEO4J_IMAGE" true)"
docker cp "$csvdir/." "$staging:/import/"
docker rm "$staging" >/dev/null

# `mb_relationships.csv` IS imported. It is where the generator puts its Discogs
# ALIAS_OF and MEMBER_OF rows, and it also carries the MusicBrainz rows typed
# MEMBER_OF that `augment.sql` resolves onto the PostgreSQL side through
# `graph.mb_relationship_type`. Both are inside `_PATH_REL_TYPES`, so both are
# traversable in the product, and leaving them out would make the two engines
# hold different graphs.
echo "importing"
docker run --rm \
    --volume "$NEO4J_DATA_VOLUME:/data" \
    --volume "$NEO4J_CSV_VOLUME:/import:ro" \
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
        -- neo4j 2>&1 | tail -30 | tee "$outdir/neo4j-import-$scale.txt"

echo "starting server on 127.0.0.1:$NEO4J_PORT"
docker run --detach --name "$NEO4J_CONTAINER" \
    --publish "127.0.0.1:$NEO4J_PORT:7687" \
    --volume "$NEO4J_DATA_VOLUME:/data" \
    --env "NEO4J_AUTH=neo4j/$NEO4J_PASSWORD" \
    --env "NEO4J_server_memory_heap_initial__size=$NEO4J_HEAP" \
    --env "NEO4J_server_memory_heap_max__size=$NEO4J_HEAP" \
    --env "NEO4J_server_memory_pagecache_size=$NEO4J_PAGECACHE" \
    "$NEO4J_IMAGE" >/dev/null

shell() { docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "$@"; }
for _ in $(seq 1 120); do shell "RETURN 1" >/dev/null 2>&1 && break; sleep 2; done
shell "RETURN 1" >/dev/null

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
    echo "scale: $scale   heap: $NEO4J_HEAP   pagecache: $NEO4J_PAGECACHE   image: $NEO4J_IMAGE"
    shell --format plain "CALL dbms.components() YIELD name, versions, edition RETURN name, versions, edition;"
    shell --format plain "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS nodes ORDER BY label;"
    shell --format plain "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS rels ORDER BY type;"
} | tee "$outdir/neo4j-stats-$scale.txt"

# The CSV has done its job and is a gigabyte nothing reads again.
docker volume rm --force "$NEO4J_CSV_VOLUME" >/dev/null 2>&1 || true
echo "neo4j ready on 127.0.0.1:$NEO4J_PORT"
