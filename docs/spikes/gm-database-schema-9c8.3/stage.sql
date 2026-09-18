-- gm-database-schema-9c8.3 — the `path` schema and the staging table that
-- `load-path-edges.sh` streams `artist_edges.py` into. See `augment.sql` for why
-- the stream has to be replayed at all.
DROP SCHEMA IF EXISTS path CASCADE;
CREATE SCHEMA path;

CREATE UNLOGGED TABLE path.artist_edge_stage (
    owner_id   text NOT NULL,
    element_id text NOT NULL,
    block      text NOT NULL
);
