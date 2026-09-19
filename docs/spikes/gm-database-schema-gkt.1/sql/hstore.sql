-- gm-database-schema-gkt.1 — the same search with the seen set in an hstore.
-- Throwaway spike DDL. For the Recommendation, not for the Verdict.
--
-- `pf.seen` is a table. The obvious objection is that a table is heavy for what
-- is really the working memory of one request: every membership test is an index
-- probe through the buffer manager, every insertion is a heap tuple, and the
-- whole thing is truncated and rebuilt per call. An in-memory associative array
-- held in a PL/pgSQL variable should be faster.
--
-- It is, and only while the set is small. This function is here so the
-- Recommendation can name the crossover rather than guess it. It answers found
-- and depth and keeps the work counters; it does not reconstruct the path, which
-- is not what is being measured and would need the parent chain kept as well.
--
-- The cost this exposes is not hstore's lookup, which is a hash probe. It is
-- that a PL/pgSQL variable passed into a SQL statement is passed BY VALUE: every
-- one of the two statements per expanded vertex serialises the whole seen set
-- into the parameter and the executor detoasts it again. At a few hundred
-- entries that is free. At the 1.25 million entries a hub expansion produces it
-- is quadratic, and the function does not finish.

CREATE EXTENSION IF NOT EXISTS hstore;

DROP FUNCTION IF EXISTS pf.find_path_hstore("char", text, "char", text, int);

CREATE FUNCTION pf.find_path_hstore(
    _sk "char", _skey text, _tk "char", _tkey text, _max_depth int DEFAULT 6
) RETURNS pf.result
LANGUAGE plpgsql AS $$
DECLARE
    r       pf.result;
    seen    hstore[] := ARRAY[hstore(''), hstore('')];   -- index 1 = forward, 2 = backward
    front   text[][];
    fwd     text[] := ARRAY[]::text[];
    bwd     text[] := ARRAY[]::text[];
    nxt     text[];
    df      int := 0;
    db      int := 0;
    _side   int;
    _other  int;
    tok     text;
    v       text;
    hit     record;
    added   bigint;
BEGIN
    _max_depth := greatest(1, least(coalesce(_max_depth, 6), 10));
    r.found := false; r.expanded := 0; r.probes := 0; r.levels := ARRAY[]::int[];

    IF _sk = _tk AND _skey = _tkey THEN
        r.found := true; r.depth := 0; r.seen_rows := 0; RETURN r;
    END IF;

    seen[1] := hstore(_sk::text || ':' || _skey, '0');
    seen[2] := hstore(_tk::text || ':' || _tkey, '0');
    fwd := ARRAY[_sk::text || ':' || _skey];
    bwd := ARRAY[_tk::text || ':' || _tkey];

    <<search>>
    WHILE df + db < _max_depth LOOP
        EXIT search WHEN cardinality(fwd) = 0 OR cardinality(bwd) = 0;
        IF cardinality(fwd) <= cardinality(bwd) THEN
            _side := 1; _other := 2; front := ARRAY[fwd];
        ELSE
            _side := 2; _other := 1; front := ARRAY[bwd];
        END IF;

        nxt := ARRAY[]::text[];
        added := 0;

        FOREACH v IN ARRAY front[1:] LOOP
            r.probes := r.probes + 1;

            SELECT (e.dst_kind::text || ':' || e.dst_key) AS w,
                   (seen[_other] -> (e.dst_kind::text || ':' || e.dst_key))::int AS odepth
            INTO hit
            FROM pf.edge AS e
            WHERE e.src_kind = substr(v, 1, 1)::"char"
              AND e.src_key  = substr(v, 3)
              AND seen[_other] ? (e.dst_kind::text || ':' || e.dst_key)
            LIMIT 1;

            IF FOUND THEN
                r.found := true;
                r.depth := (CASE WHEN _side = 1 THEN df ELSE db END) + 1 + hit.odepth;
                EXIT search;
            END IF;

            FOR tok IN
                SELECT DISTINCT e.dst_kind::text || ':' || e.dst_key
                FROM pf.edge AS e
                WHERE e.src_kind = substr(v, 1, 1)::"char" AND e.src_key = substr(v, 3)
            LOOP
                IF NOT (seen[_side] ? tok) THEN
                    seen[_side] := seen[_side] || hstore(tok, (CASE WHEN _side = 1 THEN df ELSE db END + 1)::text);
                    nxt := nxt || tok;
                    added := added + 1;
                END IF;
            END LOOP;
            r.expanded := r.expanded + 1;
        END LOOP;

        r.levels := r.levels || added::int;
        IF _side = 1 THEN fwd := nxt; df := df + 1; ELSE bwd := nxt; db := db + 1; END IF;
    END LOOP search;

    r.seen_rows := (SELECT count(*) FROM each(seen[1])) + (SELECT count(*) FROM each(seen[2]));
    RETURN r;
END;
$$;
