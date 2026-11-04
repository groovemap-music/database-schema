-- With sequential scans disabled, can each consumer predicate use the index that claims to serve it?
SET enable_seqscan = off; SET enable_bitmapscan = on; SET jit = off;
\echo --- genres: consumer shape (IS NULL OR ?|)
EXPLAIN (COSTS OFF) SELECT data_id FROM bench.r WHERE ((data->'genres') IS NULL OR (data->'genres') ?| ARRAY['Jazz']);
\echo --- genres: bare operator, for contrast
EXPLAIN (COSTS OFF) SELECT data_id FROM bench.r WHERE (data->'genres') ?| ARRAY['Jazz'];
\echo --- media: consumer shape
EXPLAIN (COSTS OFF) SELECT data_id FROM bench.r WHERE (media IS NULL OR ((media->'families') ?| ARRAY['vinyl']));
\echo --- year: consumer shape
EXPLAIN (COSTS OFF) SELECT data_id FROM bench.r WHERE ((data->>'year') IS NULL OR (data->>'year')::int >= 1990);
\echo --- country: consumer shape
EXPLAIN (COSTS OFF) SELECT data_id FROM bench.r WHERE ((data->>'country') IS NULL OR (data->>'country') = ANY(ARRAY['Japan']));
