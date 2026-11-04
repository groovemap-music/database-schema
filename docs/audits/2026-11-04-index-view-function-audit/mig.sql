-- Upgrade test: a table already on the v0.6.1 shape moves to the proposed key, in place.
\set ON_ERROR_STOP off
SET jit = off;
DROP TABLE IF EXISTS bench.c;
CREATE TABLE bench.c (person_name text NOT NULL, release_id text NOT NULL, role text NOT NULL,
    role_category text GENERATED ALWAYS AS (bench.role_cat_pl(role)) STORED);
CREATE UNIQUE INDEX c_key ON bench.c (release_id, graph.text_key(person_name), graph.text_key(role));
CREATE INDEX c_hash ON bench.c USING hash (person_name);
CREATE INDEX c_role ON bench.c (role_category, release_id);
CALL bench.load('bench.c', 260, 1000000);
\echo before: rows, indexes MB
SELECT count(*), pg_indexes_size('bench.c') / 1048576 FROM bench.c;

\echo --- 1. a failure after the drop leaves the old key whole
BEGIN;
CREATE UNIQUE INDEX c_key_v2 ON bench.c (release_id, graph.credit_key(person_name, role));
DROP INDEX bench.c_key;
ROLLBACK;
SELECT string_agg(relname, ',' ORDER BY relname) FROM pg_class WHERE oid IN (SELECT indexrelid FROM pg_index WHERE indrelid = 'bench.c'::regclass);

\echo --- 2. the guarded migration, timed
\timing on
DO $migrate$
BEGIN
    IF pg_get_indexdef('bench.c_key'::regclass) LIKE '%text_key(role)%' THEN
        CREATE UNIQUE INDEX c_key_v2 ON bench.c (release_id, graph.credit_key(person_name, role));
        DROP INDEX bench.c_key;
        ALTER INDEX bench.c_key_v2 RENAME TO c_key;
    END IF;
    DROP INDEX IF EXISTS bench.c_hash;
END
$migrate$;
\echo --- 3. a second run is a no-op
DO $migrate$
BEGIN
    IF pg_get_indexdef('bench.c_key'::regclass) LIKE '%text_key(role)%' THEN
        RAISE EXCEPTION 'guard fired on a converged table';
    END IF;
    DROP INDEX IF EXISTS bench.c_hash;
END
$migrate$;
\timing off
\echo after: rows, indexes MB, key MB, index list
SELECT count(*), pg_indexes_size('bench.c') / 1048576, pg_relation_size('bench.c_key') / 1048576 FROM bench.c;
SELECT string_agg(relname, ',' ORDER BY relname) FROM pg_class WHERE oid IN (SELECT indexrelid FROM pg_index WHERE indrelid = 'bench.c'::regclass);

\echo --- 4. the natural key is unchanged
BEGIN;
INSERT INTO bench.c (person_name, release_id, role) VALUES ('ab', 'r1', 'c'), ('a', 'r1', 'bc');
\echo two pairs that concatenate alike stay two rows
SELECT count(*) FROM bench.c WHERE release_id = 'r1';
INSERT INTO bench.c (person_name, release_id, role) VALUES ('ab', 'r1', 'c') ON CONFLICT DO NOTHING;
\echo a target-less ON CONFLICT DO NOTHING still dedupes
SELECT count(*) FROM bench.c WHERE release_id = 'r1';
INSERT INTO bench.c (person_name, release_id, role) VALUES (repeat('x', 5000), 'r1', repeat('y', 5000));
\echo a value past the btree limit stores whole
SELECT count(*), max(length(person_name)) FROM bench.c WHERE release_id = 'r1';
SAVEPOINT s;
\echo a plain duplicate raises
INSERT INTO bench.c (person_name, release_id, role) VALUES ('ab', 'r1', 'c');
ROLLBACK TO s;
ROLLBACK;
\echo --- 5. the document delete still uses the key
EXPLAIN (COSTS OFF) DELETE FROM bench.c WHERE release_id = ANY(ARRAY['1000']);
\echo --- 6. rollback to the v0.6.1 shape, timed
\timing on
DO $rollback$
BEGIN
    CREATE UNIQUE INDEX c_key_v1 ON bench.c (release_id, graph.text_key(person_name), graph.text_key(role));
    CREATE INDEX c_hash ON bench.c USING hash (person_name);
    DROP INDEX bench.c_key;
    ALTER INDEX bench.c_key_v1 RENAME TO c_key;
END
$rollback$;
\timing off
SELECT count(*) FROM bench.c;
