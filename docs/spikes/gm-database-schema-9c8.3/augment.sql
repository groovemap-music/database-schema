-- gm-database-schema-9c8.3 — the ALIAS_OF and MEMBER_OF edges. Throwaway spike DDL.
--
-- Why this file exists. `_PATH_REL_TYPES` in `api/queries/neo4j_queries.py` is
-- `BY|ON|IS|ALIAS_OF|MEMBER_OF|DERIVED_FROM`. Eight of the ten relations behind
-- those six types are already materialized by
-- `gm-database-schema-9c8.1/materialize.sql`. `alias_of` and `member_of` are not,
-- and the sibling spike had no reason to materialize them: none of its three
-- reads touches them.
--
-- They cannot be skipped here. They are the ONLY artist-to-artist edges in the
-- path set. Without them every artist-to-artist path is forced through a
-- Release, and the spike would be measuring a graph the product does not have.
--
-- Worse, the sibling spike's catalog is asymmetric on exactly these edges.
-- `generate.py` does emit them — `iter_mb_relationships` types 15% of its stream
-- from `DISCOGS_RELATIONSHIP_TYPES = ("ALIAS_OF", "MEMBER_OF")` — but writes them
-- only to the Neo4j target, dropping them from the PostgreSQL target because in
-- the shipped schema those edges come from the Discogs artist documents, and the
-- generator emits no `aliases` or `groups` block for them to come from. Neo4j has
-- them; PostgreSQL does not. Measuring the two engines across that gap would not
-- be a comparison.
--
-- So `artist_edges.py` replays the same stream and this file writes the blocks
-- the shipped views read, then reads those views back — the identical check
-- `materialize.sql` makes for the other eight relations. Both engines end up
-- holding the same artist-to-artist edges, from the same seed, in the same order.
-- Nothing in `gm-database-schema-9c8.1/` is edited.
--
-- `stage.sql` creates the `path` schema and the staging table, `load-path-edges.sh`
-- streams `artist_edges.py` into it through `\copy`, and this file runs last.

CREATE INDEX artist_edge_stage_owner ON path.artist_edge_stage (owner_id, block);
ANALYZE path.artist_edge_stage;

-- ---------------------------------------------------------------------------
-- Write the blocks into the Discogs artist documents
-- ---------------------------------------------------------------------------
--
-- Stripped first so the file is re-runnable and so no block left by an earlier
-- run survives into a document that this run gives no block to.

UPDATE public.artists SET data = data - 'aliases' - 'groups' - 'members'
WHERE data ?| ARRAY['aliases', 'groups', 'members'];

WITH blocks AS (
    SELECT owner_id,
           jsonb_object_agg(block, elements) AS block
    FROM (
        SELECT owner_id, block,
               jsonb_agg(DISTINCT jsonb_build_object('id', element_id, 'name', '')) AS elements
        FROM path.artist_edge_stage
        WHERE owner_id <> element_id
        GROUP BY owner_id, block
    ) AS per_block
    GROUP BY owner_id
)
UPDATE public.artists AS a
SET data = a.data || blocks.block
FROM blocks
WHERE a.data_id = blocks.owner_id;

-- ---------------------------------------------------------------------------
-- Materialize the two relations from the shipped views
-- ---------------------------------------------------------------------------
--
-- Both directions indexed, per 9c8.1's recommendation. These two are small, so
-- the index set is not what decides their cost; it is written the same way as
-- the other eight so the traversal has one access path to reason about.

CREATE TABLE path.alias_of (
    alias_artist_id text NOT NULL,
    artist_id       text NOT NULL,
    PRIMARY KEY (alias_artist_id, artist_id)
);
CREATE INDEX alias_of_reverse ON path.alias_of (artist_id, alias_artist_id);

CREATE TABLE path.member_of (
    member_artist_id text NOT NULL,
    group_artist_id  text NOT NULL,
    PRIMARY KEY (member_artist_id, group_artist_id)
);
CREATE INDEX member_of_reverse ON path.member_of (group_artist_id, member_artist_id);

INSERT INTO path.alias_of
SELECT DISTINCT alias_artist_id, artist_id::text FROM graph.alias_of
WHERE alias_artist_id <> artist_id::text;

INSERT INTO path.member_of
SELECT DISTINCT member_artist_id, group_artist_id::text FROM graph.member_of
WHERE member_artist_id <> group_artist_id::text;

-- ---------------------------------------------------------------------------
-- The MusicBrainz-sourced MEMBER_OF edges
-- ---------------------------------------------------------------------------
--
-- This relation is a finding, not a convenience, and it is the reason the spike
-- carries a note forward to whoever rewrites `find_shortest_path`.
--
-- `MEMBER_OF` is in `_PATH_REL_TYPES`, and it is ALSO in the enricher's
-- MusicBrainz relationship vocabulary — `generate.py`'s `MB_RELATIONSHIP_TYPES`
-- names it first, matching `brainzgraphinator`'s own type names. In Neo4j both
-- provenances share one relationship space, so
-- `shortestPath((a)-[:...|MEMBER_OF|...*..d]-(b))` traverses BOTH: a Discogs
-- band membership and a MusicBrainz "member of" assertion are the same edge type
-- to the expander.
--
-- In PostgreSQL they are two different relations in two different key spaces.
-- `graph.member_of` is Discogs-only, derived from artist documents and keyed on
-- Discogs artist ids. The MusicBrainz ones live in `musicbrainz.relationships`,
-- keyed on MBIDs, and reach a Discogs id only through
-- `musicbrainz.artists.discogs_artist_id`. A rewrite that traverses
-- `graph.member_of` alone silently drops them and returns different paths from
-- the Cypher it replaces.
--
-- The spike includes them, because Neo4j does and the two graphs have to be the
-- same graph. The cost of the extra key-space crossing is paid at build time
-- here rather than per traversal step, which is itself the recommendation.

CREATE TABLE path.mb_member_of (
    member_artist_id text NOT NULL,
    group_artist_id  text NOT NULL,
    PRIMARY KEY (member_artist_id, group_artist_id)
);
CREATE INDEX mb_member_of_reverse ON path.mb_member_of (group_artist_id, member_artist_id);

INSERT INTO path.mb_member_of
SELECT DISTINCT s.discogs_artist_id::text, t.discogs_artist_id::text
FROM graph_mat.mb_rel_artist_artist AS r
JOIN graph_mat.mb_artist AS s ON s.mbid = r.source_mbid
JOIN graph_mat.mb_artist AS t ON t.mbid = r.target_mbid
WHERE r.relationship_type = 'MEMBER_OF'
  AND s.discogs_artist_id IS NOT NULL
  AND t.discogs_artist_id IS NOT NULL
  AND s.discogs_artist_id <> t.discogs_artist_id;

ANALYZE path.alias_of;
ANALYZE path.member_of;
ANALYZE path.mb_member_of;
