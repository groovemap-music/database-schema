-- Throwaway DDL for the gm-database-schema-9c8.1 spike. Not product schema.
--
-- The read measurements say materializing the edges is necessary. They do NOT
-- say which mechanism should write them, because a materialized view and a
-- loader-written table are the same heap plus the same index at read time and
-- measure identically. The difference is entirely in what it costs to keep them
-- current, so that is what this script measures.
--
-- Run with `\timing on`. `graph.by_artist` is the subject: it is the busiest
-- Discogs edge relation and the one all three read families lean on hardest.
--
-- Drop with: DROP MATERIALIZED VIEW IF EXISTS graph_mat.by_artist_mv;

\timing on

-- 1. Initial build. This is also what a loader's first full backfill costs, so
--    it is the floor for either mechanism.
DROP MATERIALIZED VIEW IF EXISTS graph_mat.by_artist_mv;
CREATE MATERIALIZED VIEW graph_mat.by_artist_mv AS SELECT release_id, artist_id FROM graph.by_artist;

-- REFRESH ... CONCURRENTLY requires a unique index, and the pair is already the
-- edge's natural key.
CREATE UNIQUE INDEX by_artist_mv_key ON graph_mat.by_artist_mv (release_id, artist_id);
CREATE INDEX by_artist_mv_reverse ON graph_mat.by_artist_mv (artist_id, release_id);
ANALYZE graph_mat.by_artist_mv;

-- 2. Full refresh. Takes an ACCESS EXCLUSIVE lock: every reader of the relation
--    blocks for the whole duration.
REFRESH MATERIALIZED VIEW graph_mat.by_artist_mv;

-- 3. Concurrent refresh. Readers are not blocked, but the whole relation is
--    still recomputed AND then diffed against the existing copy, so it is
--    strictly more work than the full refresh, not less.
REFRESH MATERIALIZED VIEW CONCURRENTLY graph_mat.by_artist_mv;

-- 4. What a loader writes instead, for ONE changed release. This is the
--    comparison that decides the recommendation: an edge table maintained by the
--    loader pays per changed release, while either REFRESH pays per catalog.
BEGIN;
DELETE FROM graph_mat.by_artist WHERE release_id = '424242';
INSERT INTO graph_mat.by_artist (release_id, artist_id)
    SELECT release_id, artist_id FROM graph.by_artist WHERE release_id = '424242';
COMMIT;

-- 5. The same incremental write, expressed against the base table rather than
--    the view, to confirm the two are equivalent.
--
--    They are. Step 4 is already fast, and the reason matters more than the
--    number: a predicate on an edge view's SOURCE column is a predicate on
--    `releases.data_id`, a real column with a primary key, and the planner
--    pushes it through the view's DISTINCT down to an index scan of one row.
--    Only the TARGET side is trapped inside the JSONB array. See
--    ./probe-direction.sql, which is where that asymmetry is measured — it is
--    the finding the whole spike turns on.
--
--    So the loader needs no privileged access to the parsed document to write
--    its edges cheaply; reading the shipped view back for the one release it
--    just wrote costs about a millisecond.
BEGIN;
DELETE FROM graph_mat.by_artist WHERE release_id = '424242';
INSERT INTO graph_mat.by_artist (release_id, artist_id)
SELECT DISTINCT releases.data_id, btrim(element.value ->> 'id')
FROM public.releases AS releases
CROSS JOIN LATERAL jsonb_array_elements(
    CASE WHEN jsonb_typeof(releases.data -> 'artists') = 'array' THEN releases.data -> 'artists' ELSE '[]'::jsonb END
) AS element(value)
WHERE releases.data_id = '424242'
  AND NULLIF(btrim(element.value ->> 'id'), '') IS NOT NULL
  AND btrim(element.value ->> 'id') <> '0';
COMMIT;

\timing off
