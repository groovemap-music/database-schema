-- :lo is the first data_id of a 1000-row batch; each statement is rolled back.
\echo insert-new
BEGIN;
INSERT INTO masters (hash, data_id, data, gm_item_id, updated_at)
SELECT hash, 'n' || data_id, jsonb_set(data, '{title}', to_jsonb((data->>'title') || ' Again')), gm_item_id, NOW()
FROM masters WHERE data_id IN (SELECT g::text FROM generate_series(:lo, :lo + 999) g)
ON CONFLICT (data_id) DO UPDATE SET hash = EXCLUDED.hash, data = EXCLUDED.data, gm_item_id = EXCLUDED.gm_item_id, updated_at = NOW();
ROLLBACK;
\echo upsert-changed
BEGIN;
INSERT INTO masters (hash, data_id, data, gm_item_id, updated_at)
SELECT md5(hash), data_id, jsonb_set(data, '{title}', to_jsonb((data->>'title') || ' Again')), gm_item_id, NOW()
FROM masters WHERE data_id IN (SELECT g::text FROM generate_series(:lo, :lo + 999) g)
ON CONFLICT (data_id) DO UPDATE SET hash = EXCLUDED.hash, data = EXCLUDED.data, gm_item_id = EXCLUDED.gm_item_id, updated_at = NOW();
ROLLBACK;
\echo upsert-unchanged
BEGIN;
INSERT INTO masters (hash, data_id, data, gm_item_id, updated_at)
SELECT hash, data_id, data, gm_item_id, NOW()
FROM masters WHERE data_id IN (SELECT g::text FROM generate_series(:lo, :lo + 999) g)
ON CONFLICT (data_id) DO UPDATE SET hash = EXCLUDED.hash, data = EXCLUDED.data, gm_item_id = EXCLUDED.gm_item_id, updated_at = NOW();
ROLLBACK;
\echo refresh-updated_at
BEGIN;
UPDATE masters SET updated_at = NOW() WHERE data_id = ANY (ARRAY(SELECT g::text FROM generate_series(:lo, :lo + 999) g));
ROLLBACK;
\echo purge-delete
BEGIN;
DELETE FROM masters WHERE data_id = ANY (ARRAY(SELECT g::text FROM generate_series(:lo, :lo + 999) g));
ROLLBACK;
