# Reload seen lists: measurements (gm-database-schema-uw6d)

The design and the numbers are in
[docs/architecture.md, "Reload seen lists"](../../architecture.md#reload-seen-lists).
This directory holds the scripts that produced the numbers, so they can be rerun against a
disposable PostgreSQL. Never point them at a live database: they rewrite every row and checkpoint.

| File | What it does |
| --- | --- |
| `apply.py` | Applies this checkout's full PostgreSQL schema |
| `bench.py` | Runs an unchanged reload three ways: the Discogs touch against the seen-list append, then MusicBrainz relationships and entities with today's upsert, the locking `ON CONFLICT ... WHERE` no-op, and the lock-free no-op. It also times `status`, `stale`, `shadow` and the old purge count |
| `scale.sql` | The purge's anti-join and distinct count at scale, over narrow stand-ins, with an index-free and an indexed list |

## Rerunning

```bash
docker run -d --rm --name bench-pg -e POSTGRES_USER=groovemap -e POSTGRES_PASSWORD=bench -e POSTGRES_DB=bench \
  -p 127.0.0.1:55433:5432 --shm-size 256m postgres:18-alpine -c shared_buffers=512MB -c max_wal_size=2GB -c checkpoint_timeout=30min
DSN="host=127.0.0.1 port=55433 user=groovemap password=bench dbname=bench"
uv run python apply.py "$DSN"
for k in 0 1 2 3; do uv run python ../2026-11-07-release-title-search-vector/generate.py "$DSN" $((k*50000+1)) 50000 $k & done; wait
uv run python bench.py "$DSN" 200000 300000 100000
psql "$DSN" -v n=5000000 -f scale.sql
docker rm -fv bench-pg
```

`bench.py` prints one JSON line per run. `checkpoint_timeout` is long so that only the script's
own `CHECKPOINT` before each run starts a new cycle. Every page is then logged whole the first
time a run changes it, as it is over an hour-long reload on lux. `scale.sql` runs serially with
lux's `work_mem` (64 MB), so its temp figures are the worst case. The 200k-row sample takes about
1.1 GB, and `scale.sql` at 5M rows about 1 GB more.

## Recorded results (2026-11-08, PostgreSQL 18.6 Alpine, laptop Docker)

```text
{"run": "discogs seen append", "rows": 199000, "us_per_row": 13.7, "wal_per_row": 0.4, "updates": 0, "hot": 0, "dead_tuples": 0}
{"seen_list_bytes": 8855552}
{"query": "status", "seconds": 0.06, "temp_bytes": 1613824}
{"query": "stale", "seconds": 0.07, "rows": 1000, "temp_bytes": 0}
{"run": "discogs touch", "rows": 199000, "us_per_row": 230.2, "wal_per_row": 7796.8, "updates": 199000, "hot": 0}
{"query": "shadow", "seconds": 0.53, "first": [true, 200000, 1000, 1000, 0, 0], "temp_bytes": 7691505}
{"run": "mb relationships: touch upsert (today)", "rows": 300000, "us_per_row": 73.7, "wal_per_row": 2243.7, "dead_tuples": 300000}
{"run": "mb relationships: DO UPDATE ... WHERE changed + append", "rows": 300000, "us_per_row": 64.8, "wal_per_row": 411.5, "dead_tuples": 0}
{"run": "mb relationships: UPDATE changed + INSERT DO NOTHING + append", "rows": 300000, "us_per_row": 101.4, "wal_per_row": 3.5, "dead_tuples": 0}
{"query": "mb relationships stale", "seconds": 1.75, "rows": 0, "temp_bytes": 0}
{"mb releases heap bytes per row": 2340}
{"run": "mb entities: upsert (today)", "rows": 100000, "us_per_row": 73.9, "wal_per_row": 2832.3, "dead_tuples": 100000}
{"run": "mb entities: DO UPDATE ... WHERE changed", "rows": 100000, "us_per_row": 68.7, "wal_per_row": 1198.1, "dead_tuples": 0}
{"run": "mb entities: UPDATE changed + INSERT DO NOTHING", "rows": 100000, "us_per_row": 116.5, "wal_per_row": 0.3, "dead_tuples": 0}
```

Two earlier runs of the Discogs pair gave 197 and 205 µs and 8.1 and 7.75 KB a row for the touch,
and 14.8 and 15.2 µs for the append. With 32-byte MusicBrainz keys, before the lists kept 8 bytes,
the relationships stale query spilled 40 MB at 300k rows.

`scale.sql` at 5M keys: the index-free list took 2.7 s to append 4.975M keys in 100-key
statements (40 bytes of WAL in total) and is 210 MB. The indexed list took 10.9 s and is 395 MB.
The anti-join took 4.0-5.0 s with 198 MB of temp over either list (the planner ignores the index).
It took 4.1 s with no temp at `work_mem = 256MB`. The distinct count took 3.4 s with 110 MB of
temp.
