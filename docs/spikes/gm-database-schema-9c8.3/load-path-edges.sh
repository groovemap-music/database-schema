#!/usr/bin/env bash
# Build the path edge surface on top of a catalog that `gm-database-schema-9c8.1`
# has already loaded and materialized.
#
# Throwaway spike harness for gm-database-schema-9c8.3. Re-runnable: every object
# it creates is dropped first. ./README.md documents the whole sequence.
set -euo pipefail

PG_CONTAINER="${PG_CONTAINER:-gm9c83-pg}"
PGPASSWORD="${PGPASSWORD:-spike-password}"
scale="${1:-synthetic}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

psql_run() {
    docker exec -i -e PGPASSWORD="$PGPASSWORD" "$PG_CONTAINER" \
        psql -U groovemap -d groovemap -v ON_ERROR_STOP=1 -q "$@"
}

step() {
    local label="$1" started
    shift
    started="$(date +%s)"
    "$@"
    echo "  $label in $(( $(date +%s) - started ))s"
}

echo "staging"
psql_run -f - < "$here/stage.sql"

# Streamed into `\copy` rather than written out and read back, the way
# `gm-database-schema-9c8.1/load-postgres.sh` streams its tables.
echo "replaying the generator's Discogs artist-to-artist stream at scale $scale"
step "artist edges loaded" bash -c \
    "python3 '$here/artist_edges.py' --scale '$scale' | \
     docker exec -i -e PGPASSWORD='$PGPASSWORD' '$PG_CONTAINER' \
       psql -U groovemap -d groovemap -v ON_ERROR_STOP=1 -q \
       -c '\\copy path.artist_edge_stage (owner_id, element_id, block) FROM STDIN WITH (FORMAT csv)'"

step "augment.sql completed"   psql_run -f - < "$here/augment.sql"
step "edges.sql completed"     psql_run -f - < "$here/edges.sql"
step "bfs.sql completed"       psql_run -f - < "$here/bfs.sql"
step "adjacency.sql completed" psql_run -f - < "$here/adjacency.sql"

echo "edge counts by relationship type"
psql_run -c "SELECT rel, count(*) AS directed_rows FROM path.edge GROUP BY rel ORDER BY 2 DESC"
psql_run -c "SELECT count(*) AS nodes FROM path.node"
psql_run -c "SELECT count(*) AS adjacency_rows FROM path.adj"
psql_run -c "
    SELECT schemaname || '.' || relname AS relation,
           pg_size_pretty(pg_total_relation_size(relid)) AS total
    FROM pg_catalog.pg_statio_user_tables
    WHERE schemaname = 'path' AND pg_total_relation_size(relid) > 0
    ORDER BY pg_total_relation_size(relid) DESC"
