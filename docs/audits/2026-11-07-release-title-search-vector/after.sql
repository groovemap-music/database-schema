-- The same branch ranked on the side table; documents read only for the 40 survivors.
EXPLAIN (ANALYZE, BUFFERS)
WITH q AS NOT MATERIALIZED (SELECT plainto_tsquery('english', :'term') AS tsq)
SELECT 'release'::text AS type, d.data_id AS id, d.data->>'title' AS name, top.rank,
       ts_headline('english', COALESCE(d.data->>'title', ''), q.tsq) AS highlight,
       d.data->>'year' AS year, d.data->'genres' AS genres, d.data->>'country' AS country
FROM q, (SELECT release_id AS data_id, ts_rank(title_tsv, q.tsq) AS rank
         FROM release_search, q WHERE title_tsv @@ q.tsq
         ORDER BY rank DESC, release_id LIMIT 40) top
JOIN releases d ON d.data_id = top.data_id;
