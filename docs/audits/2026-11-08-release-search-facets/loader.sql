-- Loader-shaped 1000-row statements, each rolled back, for timing the triggers' cost.
-- :lo is the first data_id of a 1000-row batch. Run once as is, and once with
-- -v off='ALTER TABLE releases DISABLE TRIGGER USER;' for the baseline without triggers.
\timing on
\echo upsert-changed-document
BEGIN; :off
INSERT INTO releases (hash, data_id, data, media, gm_item_id, updated_at)
SELECT md5(hash), data_id, jsonb_set(data, '{title}', to_jsonb((data->>'title') || ' Again')), media, gm_item_id, NOW()
FROM releases WHERE data_id IN (SELECT g::text FROM generate_series(:lo, :lo + 999) g)
ON CONFLICT (data_id) DO UPDATE SET hash = EXCLUDED.hash, data = EXCLUDED.data, media = EXCLUDED.media, gm_item_id = EXCLUDED.gm_item_id, updated_at = NOW();
ROLLBACK;
\echo insert-new
BEGIN; :off
INSERT INTO releases (hash, data_id, data, media, gm_item_id, updated_at)
SELECT hash, 'n' || data_id, data, media, gm_item_id, NOW()
FROM releases WHERE data_id IN (SELECT g::text FROM generate_series(:lo, :lo + 999) g);
ROLLBACK;
\echo media-backfill-changed
BEGIN; :off
UPDATE releases SET media = jsonb_set(media, '{families}', '["changed"]'), updated_at = NOW()
WHERE data_id = ANY (ARRAY(SELECT g::text FROM generate_series(:lo, :lo + 999) g));
ROLLBACK;
\echo media-backfill-unchanged
BEGIN; :off
UPDATE releases SET media = media, updated_at = NOW()
WHERE data_id = ANY (ARRAY(SELECT g::text FROM generate_series(:lo, :lo + 999) g));
ROLLBACK;
\echo refresh-updated_at
BEGIN; :off
UPDATE releases SET updated_at = NOW() WHERE data_id = ANY (ARRAY(SELECT g::text FROM generate_series(:lo, :lo + 999) g));
ROLLBACK;
