-- The search route's release branch today (catalog-api _entity_select, limit 20 -> cap 40).
EXPLAIN (ANALYZE, BUFFERS)
WITH q AS NOT MATERIALIZED (SELECT plainto_tsquery('english', :'term') AS tsq)
SELECT 'release'::text AS type, d.data_id AS id, d.data->>'title' AS name, top.rank,
       ts_headline('english', COALESCE(d.data->>'title', ''), q.tsq) AS highlight,
       d.data->>'year' AS year, d.data->'genres' AS genres, d.data->>'country' AS country
FROM q, (SELECT data_id, ts_rank(to_tsvector('english', COALESCE(data->>'title', '')), q.tsq) AS rank
         FROM releases, q WHERE to_tsvector('english', COALESCE(data->>'title', '')) @@ q.tsq
         ORDER BY rank DESC, data_id LIMIT 40) top
JOIN releases d ON d.data_id = top.data_id;
