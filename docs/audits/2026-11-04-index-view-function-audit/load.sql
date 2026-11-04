-- Same shape as graph.credited_on; indexes vary per variant. Row-at-a-time index maintenance,
-- one commit per 260 rows (loader batch of 100 releases at 2.6 credits each), ON CONFLICT DO NOTHING.
CREATE OR REPLACE PROCEDURE bench.load(tbl regclass, batch int DEFAULT 260) LANGUAGE plpgsql AS $$
DECLARE lo bigint := 0; hi bigint;
BEGIN
  SELECT max(g) INTO hi FROM bench.credit_src;
  WHILE lo < hi LOOP
    EXECUTE format('INSERT INTO %s (person_name, release_id, role) SELECT person_name, release_id, role FROM bench.credit_src WHERE g > $1 AND g <= $2 ON CONFLICT DO NOTHING', tbl) USING lo, lo + batch;
    COMMIT;
    lo := lo + batch;
  END LOOP;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS credit_src_g ON bench.credit_src (g);
-- The key proposed for schema v0.6.3 (`prop`, `propk` and mig.sql read it).
CREATE OR REPLACE FUNCTION graph.credit_key(person_name text, role text) RETURNS bytea
LANGUAGE sql IMMUTABLE PARALLEL SAFE RETURNS NULL ON NULL INPUT
AS $f$ SELECT graph.text_key(length(person_name)::text || ':' || person_name || role) $f$;
