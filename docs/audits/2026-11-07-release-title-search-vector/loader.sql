-- :lo is the first data_id of a 1000-row batch
\echo insert-new
BEGIN;
INSERT INTO releases (hash, data_id, data, media, gm_item_id, updated_at)
SELECT hash, 'n' || data_id, jsonb_set(data, '{title}', to_jsonb((data->>'title') || ' Again')), media, gm_item_id, NOW()
FROM releases WHERE data_id IN (SELECT g::text FROM generate_series(:lo, :lo + 999) g)
ON CONFLICT (data_id) DO UPDATE SET hash = EXCLUDED.hash, data = EXCLUDED.data, media = EXCLUDED.media, gm_item_id = EXCLUDED.gm_item_id, updated_at = NOW();
ROLLBACK;
\echo upsert-changed
BEGIN;
INSERT INTO releases (hash, data_id, data, media, gm_item_id, updated_at)
SELECT md5(hash), data_id, jsonb_set(data, '{title}', to_jsonb((data->>'title') || ' Again')), media, gm_item_id, NOW()
FROM releases WHERE data_id IN (SELECT g::text FROM generate_series(:lo, :lo + 999) g)
ON CONFLICT (data_id) DO UPDATE SET hash = EXCLUDED.hash, data = EXCLUDED.data, media = EXCLUDED.media, gm_item_id = EXCLUDED.gm_item_id, updated_at = NOW();
ROLLBACK;
\echo refresh-updated_at
BEGIN;
UPDATE releases SET updated_at = NOW() WHERE data_id = ANY (ARRAY(SELECT g::text FROM generate_series(:lo, :lo + 999) g));
ROLLBACK;
\echo purge-delete
BEGIN;
DELETE FROM releases WHERE data_id = ANY (ARRAY(SELECT g::text FROM generate_series(:lo, :lo + 999) g));
ROLLBACK;
