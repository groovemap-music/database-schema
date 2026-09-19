#!/usr/bin/env bash
# Build this spike's `pf` schema on top of a catalog that
# gm-database-schema-9c8.1 has loaded and gm-database-schema-9c8.3's `augment.sql`
# has extended with the artist-to-artist edges.
#
# Throwaway spike harness for gm-database-schema-gkt.1. Re-runnable: `state.sql`
# drops the whole schema first. ./README.md documents the whole sequence.
set -euo pipefail

PG_CONTAINER="${PG_CONTAINER:-gmgkt1-pg}"
PGPASSWORD="${PGPASSWORD:-spike-password}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

psql_run() {
    docker exec -i -e PGPASSWORD="$PGPASSWORD" "$PG_CONTAINER" \
        psql -U groovemap -d groovemap -v ON_ERROR_STOP=1 -q "$@"
}

# The frontier query and the side-choice query are substituted whole rather than
# in pieces, because the degree-ordered variant needs a different FROM clause and
# not just a different ORDER BY.
INDEX_ORDER_SQL='SELECT s.kind, s.key FROM pf.seen AS s WHERE s.side = _side AND s.depth = _gen ORDER BY s.kind, s.key'
DEGREE_ORDER_SQL='SELECT s.kind, s.key FROM pf.seen AS s LEFT JOIN pf.degree AS g ON g.kind = s.kind AND g.key = s.key WHERE s.side = _side AND s.depth = _gen ORDER BY coalesce(g.deg, 0), s.kind, s.key'

COUNT_SIZES_SQL='SELECT count(*) FILTER (WHERE side = 0 AND depth = df), count(*) FILTER (WHERE side = 1 AND depth = db), count(*) FILTER (WHERE side = 0 AND depth = df), count(*) FILTER (WHERE side = 1 AND depth = db) INTO fsize, bsize, fcount, bcount FROM pf.seen;'
DEGREE_SIZES_SQL='SELECT coalesce(sum(coalesce(g.deg, 0)) FILTER (WHERE s.side = 0 AND s.depth = df), 0), coalesce(sum(coalesce(g.deg, 0)) FILTER (WHERE s.side = 1 AND s.depth = db), 0), count(*) FILTER (WHERE s.side = 0 AND s.depth = df), count(*) FILTER (WHERE s.side = 1 AND s.depth = db) INTO fsize, bsize, fcount, bcount FROM pf.seen AS s LEFT JOIN pf.degree AS g ON g.kind = s.kind AND g.key = s.key;'

instantiate() { # suffix edge-view frontier-sql sizes-sql
    awk -v suf="$1" -v edge="$2" -v frontier="$3" -v sizes="$4" '
        { gsub(/@SUF@/, suf); gsub(/@EDGE@/, edge)
          gsub(/@FRONTIER_SQL@/, frontier); gsub(/@SIZES_SQL@/, sizes); print }
    ' "$here/sql/pathfinder.sql.in"
}

echo "state"  && psql_run -f - < "$here/sql/state.sql"
echo "edges"  && psql_run -f - < "$here/sql/edges.sql"
echo "degree" && psql_run -f - < "$here/sql/degree.sql"

echo "pathfinder"          && instantiate ''       'pf.edge'       "$INDEX_ORDER_SQL"   "$COUNT_SIZES_SQL"  | psql_run -f -
echo "pathfinder (no IS)"  && instantiate '_no_is' 'pf.edge_no_is' "$INDEX_ORDER_SQL"   "$COUNT_SIZES_SQL"  | psql_run -f -
echo "pathfinder (degree)" && instantiate '_deg'   'pf.edge'       "$DEGREE_ORDER_SQL" "$DEGREE_SIZES_SQL" | psql_run -f -
echo "hstore" && psql_run -f - < "$here/sql/hstore.sql"

echo "edge classes"
psql_run -c "SELECT rel, directed_rows FROM pf.edge_class ORDER BY directed_rows DESC"
psql_run -c "SELECT count(*) AS directed_rows_total FROM pf.edge"
psql_run -c "
    SELECT 'discogs member_of' AS relation, count(*) FROM path.member_of
    UNION ALL SELECT 'musicbrainz member_of', count(*) FROM path.mb_member_of
    UNION ALL SELECT 'alias_of', count(*) FROM path.alias_of"
echo "degree distribution"
psql_run -c "
    SELECT kind, count(*) AS vertices, max(deg) AS max_degree, round(avg(deg), 1) AS avg_degree
    FROM pf.degree GROUP BY kind ORDER BY max(deg) DESC"
psql_run -c "
    SELECT pg_size_pretty(pg_total_relation_size('pf.degree')) AS degree_relation_size"
