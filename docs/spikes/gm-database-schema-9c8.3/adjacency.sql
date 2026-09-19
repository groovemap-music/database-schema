-- gm-database-schema-9c8.3 — precomputed adjacency. Throwaway spike DDL.
--
-- `path.edge` is a view: twenty branches, ten relations, four key spaces, `text`
-- keys. Every frontier expansion probes all twenty branches, because the frontier
-- carries mixed vertex kinds and PostgreSQL can only prune a UNION ALL branch
-- against a constant. This file is the alternative the Verdict has to rule on:
-- one relation, one dense `bigint` node space, both directions stored, indexed
-- so the expansion is a single index-only scan.
--
-- It is not free. It is a second copy of the whole edge set, it has to be
-- maintained beside the edge tables 9c8.1 recommends, and the node numbering has
-- to be stable across a catalog that grows. The spike measures what it buys so
-- that cost can be weighed rather than guessed at.
--
-- `rel` is one byte rather than the relationship name, and it is IN the index,
-- so reconstructing which types a path alternated through costs no heap visit.

DROP TABLE IF EXISTS path.adj;
DROP TABLE IF EXISTS path.node;

CREATE UNLOGGED TABLE path.node (
    node_id bigint  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind    "char"  NOT NULL,
    key     text    NOT NULL
);

INSERT INTO path.node (kind, key)
SELECT 'a', artist_id FROM graph_mat.artist
UNION ALL SELECT 'l', label_id  FROM graph_mat.label
UNION ALL SELECT 'r', release_id FROM graph_mat.release
UNION ALL SELECT 'm', master_id FROM graph_mat.master
UNION ALL SELECT 'g', name      FROM graph_mat.genre
UNION ALL SELECT 's', name      FROM graph_mat.style;

CREATE UNIQUE INDEX node_kind_key ON path.node (kind, key);
ANALYZE path.node;

CREATE UNLOGGED TABLE path.adj (
    src bigint NOT NULL,
    dst bigint NOT NULL,
    rel "char" NOT NULL
);

INSERT INTO path.adj (src, dst, rel)
SELECT s.node_id, d.node_id,
       CASE e.rel WHEN 'BY' THEN 'B' WHEN 'ON' THEN 'O' WHEN 'IS' THEN 'I'
                  WHEN 'DERIVED_FROM' THEN 'D' WHEN 'ALIAS_OF' THEN 'A' ELSE 'M' END::"char"
FROM path.edge AS e
JOIN path.node AS s ON s.kind = e.src_kind AND s.key = e.src_key
JOIN path.node AS d ON d.kind = e.dst_kind AND d.key = e.dst_key;

CREATE INDEX adj_src ON path.adj (src, dst, rel);
ANALYZE path.adj;

-- ---------------------------------------------------------------------------
-- The same two searches, over the dense adjacency
-- ---------------------------------------------------------------------------

DROP FUNCTION IF EXISTS path.adj_shortest_bi(bigint, bigint, int);
DROP FUNCTION IF EXISTS path.adj_shortest_uni(bigint, bigint, int);
DROP FUNCTION IF EXISTS path.adj_expand(smallint, int);
DROP TABLE IF EXISTS path.adj_frontier;
DROP TABLE IF EXISTS path.adj_visited;

CREATE UNLOGGED TABLE path.adj_visited (
    side    smallint NOT NULL,
    node_id bigint   NOT NULL,
    depth   int      NOT NULL,
    parent  bigint,
    rel     "char",
    PRIMARY KEY (side, node_id)
);

CREATE UNLOGGED TABLE path.adj_frontier (
    side    smallint NOT NULL,
    gen     int      NOT NULL,
    node_id bigint   NOT NULL
);
CREATE INDEX adj_frontier_gen ON path.adj_frontier (side, gen);

CREATE FUNCTION path.adj_expand(_side smallint, _depth int) RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE
    _added bigint;
BEGIN
    ANALYZE path.adj_frontier;
    WITH nxt AS (
        INSERT INTO path.adj_visited (side, node_id, depth, parent, rel)
        SELECT _side, a.dst, _depth, f.node_id, a.rel
        FROM path.adj_frontier AS f
        JOIN path.adj AS a ON a.src = f.node_id
        WHERE f.side = _side AND f.gen = _depth - 1
        ON CONFLICT (side, node_id) DO NOTHING
        RETURNING node_id
    )
    INSERT INTO path.adj_frontier (side, gen, node_id)
    SELECT _side, _depth, node_id FROM nxt;
    GET DIAGNOSTICS _added = ROW_COUNT;
    DELETE FROM path.adj_frontier WHERE side = _side AND gen = _depth - 1;
    RETURN _added;
END;
$$;

CREATE FUNCTION path.adj_shortest_uni(_s bigint, _t bigint, _max_depth int)
RETURNS path.result LANGUAGE plpgsql AS $$
DECLARE
    r path.result; d int; added bigint;
BEGIN
    r.found := false; r.level_sizes := ARRAY[]::bigint[];
    TRUNCATE path.adj_visited, path.adj_frontier;
    INSERT INTO path.adj_visited VALUES (0, _s, 0, NULL, NULL);
    INSERT INTO path.adj_frontier VALUES (0, 0, _s);
    IF _s = _t THEN r.found := true; r.depth := 0; r.visited := 1; RETURN r; END IF;

    FOR d IN 1.._max_depth LOOP
        added := path.adj_expand(0::smallint, d);
        r.level_sizes := r.level_sizes || added;
        EXIT WHEN added = 0;
        PERFORM 1 FROM path.adj_visited WHERE side = 0 AND node_id = _t;
        IF FOUND THEN r.found := true; r.depth := d; EXIT; END IF;
    END LOOP;

    SELECT count(*) INTO r.visited FROM path.adj_visited WHERE side = 0;
    RETURN r;
END;
$$;

CREATE FUNCTION path.adj_shortest_bi(_s bigint, _t bigint, _max_depth int)
RETURNS path.result LANGUAGE plpgsql AS $$
DECLARE
    r path.result; df int := 0; db int := 0;
    added bigint; fsize bigint; bsize bigint; best int;
BEGIN
    r.found := false; r.level_sizes := ARRAY[]::bigint[];
    TRUNCATE path.adj_visited, path.adj_frontier;
    INSERT INTO path.adj_visited VALUES (0, _s, 0, NULL, NULL);
    INSERT INTO path.adj_frontier VALUES (0, 0, _s);
    INSERT INTO path.adj_visited VALUES (1, _t, 0, NULL, NULL);
    INSERT INTO path.adj_frontier VALUES (1, 0, _t);
    IF _s = _t THEN r.found := true; r.depth := 0; r.visited := 1; RETURN r; END IF;

    WHILE df + db < _max_depth LOOP
        SELECT count(*) FILTER (WHERE side = 0 AND gen = df),
               count(*) FILTER (WHERE side = 1 AND gen = db)
        INTO fsize, bsize FROM path.adj_frontier;

        IF fsize <= bsize THEN df := df + 1; added := path.adj_expand(0::smallint, df);
        ELSE                  db := db + 1; added := path.adj_expand(1::smallint, db);
        END IF;
        r.level_sizes := r.level_sizes || added;
        EXIT WHEN added = 0;

        SELECT min(f.depth + b.depth) INTO best
        FROM path.adj_visited AS f
        JOIN path.adj_visited AS b ON b.side = 1 AND b.node_id = f.node_id
        WHERE f.side = 0;

        IF best IS NOT NULL AND best <= _max_depth THEN
            r.found := true; r.depth := best; EXIT;
        END IF;
    END LOOP;

    SELECT count(*) INTO r.visited FROM path.adj_visited;
    RETURN r;
END;
$$;
