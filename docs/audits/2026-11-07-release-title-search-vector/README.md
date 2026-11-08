# Release title search vector: measurements (gm-database-schema-fi4a)

What was measured, and the numbers, are in
[docs/architecture.md, "Release title search vector"](../../architecture.md#release-title-search-vector).
This directory holds the scripts that produced them, so they can be rerun against a disposable
PostgreSQL. Never point them at a live database.

| File | What it does |
| --- | --- |
| `generate.py` | Loads a Discogs-shaped `releases` sample whose documents are toasted like lux's |
| `before.sql` | catalog-api's release search branch today: it ranks by `idx_releases_fts` and recomputes each vector |
| `after.sql` | The same branch ranked by `release_search` |
| `loader.sql` | Loader-shaped 1000-row statements, each rolled back, for timing the triggers' cost |
| `churn.sql` | One round of committed loader-shaped writes, run in a loop alongside a backfill |

## Rerunning

```bash
docker run -d --name bench-pg -e POSTGRES_PASSWORD=bench -e POSTGRES_DB=groovemap -p 127.0.0.1:55432:5432 \
  postgres:18-alpine -c shared_buffers=1GB -c max_wal_size=2GB -c track_io_timing=on
DSN="host=127.0.0.1 port=55432 user=postgres password=bench dbname=groovemap"
for k in 0 1 2 3 4 5 6 7; do uv run python generate.py "$DSN" $((k*250000+1)) 250000 & done; wait
psql "$DSN" -c "VACUUM ANALYZE releases" \
  -c "CREATE INDEX idx_releases_fts ON releases USING GIN (to_tsvector('english', COALESCE(data->>'title', '')))"
# Apply groovemap_schema.postgres._RELEASE_SEARCH_STATEMENTS, then:
psql "$DSN" -c "CALL public.release_search_backfill()" -c "VACUUM ANALYZE release_search"
psql "$DSN" -v term=love -f before.sql
psql "$DSN" -v term=love -f after.sql
psql "$DSN" -c "SELECT * FROM public.release_search_drift()"
```

For cold numbers, restart the container and drop the Docker VM's page cache before the run:
`docker run --rm --privileged alpine sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'`.
The 2M-row sample takes about 6.7 GB on disk.
