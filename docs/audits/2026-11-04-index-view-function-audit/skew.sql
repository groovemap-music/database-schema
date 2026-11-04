-- Does a hash index slow inserts of a heavily repeated value? Real insert cost of one name
-- as its duplicate count grows, against a btree on the same column.
\set ON_ERROR_STOP on
SET jit = off;
DROP TABLE IF EXISTS bench.s_h, bench.s_b;
CREATE TABLE bench.s_h (person_name text NOT NULL);
CREATE TABLE bench.s_b (person_name text NOT NULL);
CREATE INDEX s_h_i ON bench.s_h USING hash (person_name);
CREATE INDEX s_b_i ON bench.s_b (person_name);
CREATE OR REPLACE PROCEDURE bench.skew(tbl text) LANGUAGE plpgsql AS $$
DECLARE total bigint := 0; step bigint; t0 timestamptz; us numeric; idx text := tbl || '_i';
BEGIN
  FOREACH step IN ARRAY ARRAY[10000, 90000, 300000, 600000]::bigint[] LOOP
    EXECUTE format('INSERT INTO bench.%I SELECT %L FROM generate_series(1,$1)', tbl, 'Johann Sebastian Bach') USING step;
    COMMIT;
    total := total + step;
    t0 := clock_timestamp();
    FOR i IN 1..2000 LOOP
      EXECUTE format('INSERT INTO bench.%I VALUES (%L)', tbl, 'Johann Sebastian Bach');
    END LOOP;
    us := extract(epoch FROM clock_timestamp() - t0) * 1e6 / 2000;
    RAISE NOTICE '% dup=% us_per_insert=% index_mb=%', tbl, total, round(us, 1), pg_relation_size(format('bench.%I', idx)::regclass) / 1048576;
    COMMIT;
  END LOOP;
END $$;

CALL bench.skew('s_b');
