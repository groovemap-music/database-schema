-- catalog-api's master statements today (api/queries/search_queries.py, 2026-11-08): each
-- matches with to_tsvector over masters.data and reads every match's heap row.
\echo ranked-branch
-- _entity_select('master', ...), limit 20 -> cap 40.
EXPLAIN (ANALYZE, BUFFERS)
WITH q AS NOT MATERIALIZED (SELECT plainto_tsquery('english', :'term') AS tsq)
SELECT 'master'::text AS type, d.data_id AS id, d.data->>'title' AS name, top.rank,
       ts_headline('english', COALESCE(d.data->>'title', ''), q.tsq) AS highlight,
       (d.data->>'year') AS year, NULL::jsonb AS genres, NULL::text AS country
FROM q, (SELECT data_id, ts_rank(to_tsvector('english', COALESCE(data->>'title', '')), q.tsq) AS rank
         FROM masters, q WHERE to_tsvector('english', COALESCE(data->>'title', '')) @@ q.tsq
         ORDER BY rank DESC, data_id LIMIT 40) top
JOIN masters d ON d.data_id = top.data_id;
\echo year-filtered-branch
-- The same branch with year_min = 1990, which reads data->>'year' of every match.
EXPLAIN (ANALYZE, BUFFERS)
WITH q AS NOT MATERIALIZED (SELECT plainto_tsquery('english', :'term') AS tsq)
SELECT 'master'::text AS type, d.data_id AS id, d.data->>'title' AS name, top.rank,
       ts_headline('english', COALESCE(d.data->>'title', ''), q.tsq) AS highlight,
       (d.data->>'year') AS year
FROM q, (SELECT data_id, ts_rank(to_tsvector('english', COALESCE(data->>'title', '')), q.tsq) AS rank
         FROM masters, q WHERE to_tsvector('english', COALESCE(data->>'title', '')) @@ q.tsq
           AND ((data->>'year') IS NULL OR (data->>'year')::int >= 1990)
         ORDER BY rank DESC, data_id LIMIT 40) top
JOIN masters d ON d.data_id = top.data_id;
\echo decade-facet-masters-half
-- _run_decade_facets, masters half only (the releases half is gm-database-schema-1sle's).
EXPLAIN (ANALYZE, BUFFERS)
WITH q AS (SELECT plainto_tsquery('english', :'term') AS tsq),
matches AS (SELECT data->>'year' AS year FROM masters, q
            WHERE to_tsvector('english', COALESCE(data->>'title', '')) @@ q.tsq AND data->>'year' IS NOT NULL
            LIMIT 10000)
SELECT (year::int / 10 * 10)::text || 's' AS decade, COUNT(*) AS cnt FROM matches
WHERE year ~ '^[0-9]{4}$' GROUP BY decade ORDER BY decade;
