-- The purge's anti-join at scale, over narrow stand-ins: :n Discogs-shaped text keys in an
-- entity table (data_id PRIMARY KEY, updated_at) and the same keys, less 0.5%, in a seen list.
-- Run in a disposable database with lux's work_mem (64MB): psql -v n=5000000 -f scale.sql
-- Serial, so the temp figures are the worst case and Docker's small /dev/shm is not in the way
-- (lux's server has 2 GB of it, and a parallel hash keeps more of the join in memory).
\timing on
SET work_mem = '64MB';
SET max_parallel_workers_per_gather = 0;
SELECT set_config('bench.n', :'n', false);
DROP TABLE IF EXISTS scale_entity, scale_seen, scale_seen_indexed;
CREATE TABLE scale_entity (data_id VARCHAR PRIMARY KEY, updated_at TIMESTAMPTZ NOT NULL DEFAULT now() - interval '30 days');
INSERT INTO scale_entity (data_id) SELECT g::text FROM generate_series(1, :n) AS g;
VACUUM ANALYZE scale_entity;
CREATE UNLOGGED TABLE scale_seen (seen_at TIMESTAMPTZ NOT NULL DEFAULT now(), seen_key TEXT NOT NULL);
CREATE UNLOGGED TABLE scale_seen_indexed (seen_at TIMESTAMPTZ NOT NULL DEFAULT now(), seen_key TEXT NOT NULL);
CREATE INDEX ON scale_seen_indexed (seen_key);
-- The appends, in dump order, 100 ids a statement as the loader's batches are.
SELECT pg_current_wal_insert_lsn() AS lsn \gset
DO $$ BEGIN FOR b IN 0 .. (current_setting('bench.n')::int * 0.995 / 100)::int - 1 LOOP
    INSERT INTO scale_seen (seen_key) SELECT g::text FROM generate_series(b * 100 + 1, b * 100 + 100) AS g;
END LOOP; END $$;
SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(), :'lsn') AS append_wal_bytes;
DO $$ BEGIN FOR b IN 0 .. (current_setting('bench.n')::int * 0.995 / 100)::int - 1 LOOP
    INSERT INTO scale_seen_indexed (seen_key) SELECT g::text FROM generate_series(b * 100 + 1, b * 100 + 100) AS g;
END LOOP; END $$;
ANALYZE scale_seen, scale_seen_indexed;
SELECT pg_size_pretty(pg_total_relation_size('scale_seen')) AS seen, pg_size_pretty(pg_total_relation_size('scale_seen_indexed')) AS seen_indexed;
SELECT temp_bytes AS t0 FROM pg_stat_database WHERE datname = current_database() \gset
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT e.data_id FROM scale_entity e WHERE e.updated_at < now() - interval '1 minute'
  AND NOT EXISTS (SELECT 1 FROM scale_seen s WHERE s.seen_key = e.data_id AND s.seen_at >= now() - interval '1 minute');
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT e.data_id FROM scale_entity e WHERE e.updated_at < now() - interval '1 minute'
  AND NOT EXISTS (SELECT 1 FROM scale_seen_indexed s WHERE s.seen_key = e.data_id AND s.seen_at >= now() - interval '1 minute');
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT count(*) FROM (SELECT DISTINCT seen_key FROM scale_seen WHERE seen_at >= now() - interval '1 minute') d;
SET work_mem = '256MB';
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT e.data_id FROM scale_entity e WHERE e.updated_at < now() - interval '1 minute'
  AND NOT EXISTS (SELECT 1 FROM scale_seen s WHERE s.seen_key = e.data_id AND s.seen_at >= now() - interval '1 minute');
