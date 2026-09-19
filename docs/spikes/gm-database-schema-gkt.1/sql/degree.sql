-- gm-database-schema-gkt.1 — the precomputed degree relation. Throwaway spike DDL.
--
-- One row per vertex of the traversal surface, holding its undirected degree.
-- This is the ONLY precomputed structure any variant in this spike needs, and it
-- is deliberately much smaller than the alternative gm-database-schema-9c8.3
-- priced: that spike's `path.adj` is a second copy of the whole edge set at
-- 1,649 MB, kept current against a growing catalog, for a 1.3x to 1.4x gain that
-- did not change its verdict. This is one bigint per vertex.
--
-- It buys two things, both of them ordering decisions rather than access paths:
-- which side of the bidirectional search to expand next, and which vertex of
-- that side's frontier to expand first. Neither changes an answer.

DROP TABLE IF EXISTS pf.degree;

CREATE TABLE pf.degree (
    kind "char"  NOT NULL,
    key  text    NOT NULL,
    deg  bigint  NOT NULL,
    PRIMARY KEY (kind, key)
);

INSERT INTO pf.degree (kind, key, deg)
SELECT src_kind, src_key, count(*) FROM pf.edge GROUP BY 1, 2;

ANALYZE pf.degree;

-- On the record, because the Recommendation quotes it: what the hub vertices are
-- and how far they are from the rest of the distribution.
