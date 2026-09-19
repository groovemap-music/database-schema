-- Throwaway diagnostic for the gm-database-schema-9c8.1 spike. Not product schema.
--
-- The single measurement that explains every other number in the spike.
--
-- An edge view over a JSONB document is NOT uniformly expensive. It is cheap in
-- one direction and catastrophic in the other, and which direction is which is a
-- property of where the two endpoints live:
--
--   SOURCE (`release_id`) is `releases.data_id` — a real column, primary key.
--     The planner pushes an equality on it through the view's DISTINCT and down
--     to an index scan of exactly one heap row.
--
--   TARGET (`artist_id`, `label_id`, `genre_name`) lives INSIDE the JSONB array
--     that the view unnests. No index can reach it: the value only exists after
--     `jsonb_array_elements` has produced it, so the predicate cannot be
--     evaluated before the row has been read and expanded. Every probe by target
--     is therefore a full scan of the base table plus a full unnest of every
--     array in it.
--
-- This matters because catalog-api reads the graph from the target side almost
-- exclusively — "this artist's collaborators", "this label's genres" — which is
-- the direction that does not work.
--
-- A GIN index on `data -> 'artists'` does not rescue this. GIN would serve a
-- containment predicate written against the document
-- (`data @> '{"artists":[{"id":"5665"}]}'`), but the view does not express one:
-- it unnests first and filters afterward, and PostgreSQL will not rewrite the
-- second form into the first. Nor can an expression index be built over a
-- set-returning function. The limit is structural, not a missing index.

\timing on

-- Probe by SOURCE. Expect an index scan and single-digit buffers.
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT * FROM graph.by_artist WHERE release_id = :'probe_release';

-- Probe by TARGET. Expect a full scan of every release and every artists array.
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT * FROM graph.by_artist WHERE artist_id = :'probe_artist';

-- The same two probes against the materialized declaration, where both
-- directions are indexed and neither is inside a document.
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT * FROM graph_mat.by_artist WHERE release_id = :'probe_release';

EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT * FROM graph_mat.by_artist WHERE artist_id = :'probe_artist';

\timing off
