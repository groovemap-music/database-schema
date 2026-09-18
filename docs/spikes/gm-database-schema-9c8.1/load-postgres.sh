#!/usr/bin/env bash
# Load one scale of the generated catalog into the running PostgreSQL container
# and build the materialized declaration beside the shipped one.
#
# Throwaway spike harness for gm-database-schema-9c8.1. Expects the product
# schema to have been applied already; ./README.md documents the whole sequence.
#
# Every table is piped straight from the generator into `\copy` — nothing is
# staged on disk. At the synthetic scale the release stream alone is most of a
# gigabyte of JSON, and writing it out only to read it back would double both the
# disk footprint and the load time.
set -euo pipefail

PG_CONTAINER="${PG_CONTAINER:-gm9c81-pg}"
PGPASSWORD="${PGPASSWORD:-spike-password}"

scale="${1:?usage: load-postgres.sh <scale>}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

psql_run() {
    docker exec -i -e PGPASSWORD="$PGPASSWORD" "$PG_CONTAINER" \
        psql -U groovemap -d groovemap -v ON_ERROR_STOP=1 -q "$@"
}

copy_table() {
    local table="$1" relation="$2" columns="$3"
    local started
    started="$(date +%s)"
    python3 "$here/generate.py" --target postgres --scale "$scale" --table "$table" 2>/dev/null \
        | psql_run -c "\\copy $relation ($columns) FROM STDIN WITH (FORMAT csv)" > /dev/null
    echo "  $table loaded in $(( $(date +%s) - started ))s"
}

echo "truncating"
psql_run -c "
    TRUNCATE public.artists, public.labels, public.masters, public.releases,
             musicbrainz.artists, musicbrainz.relationships CASCADE" > /dev/null

echo "loading scale $scale"
copy_table artists public.artists "data_id,hash,data"
copy_table labels public.labels "data_id,hash,data"
copy_table masters public.masters "data_id,hash,data"
copy_table releases public.releases "data_id,hash,data,media"
copy_table mb_artists musicbrainz.artists \
    "mbid,name,sort_name,type,gender,begin_date,end_date,ended,area,begin_area,end_area,disambiguation,discogs_artist_id"
copy_table mb_relationships musicbrainz.relationships \
    "source_mbid,target_mbid,source_entity_type,target_entity_type,relationship_type,begin_date,end_date,ended,attributes"

# VACUUM ANALYZE rather than ANALYZE: COPY leaves the visibility map unset, and
# without it no index-only scan is possible, which would penalise the
# materialized backend for a reason that has nothing to do with its shape.
echo "vacuum analyze"
psql_run -c "VACUUM ANALYZE" > /dev/null

echo "building materialized declaration"
started="$(date +%s)"
psql_run -f - < "$here/materialize.sql" > /dev/null
echo "  materialize.sql completed in $(( $(date +%s) - started ))s"

echo "row counts"
psql_run -c "
    SELECT 'releases' AS relation, count(*) FROM public.releases
    UNION ALL SELECT 'artists', count(*) FROM public.artists
    UNION ALL SELECT 'labels', count(*) FROM public.labels
    UNION ALL SELECT 'masters', count(*) FROM public.masters
    UNION ALL SELECT 'mb_artists', count(*) FROM musicbrainz.artists
    UNION ALL SELECT 'mb_relationships', count(*) FROM musicbrainz.relationships
    UNION ALL SELECT 'edge: by_artist', count(*) FROM graph_mat.by_artist
    UNION ALL SELECT 'edge: on_label', count(*) FROM graph_mat.on_label
    UNION ALL SELECT 'edge: in_genre', count(*) FROM graph_mat.in_genre
    UNION ALL SELECT 'edge: in_style', count(*) FROM graph_mat.in_style
    UNION ALL SELECT 'edge: derived_from', count(*) FROM graph_mat.derived_from
    UNION ALL SELECT 'edge: master_by_artist', count(*) FROM graph_mat.master_by_artist
    UNION ALL SELECT 'edge: master_in_genre', count(*) FROM graph_mat.master_in_genre
    UNION ALL SELECT 'edge: master_in_style', count(*) FROM graph_mat.master_in_style
    UNION ALL SELECT 'edge: mb_rel_artist_artist', count(*) FROM graph_mat.mb_rel_artist_artist
    UNION ALL SELECT 'EDGE TOTAL', (SELECT count(*) FROM graph_mat.by_artist)
                                 + (SELECT count(*) FROM graph_mat.on_label)
                                 + (SELECT count(*) FROM graph_mat.in_genre)
                                 + (SELECT count(*) FROM graph_mat.in_style)
                                 + (SELECT count(*) FROM graph_mat.derived_from)
                                 + (SELECT count(*) FROM graph_mat.master_by_artist)
                                 + (SELECT count(*) FROM graph_mat.master_in_genre)
                                 + (SELECT count(*) FROM graph_mat.master_in_style)
                                 + (SELECT count(*) FROM graph_mat.mb_rel_artist_artist)"

echo "relation sizes"
psql_run -c "
    SELECT schemaname || '.' || relname AS relation,
           pg_size_pretty(pg_total_relation_size(relid)) AS total
    FROM pg_catalog.pg_statio_user_tables
    WHERE schemaname IN ('public', 'musicbrainz', 'graph_mat')
      AND pg_total_relation_size(relid) > 0
    ORDER BY pg_total_relation_size(relid) DESC
    LIMIT 20"
