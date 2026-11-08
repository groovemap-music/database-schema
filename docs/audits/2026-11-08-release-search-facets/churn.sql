-- One round of committed loader-shaped writes around :lo, run in a loop alongside a backfill.
-- fi4a's round, plus the two writes that change only facet attributes: discogs-sql-loader's
-- media backfill (an UPDATE setting media alone) and an upsert that changes genres and country.
INSERT INTO releases (hash, data_id, data, media, gm_item_id, updated_at)
SELECT md5(hash), data_id, jsonb_set(data, '{title}', to_jsonb('Churn ' || :lo || ' ' || (data->>'title'))), media, gm_item_id, NOW()
FROM releases WHERE data_id IN (SELECT (:lo + g * 7)::text FROM generate_series(0, 199) g)
ON CONFLICT (data_id) DO UPDATE SET hash = EXCLUDED.hash, data = EXCLUDED.data, media = EXCLUDED.media, gm_item_id = EXCLUDED.gm_item_id, updated_at = NOW();
INSERT INTO releases (hash, data_id, data, media, gm_item_id, updated_at)
SELECT md5(hash), data_id, data || jsonb_build_object('genres', jsonb_build_array('Churn ' || :lo), 'country', 'Churnland'), media, gm_item_id, NOW()
FROM releases WHERE data_id IN (SELECT (:lo + 4 + g * 9)::text FROM generate_series(0, 199) g)
ON CONFLICT (data_id) DO UPDATE SET hash = EXCLUDED.hash, data = EXCLUDED.data, media = EXCLUDED.media, gm_item_id = EXCLUDED.gm_item_id, updated_at = NOW();
UPDATE releases SET media = jsonb_set(COALESCE(media, '{"items": []}'), '{families}', to_jsonb(ARRAY['churn' || :lo])), updated_at = NOW()
WHERE data_id = ANY (ARRAY(SELECT (:lo + 5 + g * 3)::text FROM generate_series(0, 499) g));
UPDATE releases SET updated_at = NOW() WHERE data_id = ANY (ARRAY(SELECT (:lo + 3 + g * 5)::text FROM generate_series(0, 999) g));
DELETE FROM releases WHERE data_id = ANY (ARRAY(SELECT (:lo + 1 + g * 11)::text FROM generate_series(0, 49) g));
INSERT INTO releases (hash, data_id, data, media, updated_at)
SELECT hash, 'c' || data_id, data, media, NOW() FROM releases WHERE data_id IN (SELECT (:lo + 2 + g * 13)::text FROM generate_series(0, 49) g)
ON CONFLICT (data_id) DO NOTHING;
