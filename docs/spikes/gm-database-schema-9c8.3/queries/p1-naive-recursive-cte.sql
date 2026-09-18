-- p1 — shortestPath translated literally into WITH RECURSIVE.
--
--   MATCH p = shortestPath((a)-[:BY|ON|IS|ALIAS_OF|MEMBER_OF|DERIVED_FROM*..d]-(b))
--
-- This is the form a straight port reaches for, and the spike measures it so the
-- document can say what it costs rather than assert that it is wrong.
--
-- The cycle guard is the only pruning a single recursive CTE can express. A
-- recursive CTE may not reference its own working table twice, so it cannot
-- anti-join the frontier against everything already seen; `NOT (token = ANY(seen))`
-- checks the current path only. That makes the recursion enumerate every SIMPLE
-- PATH of length at most d, not every node at distance at most d — and with a
-- mean degree of 13 and genre vertices of degree 137,000, the two quantities
-- diverge immediately.
--
-- PostgreSQL's `CYCLE ... SET ... USING` clause expresses the same guard more
-- neatly and prunes exactly as much: it too is per-path, not global.
--
-- Placeholders, substituted by the harness: `sk`/`skey` is the source kind and
-- key, `tk`/`tkey` the target, and the depth is the clamped max_depth, 1 to 10.

WITH RECURSIVE walk (kind, key, depth, seen, rels) AS (
    SELECT :'sk'::"char", :'skey'::text, 0, ARRAY[:'sk' || ':' || :'skey'], ARRAY[]::text[]
    UNION ALL
    SELECT e.dst_kind,
           e.dst_key,
           w.depth + 1,
           w.seen || (e.dst_kind::text || ':' || e.dst_key),
           w.rels || e.rel
    FROM walk AS w
    JOIN path.edge AS e ON e.src_kind = w.kind AND e.src_key = w.key
    WHERE w.depth < :depth
      AND NOT ((e.dst_kind::text || ':' || e.dst_key) = ANY (w.seen))
)
SELECT depth, seen AS nodes, rels
FROM walk
WHERE kind = :'tk'::"char" AND key = :'tkey'::text
ORDER BY depth
LIMIT 1;
