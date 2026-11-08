-- The same statements reading public.master_search; masters is read only for the 40 survivors.
\echo ranked-branch
EXPLAIN (ANALYZE, BUFFERS)
WITH q AS NOT MATERIALIZED (SELECT plainto_tsquery('english', :'term') AS tsq)
SELECT 'master'::text AS type, d.data_id AS id, d.data->>'title' AS name, top.rank,
       ts_headline('english', COALESCE(d.data->>'title', ''), q.tsq) AS highlight,
       (d.data->>'year') AS year, NULL::jsonb AS genres, NULL::text AS country
FROM q, (SELECT master_id AS data_id, ts_rank(title_tsv, q.tsq) AS rank
         FROM master_search, q WHERE title_tsv @@ q.tsq
         ORDER BY rank DESC, master_id LIMIT 40) top
JOIN masters d ON d.data_id = top.data_id;
\echo year-filtered-branch
EXPLAIN (ANALYZE, BUFFERS)
WITH q AS NOT MATERIALIZED (SELECT plainto_tsquery('english', :'term') AS tsq)
SELECT 'master'::text AS type, d.data_id AS id, d.data->>'title' AS name, top.rank,
       ts_headline('english', COALESCE(d.data->>'title', ''), q.tsq) AS highlight,
       top.year
FROM q, (SELECT master_id AS data_id, year, ts_rank(title_tsv, q.tsq) AS rank
         FROM master_search, q WHERE title_tsv @@ q.tsq
           AND (year IS NULL OR year::int >= 1990)
         ORDER BY rank DESC, master_id LIMIT 40) top
JOIN masters d ON d.data_id = top.data_id;
\echo decade-facet-masters-half
EXPLAIN (ANALYZE, BUFFERS)
WITH q AS (SELECT plainto_tsquery('english', :'term') AS tsq),
matches AS (SELECT year FROM master_search, q
            WHERE title_tsv @@ q.tsq AND year IS NOT NULL
            LIMIT 10000)
SELECT (year::int / 10 * 10)::text || 's' AS decade, COUNT(*) AS cnt FROM matches
WHERE year ~ '^[0-9]{4}$' GROUP BY decade ORDER BY decade;
