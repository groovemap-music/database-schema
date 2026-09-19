-- gm-database-schema-gkt.1 — search state. Throwaway spike DDL.
--
-- The vertex-at-a-time searches in `pathfinder.sql.in` need two relations and
-- nothing else: the set of vertices already seen from each end, and the queue of
-- vertices at the level currently being expanded.
--
-- `pf.seen` is a table rather than an hstore or an array held in a PL/pgSQL
-- variable, and the choice is load-bearing rather than incidental. The searches
-- this spike exists to measure are the ones that stop early, where the seen set
-- is a few hundred rows and any representation is fast. The searches that decide
-- the Verdict are the ones that do NOT stop early, where the seen set reaches
-- 1.25 million rows — and at that size an hstore is rebuilt on every assignment
-- and an array is scanned linearly. A table keyed on the probe is the only one
-- of the three whose cost per membership test does not grow with the set. The
-- spike measures the hstore variant too, in `pf.find_path_hstore`, so the claim
-- is on the record rather than asserted.
--
-- UNLOGGED, not TEMPORARY. A temporary table is what a product would use — it is
-- per-session, so two concurrent path lookups cannot collide — but a temporary
-- relation is invisible from a second connection, and every plan capture in
-- `results/` was taken from a second connection while a measurement ran. The
-- Recommendation says TEMPORARY; the harness uses UNLOGGED so its own evidence
-- can be audited. Nothing else differs: both are unlogged writes to local
-- buffers, and the measured cost of the two is reported in the document.

DROP SCHEMA IF EXISTS pf CASCADE;
CREATE SCHEMA pf;

-- `(kind, key)` rather than a concatenated `'a:5665'` token, for the reason
-- `gm-database-schema-9c8.3/edges.sql` gives: an equality on a concatenation
-- cannot use the `text` indexes `materialize.sql` already built, and would need
-- an expression index on all ten relations in both directions.
CREATE UNLOGGED TABLE pf.seen (
    side     smallint NOT NULL,
    kind     "char"   NOT NULL,
    key      text     NOT NULL,
    depth    int      NOT NULL,
    par_kind "char",
    par_key  text,
    rel      text,
    PRIMARY KEY (side, kind, key)
);

-- The frontier is not a second relation. It is `WHERE side = s AND depth = d`
-- over the relation that is being written anyway, and this index is what makes
-- that a scan of the level rather than of the table. It leads on the two columns
-- the predicate fixes and carries `kind` and `key`, so the frontier scan is
-- index-only and touches no heap page.
--
-- An earlier revision of this harness kept a separate `pf.queue`, which is the
-- obvious shape and is what `gm-database-schema-9c8.3/bfs.sql` does. It costs a
-- second heap insert, a second index insert and a sequence nextval for every
-- vertex discovered, and a hub expansion discovers 148,544 of them in one
-- statement. Deriving the frontier from `pf.seen` instead cut the worst measured
-- case by roughly two fifths, and it is the reason the Recommendation asks for
-- ONE request-scoped relation rather than two.
--
-- Ordering by `(kind, key)` rather than by discovery order is deliberate. It is
-- reproducible, which discovery order within a level is not once the level is
-- built by many statements, and it is uncorrelated with cost — which is the
-- point: the baseline variant must not accidentally expand cheap vertices first,
-- because expanding cheap vertices first is exactly what the `_deg` variant is
-- being measured FOR.
CREATE INDEX pf_seen_level ON pf.seen (side, depth, kind, key);

-- What a search returns. `expanded`, `probes` and `seen_rows` are the work
-- counters the Evidence section reports beside the latency: a latency on its own
-- cannot distinguish a search that stopped early from one that got lucky on a
-- warm cache.
CREATE TYPE pf.result AS (
    found       boolean,
    depth       int,
    nodes       text[],
    rels        text[],
    expanded    bigint,   -- frontier vertices expanded, one statement pair each
    probes      bigint,   -- touch probes issued, one per expanded vertex
    seen_rows   bigint,   -- rows in pf.seen when the search returned
    levels      int[]     -- (side, depth) pairs flattened, for the level profile
);

-- ---------------------------------------------------------------------------
-- Path reconstruction from the parent pointers
-- ---------------------------------------------------------------------------
--
-- Unchanged from `gm-database-schema-9c8.3/bfs.sql`, deliberately: the two
-- spikes' answers are compared against each other as well as against Neo4j, and
-- a difference in reconstruction would show up as a difference in answer.

CREATE FUNCTION pf.trace(_side smallint, _kind "char", _key text)
RETURNS TABLE (nodes text[], rels text[])
LANGUAGE sql STABLE AS $$
    WITH RECURSIVE back AS (
        SELECT v.kind, v.key, v.depth, v.par_kind, v.par_key, v.rel
        FROM pf.seen AS v
        WHERE v.side = _side AND v.kind = _kind AND v.key = _key
        UNION ALL
        SELECT p.kind, p.key, p.depth, p.par_kind, p.par_key, p.rel
        FROM back AS b
        JOIN pf.seen AS p
          ON p.side = _side AND p.kind = b.par_kind AND p.key = b.par_key
    )
    SELECT array_agg(kind::text || ':' || key ORDER BY depth),
           array_remove(array_agg(rel ORDER BY depth), NULL)
    FROM back;
$$;
