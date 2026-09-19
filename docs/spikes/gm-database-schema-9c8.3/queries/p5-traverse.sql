-- p5 — `get_explore_traversal`'s `(start)-[:...*1..n]-(discovered)`.
-- Definition in ../bfs.sql. No target, so no early exit: level n must be
-- exhausted before the first row exists.
SELECT count(*) AS rows, max(dist) AS max_dist
FROM path.traverse(:'sk'::"char", :'skey'::text, :hops);
