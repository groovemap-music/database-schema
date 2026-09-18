-- p3 — the same BFS from both ends, meeting in the middle.
-- Definition in ../bfs.sql. This is what Neo4j's `shortestPath` does.
SELECT found, depth, visited, level_sizes, nodes, rels
FROM path.shortest_bi(:'sk'::"char", :'skey'::text, :'tk'::"char", :'tkey'::text, :depth) AS t;
