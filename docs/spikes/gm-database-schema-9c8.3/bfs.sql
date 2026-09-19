-- gm-database-schema-9c8.3 — frontier BFS over `path.edge`. Throwaway spike DDL.
--
-- `queries/p1-naive-recursive-cte.sql` is the literal translation of
-- `shortestPath` into `WITH RECURSIVE`, and the spike document reports what it
-- costs. This file is the second attempt: level-synchronous breadth-first search
-- with an explicit visited set, which is what `shortestPath` actually does.
--
-- Why this is not one recursive CTE. A recursive CTE cannot reference its own
-- working table twice, so it cannot anti-join the frontier against everything
-- seen so far. `UNION` instead of `UNION ALL` deduplicates the whole output row,
-- which works only while the row is the bare node — the moment a path array or a
-- depth is carried, two arrivals at one node are two distinct rows and the
-- pruning stops. That leaves two options that do prune: carry the path and guard
-- with `NOT (v = ANY(path))`, which enumerates every simple path rather than
-- every node (p1, and it is why p1 is unusable), or drive the levels from
-- outside the statement, which is this file. The bead calls the second one
-- "bounded UNION unrolling"; the loop below is that unrolling with the bound
-- supplied at call time instead of written out ten times.
--
-- Everything is UNLOGGED and truncated per call. The visited set is the working
-- memory of one request, so a product would use a temporary table; UNLOGGED
-- relations keep the plans inspectable from a second session while a
-- measurement runs.

-- Re-runnable: every object this file creates is dropped first, so a fix to one
-- function does not require rebuilding the catalog.
DROP FUNCTION IF EXISTS path.traverse("char", text, int);
DROP FUNCTION IF EXISTS path.shortest_bi("char", text, "char", text, int);
DROP FUNCTION IF EXISTS path.shortest_uni("char", text, "char", text, int);
DROP FUNCTION IF EXISTS path.trace(smallint, "char", text);
DROP FUNCTION IF EXISTS path.expand(smallint, int);
DROP TYPE IF EXISTS path.result CASCADE;
DROP TABLE IF EXISTS path.frontier;
DROP TABLE IF EXISTS path.visited;

CREATE UNLOGGED TABLE path.visited (
    side     smallint NOT NULL,
    kind     "char"   NOT NULL,
    key      text     NOT NULL,
    depth    int      NOT NULL,
    par_kind "char",
    par_key  text,
    rel      text,
    PRIMARY KEY (side, kind, key)
);

CREATE UNLOGGED TABLE path.frontier (
    side smallint NOT NULL,
    gen  int      NOT NULL,
    kind "char"   NOT NULL,
    key  text     NOT NULL
);
CREATE INDEX frontier_gen ON path.frontier (side, gen);

CREATE TYPE path.result AS (
    found       boolean,
    depth       int,
    nodes       text[],
    rels        text[],
    visited     bigint,
    level_sizes bigint[]
);

-- ---------------------------------------------------------------------------
-- One level
-- ---------------------------------------------------------------------------
--
-- `ON CONFLICT DO NOTHING` is the visited check. It is one index probe per
-- candidate against the primary key that is already being maintained, rather
-- than an anti-join followed by an insert that probes the same index twice.
-- Rows that survive it are, by construction, exactly the nodes seen for the
-- first time — which at level d means the nodes whose shortest distance is d —
-- so `RETURNING` is the next frontier with no further work.
--
-- A node reached twice within one level keeps whichever parent the executor
-- inserted first. Both are shortest paths of the same length; which one is
-- reported is not stable across runs, and the spike reports lengths rather than
-- particular paths for that reason.

CREATE FUNCTION path.expand(_side smallint, _depth int) RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE
    _added bigint;
BEGIN
    -- The frontier swings between one row and several hundred thousand across
    -- the levels of a single search. Without this the planner keeps whatever
    -- estimate the first level produced and picks a nested loop over twenty
    -- branches when it should pick a hash join, or the reverse.
    ANALYZE path.frontier;

    WITH nxt AS (
        INSERT INTO path.visited (side, kind, key, depth, par_kind, par_key, rel)
        SELECT _side, e.dst_kind, e.dst_key, _depth, f.kind, f.key, e.rel
        FROM path.frontier AS f
        JOIN path.edge AS e ON e.src_kind = f.kind AND e.src_key = f.key
        WHERE f.side = _side AND f.gen = _depth - 1
        ON CONFLICT (side, kind, key) DO NOTHING
        RETURNING kind, key
    )
    INSERT INTO path.frontier (side, gen, kind, key)
    SELECT _side, _depth, kind, key FROM nxt;

    GET DIAGNOSTICS _added = ROW_COUNT;
    DELETE FROM path.frontier WHERE side = _side AND gen = _depth - 1;
    RETURN _added;
END;
$$;

-- ---------------------------------------------------------------------------
-- Path reconstruction from the parent pointers
-- ---------------------------------------------------------------------------

CREATE FUNCTION path.trace(_side smallint, _kind "char", _key text)
RETURNS TABLE (nodes text[], rels text[])
LANGUAGE sql STABLE AS $$
    WITH RECURSIVE back AS (
        SELECT v.kind, v.key, v.depth, v.par_kind, v.par_key, v.rel
        FROM path.visited AS v
        WHERE v.side = _side AND v.kind = _kind AND v.key = _key
        UNION ALL
        SELECT p.kind, p.key, p.depth, p.par_kind, p.par_key, p.rel
        FROM back AS b
        JOIN path.visited AS p
          ON p.side = _side AND p.kind = b.par_kind AND p.key = b.par_key
    )
    SELECT array_agg(kind::text || ':' || key ORDER BY depth),
           array_remove(array_agg(rel ORDER BY depth), NULL)
    FROM back;
$$;

-- ---------------------------------------------------------------------------
-- Unidirectional shortest path — the direct analogue of `shortestPath`
-- ---------------------------------------------------------------------------

CREATE FUNCTION path.shortest_uni(
    _sk "char", _skey text, _tk "char", _tkey text, _max_depth int
) RETURNS path.result
LANGUAGE plpgsql AS $$
DECLARE
    r      path.result;
    d      int;
    added  bigint;
BEGIN
    r.found := false;
    r.level_sizes := ARRAY[]::bigint[];

    TRUNCATE path.visited, path.frontier;
    INSERT INTO path.visited VALUES (0, _sk, _skey, 0, NULL, NULL, NULL);
    INSERT INTO path.frontier VALUES (0, 0, _sk, _skey);

    IF _sk = _tk AND _skey = _tkey THEN
        r.found := true; r.depth := 0; r.visited := 1;
        RETURN r;
    END IF;

    FOR d IN 1.._max_depth LOOP
        added := path.expand(0::smallint, d);
        r.level_sizes := r.level_sizes || added;
        EXIT WHEN added = 0;

        PERFORM 1 FROM path.visited
        WHERE side = 0 AND kind = _tk AND key = _tkey;
        IF FOUND THEN
            r.found := true;
            r.depth := d;
            EXIT;
        END IF;
    END LOOP;

    SELECT count(*) INTO r.visited FROM path.visited WHERE side = 0;
    IF r.found THEN
        SELECT t.nodes, t.rels INTO r.nodes, r.rels FROM path.trace(0::smallint, _tk, _tkey) AS t;
    END IF;
    RETURN r;
END;
$$;

-- ---------------------------------------------------------------------------
-- Bidirectional shortest path
-- ---------------------------------------------------------------------------
--
-- Alternate one level from each end and, after every expansion, take the
-- minimum of `forward.depth + backward.depth` over every node both sides have
-- seen. That minimum is the true shortest distance the first time it exists:
-- any path of length L <= df + db has a node at distance df from the source, and
-- that node is then at distance L - df <= db from the target, so it is in both
-- visited sets; and any node in both sets witnesses a walk of exactly
-- f.depth + b.depth. Checking the whole visited sets rather than only the two
-- frontiers is what makes it exact — stopping the moment the frontiers touch is
-- the version of this algorithm that is off by one.
--
-- The edge surface is symmetric by construction, every relation appearing in
-- `path.edge` once per direction, so the backward search walks the same view
-- with no separate reverse structure.

CREATE FUNCTION path.shortest_bi(
    _sk "char", _skey text, _tk "char", _tkey text, _max_depth int
) RETURNS path.result
LANGUAGE plpgsql AS $$
DECLARE
    r        path.result;
    df       int := 0;
    db       int := 0;
    added    bigint;
    fsize    bigint;
    bsize    bigint;
    meet     record;
BEGIN
    r.found := false;
    r.level_sizes := ARRAY[]::bigint[];

    TRUNCATE path.visited, path.frontier;
    INSERT INTO path.visited VALUES (0, _sk, _skey, 0, NULL, NULL, NULL);
    INSERT INTO path.frontier VALUES (0, 0, _sk, _skey);
    INSERT INTO path.visited VALUES (1, _tk, _tkey, 0, NULL, NULL, NULL);
    INSERT INTO path.frontier VALUES (1, 0, _tk, _tkey);

    IF _sk = _tk AND _skey = _tkey THEN
        r.found := true; r.depth := 0; r.visited := 1;
        RETURN r;
    END IF;

    WHILE df + db < _max_depth LOOP
        -- Expand whichever side has the smaller frontier. This is the whole
        -- point of searching from both ends: the cost of a level is the size of
        -- the frontier it expands, and against a graph with hub vertices the two
        -- ends grow at wildly different rates.
        SELECT count(*) FILTER (WHERE side = 0 AND gen = df),
               count(*) FILTER (WHERE side = 1 AND gen = db)
        INTO fsize, bsize FROM path.frontier;

        IF fsize <= bsize AND df + db + 1 <= _max_depth THEN
            df := df + 1; added := path.expand(0::smallint, df);
        ELSE
            db := db + 1; added := path.expand(1::smallint, db);
        END IF;
        r.level_sizes := r.level_sizes || added;
        EXIT WHEN added = 0;

        SELECT f.kind, f.key, f.depth + b.depth AS total
        INTO meet
        FROM path.visited AS f
        JOIN path.visited AS b ON b.side = 1 AND b.kind = f.kind AND b.key = f.key
        WHERE f.side = 0
        ORDER BY f.depth + b.depth
        LIMIT 1;

        IF meet.total IS NOT NULL AND meet.total <= _max_depth THEN
            r.found := true;
            r.depth := meet.total;
            EXIT;
        END IF;
    END LOOP;

    SELECT count(*) INTO r.visited FROM path.visited;

    IF r.found THEN
        -- Stitch the two halves: source to meeting point forward, then the
        -- target half reversed.
        SELECT fwd.nodes || rev.nodes[2:], fwd.rels || rev.rels
        INTO r.nodes, r.rels
        FROM path.trace(0::smallint, meet.kind, meet.key) AS fwd,
             LATERAL (
                 SELECT t.nodes, t.rels FROM path.trace(1::smallint, meet.kind, meet.key) AS t
             ) AS raw,
             LATERAL (
                 SELECT (SELECT array_agg(n ORDER BY i DESC) FROM unnest(raw.nodes) WITH ORDINALITY AS u(n, i)) AS nodes,
                        (SELECT array_agg(x ORDER BY i DESC) FROM unnest(raw.rels)  WITH ORDINALITY AS w(x, i)) AS rels
             ) AS rev;
    END IF;
    RETURN r;
END;
$$;

-- ---------------------------------------------------------------------------
-- Bounded undirected traversal — `get_explore_traversal`'s `*1..n`
-- ---------------------------------------------------------------------------
--
-- No target, so no early exit: every level up to n must be exhausted before the
-- first row can be returned. `LIMIT 100` cannot rescue it, because the Cypher
-- orders by distance and takes the best path per discovered node, which is an
-- aggregate over the whole traversal. The visited set's parent pointers already
-- hold the shortest path to every node, so the projection is a trace per
-- surviving row rather than a second traversal.

CREATE FUNCTION path.traverse(_sk "char", _skey text, _hops int)
RETURNS TABLE (id text, name text, type text, path_names text[], rel_types text[], dist int)
LANGUAGE plpgsql AS $$
DECLARE
    d     int;
    added bigint;
BEGIN
    TRUNCATE path.visited, path.frontier;
    INSERT INTO path.visited VALUES (0, _sk, _skey, 0, NULL, NULL, NULL);
    INSERT INTO path.frontier VALUES (0, 0, _sk, _skey);

    FOR d IN 1.._hops LOOP
        added := path.expand(0::smallint, d);
        EXIT WHEN added = 0;
    END LOOP;

    RETURN QUERY
    WITH discovered AS (
        SELECT v.kind, v.key, v.depth
        FROM path.visited AS v
        WHERE v.side = 0
          AND v.depth > 0
          AND v.kind IN ('a', 'l', 'g', 's')
        ORDER BY v.depth
        LIMIT 100
    )
    SELECT d2.key,
           coalesce(
               CASE d2.kind WHEN 'a' THEN (SELECT x.name FROM graph_mat.artist AS x WHERE x.artist_id = d2.key)
                            WHEN 'l' THEN (SELECT x.name FROM graph_mat.label  AS x WHERE x.label_id  = d2.key)
                            ELSE d2.key END,
               d2.key),
           CASE d2.kind WHEN 'a' THEN 'artist' WHEN 'l' THEN 'label' WHEN 'g' THEN 'genre' ELSE 'style' END,
           t.nodes, t.rels, d2.depth
    FROM discovered AS d2, LATERAL path.trace(0::smallint, d2.kind, d2.key) AS t
    ORDER BY d2.depth;
END;
$$;
