-- p2 — level-synchronous BFS with a global visited set, one end.
-- Definition in ../bfs.sql. This is `shortestPath`'s own algorithm minus the
-- search from the far end.
SELECT found, depth, visited, level_sizes, nodes, rels
FROM path.shortest_uni(:'sk'::"char", :'skey'::text, :'tk'::"char", :'tkey'::text, :depth) AS t;
