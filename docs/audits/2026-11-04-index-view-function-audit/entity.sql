-- 300k release documents shaped like normalize_release's output: top-level arrays, xmltodict-wrapped tracklist.
DROP TABLE IF EXISTS bench.doc_src;
CREATE UNLOGGED TABLE bench.doc_src AS
WITH g AS (SELECT n, (n * 2654435761::bigint) % 4294967296 AS h FROM generate_series(1, 300000) n)
SELECT n,
       n::text AS data_id,
       encode(sha256(n::text::bytea), 'hex') AS hash,
       jsonb_build_object(
         'id', n, 'notes', (SELECT string_agg(md5((n * k)::text), ' ') FROM generate_series(1, 60) k), 'title', 'Release title ' || md5(n::text), 'year', (1950 + h % 75)::text,
         'country', (ARRAY['US','UK','Germany','France','Japan','Italy','Netherlands','Canada','Unknown'])[1 + h % 9],
         'sha256', encode(sha256(n::text::bytea), 'hex'),
         'genres', to_jsonb(ARRAY[(ARRAY['Rock','Electronic','Pop','Jazz','Funk / Soul','Hip Hop','Classical','Folk, World, & Country','Latin','Reggae','Blues','Stage & Screen'])[1 + h % 12]]),
         'styles', to_jsonb(ARRAY['Style ' || (h % 757), 'Style ' || (h % 311)]),
         'labels', jsonb_build_array(jsonb_build_object('id', (h % 2000000)::text, 'name', 'Label ' || (h % 2000000), 'catno', 'CAT-' || (h % 100000))),
         'artists', jsonb_build_array(jsonb_build_object('id', (h % 10000000)::text, 'name', 'Artist ' || (h % 10000000))),
         'formats', jsonb_build_array(jsonb_build_object('name', 'Vinyl', 'qty', '1', 'descriptions', '["LP","Album"]'::jsonb)),
         'extraartists', (SELECT jsonb_agg(jsonb_build_object('id', ((h + i) % 10000000)::text, 'name', 'Credit Person ' || ((h + i) % 661775), 'role', 'Producer')) FROM generate_series(1, 3) i),
         'tracklist', jsonb_build_object('track', (SELECT jsonb_agg(jsonb_build_object('position', 'A' || t, 'title', 'Track ' || md5((n * t)::text), 'duration', '3:41')) FROM generate_series(1, 1 + (h % 18)::int) t)),
         'identifiers', jsonb_build_object('aliases', jsonb_build_array(jsonb_build_object('provider','discogs','entity_kind','barcode','external_id', (7000000000000 + h)::text), jsonb_build_object('provider','discogs','entity_kind','matrix','external_id', 'MX-' || md5(h::text)))),
         'companies', jsonb_build_object('items', jsonb_build_array(jsonb_build_object('discogs_id', (h % 200000)::text, 'name', 'Company ' || (h % 200000), 'role', 'Pressed By', 'role_category', 'manufacturing')))
       ) AS data,
       jsonb_build_object('families', to_jsonb(ARRAY['vinyl']), 'items', jsonb_build_array(jsonb_build_object('medium','lp','family','vinyl','qty',1))) AS media
FROM g;
CREATE UNIQUE INDEX doc_src_n ON bench.doc_src (n);
ANALYZE bench.doc_src;
SELECT avg(pg_column_size(data))::int AS stored_b, avg(octet_length(data::text))::int AS text_b, pg_size_pretty(pg_total_relation_size('bench.doc_src')) FROM bench.doc_src;

CREATE OR REPLACE PROCEDURE bench.load_docs(tbl regclass, batch int DEFAULT 100) LANGUAGE plpgsql AS $$
DECLARE lo bigint := 0; hi bigint;
BEGIN
  SELECT max(n) INTO hi FROM bench.doc_src;
  WHILE lo < hi LOOP
    EXECUTE format('INSERT INTO %s (hash, data_id, data, media, gm_item_id, updated_at) SELECT hash, data_id, data, media, gen_random_uuid(), now() FROM bench.doc_src WHERE n > $1 AND n <= $2 ON CONFLICT (data_id) DO UPDATE SET hash = EXCLUDED.hash, data = EXCLUDED.data, media = EXCLUDED.media, gm_item_id = EXCLUDED.gm_item_id, updated_at = now()', tbl) USING lo, lo + batch;
    COMMIT;
    lo := lo + batch;
  END LOOP;
END $$;
-- the loader's hash-gated refresh: unchanged row, updated_at only, 100 ids per transaction
CREATE OR REPLACE PROCEDURE bench.touch_docs(tbl regclass, batch int DEFAULT 100) LANGUAGE plpgsql AS $$
DECLARE lo bigint := 0; hi bigint;
BEGIN
  SELECT max(n) INTO hi FROM bench.doc_src;
  WHILE lo < hi LOOP
    EXECUTE format('UPDATE %s SET updated_at = now() WHERE data_id = ANY($1)', tbl) USING (SELECT array_agg(g::text) FROM generate_series(lo + 1, lo + batch) g);
    COMMIT;
    lo := lo + batch;
  END LOOP;
END $$;
