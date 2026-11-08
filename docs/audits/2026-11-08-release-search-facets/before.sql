-- catalog-api's release facets and filtered release branch today (api/queries/search_queries.py,
-- v0.11.0), verbatim: each match is found by idx_releases_fts and its document read.
-- psql -v term=love -f before.sql
\echo genre facet
EXPLAIN (ANALYZE, BUFFERS)
WITH q AS (SELECT plainto_tsquery('english', :'term') AS tsq),
matched AS (SELECT data->'genres' AS genres FROM releases, q
            WHERE to_tsvector('english', COALESCE(data->>'title', '')) @@ q.tsq LIMIT 10000)
SELECT genre, COUNT(*) AS cnt FROM matched, jsonb_array_elements_text(genres) AS genre
GROUP BY genre ORDER BY cnt DESC LIMIT 20;
\echo decade facet, releases half
EXPLAIN (ANALYZE, BUFFERS)
WITH q AS (SELECT plainto_tsquery('english', :'term') AS tsq),
matches AS (SELECT data->>'year' AS year FROM releases, q
            WHERE to_tsvector('english', COALESCE(data->>'title', '')) @@ q.tsq AND data->>'year' IS NOT NULL LIMIT 10000)
SELECT (year::int / 10 * 10)::text || 's' AS decade, COUNT(*) AS cnt FROM matches
WHERE year ~ '^[0-9]{4}$' GROUP BY decade ORDER BY decade;
\echo media facet
EXPLAIN (ANALYZE, BUFFERS)
WITH q AS (SELECT plainto_tsquery('english', :'term') AS tsq),
matched AS (SELECT media FROM releases, q
            WHERE to_tsvector('english', COALESCE(data->>'title', '')) @@ q.tsq LIMIT 10000)
SELECT family, COUNT(*) AS cnt FROM matched, jsonb_array_elements_text(media->'families') AS family
GROUP BY family ORDER BY cnt DESC;
\echo filtered results branch: year 1970-1999, genre Rock or Jazz, media vinyl or cd, country US/UK/Japan
EXPLAIN (ANALYZE, BUFFERS)
WITH q AS NOT MATERIALIZED (SELECT plainto_tsquery('english', :'term') AS tsq)
SELECT 'release'::text AS type, d.data_id AS id, d.data->>'title' AS name, top.rank,
       ts_headline('english', COALESCE(d.data->>'title', ''), q.tsq) AS highlight,
       (d.data->>'year') AS year, (d.data->'genres') AS genres, (d.data->>'country') AS country
FROM q, (SELECT data_id, ts_rank(to_tsvector('english', COALESCE(data->>'title', '')), q.tsq) AS rank
         FROM releases, q
         WHERE to_tsvector('english', COALESCE(data->>'title', '')) @@ q.tsq
           AND ((data->>'year') IS NULL OR (data->>'year')::int >= 1970) AND ((data->>'year') IS NULL OR (data->>'year')::int <= 1999)
           AND ((data->'genres') IS NULL OR (data->'genres') ?| ARRAY['Rock', 'Jazz'])
           AND (media IS NULL OR ((media->'families') ?| ARRAY['vinyl'] OR jsonb_path_exists(media, '$.items[*] ? (@.medium == $m)', jsonb_build_object('m', 'cd'::text))))
           AND ((data->>'country') IS NULL OR (data->>'country') = ANY(ARRAY['US', 'UK', 'Japan']))
         ORDER BY rank DESC, data_id LIMIT 40) top
JOIN releases d ON d.data_id = top.data_id;
\echo filtered count branch
EXPLAIN (ANALYZE, BUFFERS)
WITH q AS NOT MATERIALIZED (SELECT plainto_tsquery('english', :'term') AS tsq)
SELECT COUNT(*) FROM (SELECT 1 FROM releases, q
  WHERE to_tsvector('english', COALESCE(data->>'title', '')) @@ q.tsq
    AND ((data->>'year') IS NULL OR (data->>'year')::int >= 1970) AND ((data->>'year') IS NULL OR (data->>'year')::int <= 1999)
    AND ((data->'genres') IS NULL OR (data->'genres') ?| ARRAY['Rock', 'Jazz'])
    AND (media IS NULL OR ((media->'families') ?| ARRAY['vinyl'] OR jsonb_path_exists(media, '$.items[*] ? (@.medium == $m)', jsonb_build_object('m', 'cd'::text))))
    AND ((data->>'country') IS NULL OR (data->>'country') = ANY(ARRAY['US', 'UK', 'Japan']))
  LIMIT 10000) matched;
