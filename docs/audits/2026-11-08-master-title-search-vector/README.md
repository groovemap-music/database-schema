# Master title search vector: measurements (gm-database-schema-nu0c)

What was measured, and the numbers, are in
[docs/architecture.md, "Master title search vector"](../../architecture.md#master-title-search-vector).
This directory holds the scripts that produced them, so they can be rerun against a disposable
PostgreSQL. Never point them at a live database. The lux baselines were read with read-only
`EXPLAIN (ANALYZE, BUFFERS)` of the `before.sql` statements.

| File | What it does |
| --- | --- |
| `generate.py` | Loads a Discogs-shaped `masters` sample sized and shaped like lux's |
| `before.sql` | catalog-api's master statements today: ranked, year-filtered, and the decade facet's masters half |
| `after.sql` | The same statements reading `master_search` |
| `loader.sql` | Loader-shaped 1000-row statements, each rolled back, for timing the triggers' cost |
| `churn.sql` | One round of committed loader-shaped writes, run in a loop alongside a backfill |

## Rerunning

```bash
docker run -d --name bench-pg -e POSTGRES_PASSWORD=bench -e POSTGRES_DB=groovemap -p 127.0.0.1:55432:5432 \
  postgres:18-alpine -c shared_buffers=1GB -c max_wal_size=2GB -c track_io_timing=on
DSN="host=127.0.0.1 port=55432 user=postgres password=bench dbname=groovemap"
for k in 0 1 2 3 4 5 6 7; do uv run python generate.py "$DSN" $((k*325000+1)) 325000 & done; wait
# Apply groovemap_schema.postgres._schema_statements() without _MASTER_SEARCH_STATEMENTS, then:
psql "$DSN" -c "VACUUM ANALYZE masters"
for term in love rock dub; do psql "$DSN" -v term=$term -f before.sql; done
# Apply _MASTER_SEARCH_STATEMENTS, timing the last ("masters master_search capture triggers"), then:
psql "$DSN" -c "CALL public.master_search_backfill()" -c "VACUUM ANALYZE master_search"
for term in love rock dub; do psql "$DSN" -v term=$term -f after.sql; done
psql "$DSN" -c "SELECT * FROM public.master_search_drift()"
psql "$DSN" -v lo=1000001 -f loader.sql   # with \timing on, with and without the triggers
```

For cold numbers, restart the container and drop the Docker VM's page cache before the run:
`docker run --rm --privileged alpine sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'`.
The 2.6M-row sample takes about 4 GB on disk, plus WAL.
