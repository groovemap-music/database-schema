# Release search facet and filter attributes: measurements (gm-database-schema-1sle)

What was measured, and the numbers, are in
[docs/architecture.md, "Release search facet and filter attributes"](../../architecture.md#release-search-facet-and-filter-attributes).
This directory holds the scripts that produced them, so they can be rerun against a disposable
PostgreSQL. Never point them at a live database.

| File | What it does |
| --- | --- |
| `generate.py` | Loads fi4a's toasted Discogs-shaped `releases` sample, with lux-shaped genres, years, countries and a `media` block per row |
| `before.sql` | catalog-api's genre, decade and media facets and its filtered release branches today, over `releases` |
| `after.sql` | The same statements over `release_search`, plus the genre facet over every match |
| `loader.sql` | Loader-shaped 1000-row statements, each rolled back, with and without the triggers |
| `churn.sql` | One round of committed loader-shaped writes, run in a loop alongside a backfill |

## Rerunning

The measurement upgrades a v0.11.0 `release_search`, as lux will be upgraded. `v011.sql` and
`new.sql` are `_RELEASE_SEARCH_STATEMENTS` of v0.11.0 (`git show v0.11.0:src/groovemap_schema/postgres.py`)
and of this change, each statement followed by `;`.

```bash
docker run -d --name bench-pg -e POSTGRES_PASSWORD=bench -e POSTGRES_DB=groovemap -p 127.0.0.1:55432:5432 \
  postgres:18-alpine -c shared_buffers=512MB -c max_wal_size=1GB -c track_io_timing=on
DSN="host=127.0.0.1 port=55432 user=postgres password=bench dbname=groovemap"
for k in 0 1 2 3; do uv run python generate.py "$DSN" $((k*125000+1)) 125000 & done; wait
psql "$DSN" -c "VACUUM ANALYZE releases" \
  -c "CREATE INDEX idx_releases_fts ON releases USING GIN (to_tsvector('english', COALESCE(data->>'title', '')))" \
  -c "CREATE INDEX idx_releases_genres ON releases USING GIN ((data->'genres'))" \
  -c "CREATE INDEX idx_releases_country ON releases ((data->>'country'))" \
  -c "CREATE INDEX idx_releases_year ON releases ((data->>'year'))" \
  -c "CREATE INDEX idx_releases_media_families ON releases USING GIN ((media->'families'))"
psql "$DSN" -f v011.sql -c "CALL public.release_search_backfill()" -c "VACUUM ANALYZE release_search"
psql "$DSN" -v term=love -f before.sql
psql "$DSN" -f new.sql                              # with \timing on: the upgrade's lock times
psql "$DSN" -c "CALL public.release_search_backfill()" -c "VACUUM ANALYZE release_search"
PGOPTIONS="-c max_parallel_workers_per_gather=0" psql "$DSN" -c "SELECT * FROM public.release_search_drift()"
psql "$DSN" -v term=love -f after.sql
psql "$DSN" -v lo=100001 -f loader.sql
psql "$DSN" -v lo=100001 -v off='ALTER TABLE releases DISABLE TRIGGER USER;' -f loader.sql
```

Run each `before.sql` and `after.sql` twice and read the second, warm run. The drift check runs
serially because a parallel hash join needs more than Docker's 64 MB of `/dev/shm`. For the
concurrency check, set the new columns back to NULL, then run `churn.sql` in a loop
(`-v lo=<n>`, a different `n` each round) while the backfill runs, and check the drift afterwards.
The 500k-row sample takes about 2.5 GB on disk.
