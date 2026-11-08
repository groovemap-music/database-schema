-- one round of loader-shaped committed writes around :lo
INSERT INTO masters (hash, data_id, data, gm_item_id, updated_at)
SELECT md5(hash), data_id, jsonb_set(jsonb_set(data, '{title}', to_jsonb('Churn ' || :lo || ' ' || (data->>'title'))), '{year}', to_jsonb(1900 + :lo % 120)), gm_item_id, NOW()
FROM masters WHERE data_id IN (SELECT (:lo + g * 7)::text FROM generate_series(0, 199) g)
ON CONFLICT (data_id) DO UPDATE SET hash = EXCLUDED.hash, data = EXCLUDED.data, gm_item_id = EXCLUDED.gm_item_id, updated_at = NOW();
UPDATE masters SET updated_at = NOW() WHERE data_id = ANY (ARRAY(SELECT (:lo + 3 + g * 5)::text FROM generate_series(0, 999) g));
DELETE FROM masters WHERE data_id = ANY (ARRAY(SELECT (:lo + 1 + g * 11)::text FROM generate_series(0, 49) g));
INSERT INTO masters (hash, data_id, data, updated_at)
SELECT hash, 'c' || data_id, data, NOW() FROM masters WHERE data_id IN (SELECT (:lo + 2 + g * 13)::text FROM generate_series(0, 49) g)
ON CONFLICT (data_id) DO NOTHING;
