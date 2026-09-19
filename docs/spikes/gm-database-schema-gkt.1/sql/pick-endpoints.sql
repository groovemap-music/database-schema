-- gm-database-schema-gkt.1 — how the endpoint cases were chosen. Throwaway spike DDL.
--
-- gm-database-schema-9c8.3 chose its endpoints once, by hand, from one
-- exhaustive breadth-first search, and then HARDCODED them, because its sibling
-- had picked seeds with `ORDER BY degree DESC OFFSET n` and no tiebreak and a
-- rerun could silently measure a different vertex. This spike keeps that
-- discipline and keeps its ids hardcoded too.
--
-- It needs a second set of them. The 9c8.3 ids are synthetic-scale ids — artist
-- 5665 of 120,000 — and the fixture scale has 1,500 artists, so at that scale
-- every one of those cases is a lookup of a vertex that does not exist and every
-- case is a miss. Measuring that and calling it "the fixture scale" would be
-- measuring nothing eight times.
--
-- So this file derives the fixture-scale set by the SAME rule, and the ids it
-- produced are then written into `bench/workloads.py` by hand exactly as 9c8.3
-- wrote its own down. It is committed so the choice can be audited and
-- reproduced, not so it can be re-run at measurement time.
--
-- The rule, stated so it can be checked:
--   root      the highest-degree Artist, ties broken by the lowest numeric id
--   dN        the lowest-numbered Artist at distance N from the root
--   rim root  the lowest-numbered degree-1 Artist at maximum distance from the
--             root, used for the cases deeper than the root's eccentricity
--   miss      the lowest-numbered Artist in no component the root can reach

DROP FUNCTION IF EXISTS pf.bfs_all("char", text, int);
CREATE FUNCTION pf.bfs_all(_sk "char", _skey text, _max int DEFAULT 12)
-- The output columns are prefixed because `RETURNS TABLE` puts them in scope
-- as PL/pgSQL variables, and a bare `kind` inside `ON CONFLICT` then resolves to
-- the variable rather than to the column.
RETURNS TABLE (v_kind "char", v_key text, v_depth int)
LANGUAGE plpgsql AS $$
DECLARE
    d int;
    n bigint;
BEGIN
    TRUNCATE pf.seen;
    INSERT INTO pf.seen (side, kind, key, depth) VALUES (0, _sk, _skey, 0);
    FOR d IN 1.._max LOOP
        INSERT INTO pf.seen (side, kind, key, depth, par_kind, par_key, rel)
        SELECT 0, e.dst_kind, e.dst_key, d, f.kind, f.key, e.rel
        FROM pf.seen AS f
        JOIN pf.edge AS e ON e.src_kind = f.kind AND e.src_key = f.key
        WHERE f.side = 0 AND f.depth = d - 1
        ON CONFLICT (side, kind, key) DO NOTHING;
        GET DIAGNOSTICS n = ROW_COUNT;
        EXIT WHEN n = 0;
    END LOOP;
    RETURN QUERY SELECT s.kind, s.key, s.depth FROM pf.seen AS s WHERE s.side = 0;
END;
$$;

-- 1. the root
SELECT 'root' AS role, g.key AS artist_id, g.deg
FROM pf.degree AS g WHERE g.kind = 'a'
ORDER BY g.deg DESC, g.key::bigint LIMIT 1;
