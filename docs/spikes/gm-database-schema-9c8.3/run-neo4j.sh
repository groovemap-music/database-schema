#!/usr/bin/env bash
# Build the Neo4j baseline for gm-database-schema-9c8.3 and measure `shortestPath`
# and the explore traversal against it.
#
# Throwaway spike harness. The image is the digest `scripts/test-integration.sh`
# pins, the same one gm-database-schema-9c8.1 measured, so the baseline is the
# Neo4j this project already runs.
#
# The graph is the sibling spike's graph plus nothing. `mb_relationships.csv` IS
# imported here, unlike in a naive reading of 9c8.1: that file is where the
# generator puts its Discogs `ALIAS_OF` and `MEMBER_OF` rows, and it also carries
# MusicBrainz rows typed `MEMBER_OF`. Both are inside `_PATH_REL_TYPES`, so both
# are traversable by `shortestPath` in the product, and `augment.sql` puts the
# same rows on the PostgreSQL side. The remaining MusicBrainz types
# (`COLLABORATED_WITH` and the rest) are imported and are simply never matched,
# which is what the product store looks like.
#
# The PROFILE output is KEPT, in the results directory and in the spike document.
# The sibling spike's reviewer could not audit its Neo4j access counts because
# the capture had been discarded.
set -euo pipefail

NEO4J_IMAGE="${NEO4J_IMAGE:-neo4j:2026-community@sha256:dbc377fb9cd8fe8dabc19d3041b197d5ca0ef8bae514cea175b8df265e5b7a76}"
NEO4J_CONTAINER="${NEO4J_CONTAINER:-gm9c83-neo4j}"
NEO4J_DATA_VOLUME="${NEO4J_DATA_VOLUME:-gm9c83-neo4j-data}"
NEO4J_CSV_VOLUME="${NEO4J_CSV_VOLUME:-gm9c83-csv}"
NEO4J_PASSWORD="${NEO4J_PASSWORD:-spike-password}"
NEO4J_HEAP="${NEO4J_HEAP:-2G}"
NEO4J_PAGECACHE="${NEO4J_PAGECACHE:-2G}"

scale="${1:?usage: run-neo4j.sh <scale> <outdir> <csvdir>}"
outdir="${2:?usage: run-neo4j.sh <scale> <outdir> <csvdir>}"
csvdir="${3:?usage: run-neo4j.sh <scale> <outdir> <csvdir>}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
sibling="$here/../gm-database-schema-9c8.1"

mkdir -p "$outdir" "$csvdir"

# The endpoint ids are the ones ./run-postgres.sh hardcodes, by the rule the
# spike document states. Both engines answer for the same six pairs.
#   case | from_type,from_id | to_type,to_id
CASES=(
    "d1-artist-artist|Artist,5665|Artist,9458"
    "d1-artist-release|Artist,5665|Release,3638"
    "d2-artist-artist|Artist,5665|Artist,1"
    "d3-artist-artist|Artist,5665|Artist,2"
    "d4-artist-artist|Artist,5665|Artist,9"
    "d5-artist-artist|Artist,55563|Artist,4814"
    "d6-artist-artist|Artist,55563|Artist,32509"
    "unreachable|Artist,5665|Artist,103111"
)


# ---------------------------------------------------------------------------
# Generate and import
# ---------------------------------------------------------------------------

if [[ ! -f "$csvdir/releases.csv" ]]; then
    echo "generating neo4j CSV at scale $scale into $csvdir"
    python3 "$sibling/generate.py" --target neo4j --scale "$scale" --outdir "$csvdir" \
        2>&1 | tee "$outdir/neo4j-generate-$scale.txt"
fi

docker rm --force "$NEO4J_CONTAINER" >/dev/null 2>&1 || true
docker volume rm --force "$NEO4J_DATA_VOLUME" >/dev/null 2>&1 || true
docker volume rm --force "$NEO4J_CSV_VOLUME" >/dev/null 2>&1 || true
docker volume create "$NEO4J_DATA_VOLUME" >/dev/null
docker volume create "$NEO4J_CSV_VOLUME" >/dev/null

# The CSV goes into a named volume rather than being bind-mounted from $csvdir.
# A bind mount only works if the host path is one Docker Desktop shares, and a
# scratch directory under /private/tmp is not; the importer sees an empty /import
# and fails on the first file. `docker cp` does not care about file sharing.
# It also keeps the 200 MB inside the Docker image, where the space freed by
# removing the PostgreSQL volume can be reused, instead of on a host volume that
# is down to its last few gigabytes.
echo "staging CSV into volume $NEO4J_CSV_VOLUME"
staging="$(docker create --volume "$NEO4J_CSV_VOLUME:/import" "$NEO4J_IMAGE" true)"
docker cp "$csvdir/." "$staging:/import/"
docker rm "$staging" >/dev/null

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

echo "starting server"
docker run --detach --name "$NEO4J_CONTAINER" \
    --publish 127.0.0.1:57688:7687 \
    --volume "$NEO4J_DATA_VOLUME:/data" \
    --env "NEO4J_AUTH=neo4j/$NEO4J_PASSWORD" \
    --env "NEO4J_server_memory_heap_initial__size=$NEO4J_HEAP" \
    --env "NEO4J_server_memory_heap_max__size=$NEO4J_HEAP" \
    --env "NEO4J_server_memory_pagecache_size=$NEO4J_PAGECACHE" \
    "$NEO4J_IMAGE" >/dev/null

shell() { docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "$@"; }

for _ in $(seq 1 90); do
    if shell "RETURN 1" >/dev/null 2>&1; then break; fi
    sleep 2
done
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
    echo "scale: $scale   heap: $NEO4J_HEAP   pagecache: $NEO4J_PAGECACHE"
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
# inside the server, which is the figure comparable to psql's `\timing`: both
# start when the statement is submitted and both end when the last row is out.
# This is the same rule gm-database-schema-9c8.1 used, so the two spikes'
# PostgreSQL-to-Neo4j ratios mean the same thing.

render() { # template from_label from_prop to_label to_prop depth
    sed -e "s/@FROM_LABEL@/$2/g" -e "s/@FROM_PROP@/$3/g" \
        -e "s/@TO_LABEL@/$4/g"   -e "s/@TO_PROP@/$5/g" -e "s/@DEPTH@/$6/g" "$1"
}

printf 'query\tcase\tdepth_cap\trun\tms\n' > "$outdir/timings.tsv"

# `:param` lines and the query are kept apart, because `PROFILE` has to prefix
# the QUERY and cypher-shell would reject it in front of a parameter assignment.
measure() { # query_name case cap params body iterations
    local qname="$1" case_name="$2" cap="$3" params="$4" body="$5" iters="$6"
    local target="$outdir/neo4j-$qname-$case_name-cap$cap.txt"

    { echo "== $qname  $case_name  cap $cap"; echo "$params"; echo "$body"; } > "$target"

    echo "-- PROFILE" >> "$target"
    printf '%s\nPROFILE %s\n' "$params" "$body" | shell --format verbose >> "$target" 2>&1 || true

    echo "-- wall clock, one discarded warm-up then $iters timed runs" >> "$target"
    for _ in $(seq 1 "$((iters + 1))"); do
        printf '%s\n%s\n' "$params" "$body" | shell --format verbose 2>&1 \
            | grep -E 'ready to start consuming query after' >> "$target" || true
    done

    # cypher-shell reports "ready to start consuming query after N ms, results
    # consumed after another M ms". Their sum is the statement's wall clock
    # inside the server, which is the figure comparable to psql's `\timing`:
    # both start when the statement is submitted and both end when the last row
    # is out. This is the rule gm-database-schema-9c8.1 used, so the two spikes'
    # PostgreSQL-to-Neo4j ratios mean the same thing.
    grep 'ready to start consuming' "$target" | tail -n +2 | awk -v q="$qname" -v c="$case_name" -v cap="$cap" '
        {
            ready = ""; consumed = ""
            for (i = 1; i <= NF; i++) {
                if ($i == "after" && ready == "") { ready = $(i + 1) + 0 }
                else if ($i == "another") { consumed = $(i + 1) + 0 }
            }
            printf "%s\t%s\t%s\t%d\t%.3f\n", q, c, cap, ++n, ready + consumed
        }' | tee -a "$outdir/timings.tsv"
}

# Table A — every case at the product's own cap of 10.
for entry in "${CASES[@]}"; do
    IFS='|' read -r name from to <<<"$entry"
    fl="${from%%,*}"; fid="${from##*,}"
    tl="${to%%,*}";   tid="${to##*,}"
    measure shortest-path "$name" 10 \
        ":param from_id => '$fid';
:param to_id => '$tid';" \
        "$(render "$here/queries/shortest-path.cypher" "$fl" id "$tl" id 10)" 3
done

# Table B — the unreachable pair against the depth cap.
for cap in 1 2 3 4 6 8 10; do
    measure shortest-path unreachable-cap "$cap" \
        ":param from_id => '5665';
:param to_id => '103111';" \
        "$(render "$here/queries/shortest-path.cypher" Artist id Artist id "$cap")" 3
done

# Table C — bounded traversal.
for hops in 1 2 3; do
    measure explore-traversal explore-artist-5665 "$hops" \
        ":param entity_id => '5665';" \
        "$(render "$here/queries/explore-traversal.cypher" "" "" "" "" "$hops")" 3
done

# The CSV has done its job at this point and is 200 MB that nothing reads again.
docker volume rm --force "$NEO4J_CSV_VOLUME" >/dev/null 2>&1 || true

echo "neo4j results in $outdir"
