-- The same statements over public.release_search (gm-database-schema-1sle): each match reads
-- the narrow side row, and the results branch reads releases only for its 40 survivors.
-- psql -v term=love -f after.sql
\echo genre facet
EXPLAIN (ANALYZE, BUFFERS)
WITH q AS (SELECT plainto_tsquery('english', :'term') AS tsq),
matched AS (SELECT genres FROM release_search, q WHERE title_tsv @@ q.tsq LIMIT 10000)
SELECT genre, COUNT(*) AS cnt FROM matched, jsonb_array_elements_text(genres) AS genre
GROUP BY genre ORDER BY cnt DESC LIMIT 20;
\echo decade facet, releases half
EXPLAIN (ANALYZE, BUFFERS)
WITH q AS (SELECT plainto_tsquery('english', :'term') AS tsq),
matches AS (SELECT year FROM release_search, q WHERE title_tsv @@ q.tsq AND year IS NOT NULL LIMIT 10000)
SELECT (year::int / 10 * 10)::text || 's' AS decade, COUNT(*) AS cnt FROM matches
WHERE year ~ '^[0-9]{4}$' GROUP BY decade ORDER BY decade;
\echo media facet
EXPLAIN (ANALYZE, BUFFERS)
WITH q AS (SELECT plainto_tsquery('english', :'term') AS tsq),
matched AS (SELECT media_families FROM release_search, q WHERE title_tsv @@ q.tsq LIMIT 10000)
SELECT family, COUNT(*) AS cnt FROM matched, jsonb_array_elements_text(media_families) AS family
GROUP BY family ORDER BY cnt DESC;
\echo filtered results branch: year 1970-1999, genre Rock or Jazz, media vinyl or cd, country US/UK/Japan
EXPLAIN (ANALYZE, BUFFERS)
WITH q AS NOT MATERIALIZED (SELECT plainto_tsquery('english', :'term') AS tsq)
SELECT 'release'::text AS type, d.data_id AS id, d.data->>'title' AS name, top.rank,
       ts_headline('english', COALESCE(d.data->>'title', ''), q.tsq) AS highlight,
       (d.data->>'year') AS year, (d.data->'genres') AS genres, (d.data->>'country') AS country
FROM q, (SELECT release_id AS data_id, ts_rank(title_tsv, q.tsq) AS rank
         FROM release_search, q
         WHERE title_tsv @@ q.tsq
           AND (year IS NULL OR year::int >= 1970) AND (year IS NULL OR year::int <= 1999)
           AND (genres IS NULL OR genres ?| ARRAY['Rock', 'Jazz'])
           AND (media_mediums IS NULL OR (media_families ?| ARRAY['vinyl'] OR media_mediums ? 'cd'))
           AND (country IS NULL OR country = ANY(ARRAY['US', 'UK', 'Japan']))
         ORDER BY rank DESC, release_id LIMIT 40) top
JOIN releases d ON d.data_id = top.data_id;
\echo filtered count branch
EXPLAIN (ANALYZE, BUFFERS)
WITH q AS NOT MATERIALIZED (SELECT plainto_tsquery('english', :'term') AS tsq)
SELECT COUNT(*) FROM (SELECT 1 FROM release_search, q
  WHERE title_tsv @@ q.tsq
    AND (year IS NULL OR year::int >= 1970) AND (year IS NULL OR year::int <= 1999)
    AND (genres IS NULL OR genres ?| ARRAY['Rock', 'Jazz'])
    AND (media_mediums IS NULL OR (media_families ?| ARRAY['vinyl'] OR media_mediums ? 'cd'))
    AND (country IS NULL OR country = ANY(ARRAY['US', 'UK', 'Japan']))
  LIMIT 10000) matched;
\echo genre facet over every match, no cap
EXPLAIN (ANALYZE, BUFFERS)
WITH q AS (SELECT plainto_tsquery('english', :'term') AS tsq)
SELECT genre, COUNT(*) AS cnt FROM release_search, q, jsonb_array_elements_text(genres) AS genre
WHERE title_tsv @@ q.tsq GROUP BY genre ORDER BY cnt DESC LIMIT 20;
