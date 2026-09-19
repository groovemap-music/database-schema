-- gm-database-schema-gkt.1 — the undirected traversal surface. Throwaway spike DDL.
--
-- `pf.edge` is `gm-database-schema-9c8.3/edges.sql`'s `path.edge`, reproduced
-- here rather than reused so this spike's schema can be dropped without taking
-- the sibling's with it, and so the second view below can exist beside it.
-- Twenty-two branches over ten relations, each relation once per direction, no
-- rows stored. Both directions of all ten are indexed already — eight by
-- `gm-database-schema-9c8.1/materialize.sql`, the other two by that spike's
-- `augment.sql` — so every branch is an index scan on `src_key`.
--
-- The six relationship types are `_PATH_REL_TYPES` from
-- `api/queries/neo4j_queries.py:83`, no more and no fewer:
-- BY, ON, IS, ALIAS_OF, MEMBER_OF, DERIVED_FROM. `part_of`, `sublabel_of` and
-- every MusicBrainz relationship whose type is not `MEMBER_OF` are deliberately
-- absent.
--
-- MEMBER_OF is two relations, not one. `path.member_of` is Discogs, derived from
-- the artist documents; `path.mb_member_of` is MusicBrainz, keyed on MBIDs in
-- `musicbrainz.relationships` and reaching a Discogs id only through
-- `musicbrainz.artists.discogs_artist_id`, which `augment.sql` resolves at build
-- time through `graph.mb_relationship_type`'s mapping. In Neo4j both provenances
-- share one relationship space and `shortestPath` traverses both. At the
-- synthetic scale the MusicBrainz side is the LARGER of the two, so a rewrite
-- that traverses the Discogs relation alone does not lose an edge case, it loses
-- most of the relation.

CREATE VIEW pf.edge (src_kind, src_key, dst_kind, dst_key, rel) AS
-- BY --------------------------------------------------------------------
SELECT 'r'::"char", release_id, 'a'::"char", artist_id, 'BY'::text  FROM graph_mat.by_artist
UNION ALL SELECT 'a', artist_id, 'r', release_id, 'BY'              FROM graph_mat.by_artist
UNION ALL SELECT 'm', master_id, 'a', artist_id, 'BY'               FROM graph_mat.master_by_artist
UNION ALL SELECT 'a', artist_id, 'm', master_id, 'BY'               FROM graph_mat.master_by_artist
-- ON --------------------------------------------------------------------
UNION ALL SELECT 'r', release_id, 'l', label_id, 'ON'               FROM graph_mat.on_label
UNION ALL SELECT 'l', label_id, 'r', release_id, 'ON'               FROM graph_mat.on_label
-- IS --------------------------------------------------------------------
UNION ALL SELECT 'r', release_id, 'g', genre_name, 'IS'             FROM graph_mat.in_genre
UNION ALL SELECT 'g', genre_name, 'r', release_id, 'IS'             FROM graph_mat.in_genre
UNION ALL SELECT 'r', release_id, 's', style_name, 'IS'             FROM graph_mat.in_style
UNION ALL SELECT 's', style_name, 'r', release_id, 'IS'             FROM graph_mat.in_style
UNION ALL SELECT 'm', master_id, 'g', genre_name, 'IS'              FROM graph_mat.master_in_genre
UNION ALL SELECT 'g', genre_name, 'm', master_id, 'IS'              FROM graph_mat.master_in_genre
UNION ALL SELECT 'm', master_id, 's', style_name, 'IS'              FROM graph_mat.master_in_style
UNION ALL SELECT 's', style_name, 'm', master_id, 'IS'              FROM graph_mat.master_in_style
-- DERIVED_FROM ----------------------------------------------------------
UNION ALL SELECT 'r', release_id, 'm', master_id, 'DERIVED_FROM'    FROM graph_mat.derived_from
UNION ALL SELECT 'm', master_id, 'r', release_id, 'DERIVED_FROM'    FROM graph_mat.derived_from
-- ALIAS_OF --------------------------------------------------------------
UNION ALL SELECT 'a', alias_artist_id, 'a', artist_id, 'ALIAS_OF'   FROM path.alias_of
UNION ALL SELECT 'a', artist_id, 'a', alias_artist_id, 'ALIAS_OF'   FROM path.alias_of
-- MEMBER_OF, Discogs provenance -----------------------------------------
UNION ALL SELECT 'a', member_artist_id, 'a', group_artist_id, 'MEMBER_OF' FROM path.member_of
UNION ALL SELECT 'a', group_artist_id, 'a', member_artist_id, 'MEMBER_OF' FROM path.member_of
-- MEMBER_OF, MusicBrainz provenance -------------------------------------
UNION ALL SELECT 'a', member_artist_id, 'a', group_artist_id, 'MEMBER_OF' FROM path.mb_member_of
UNION ALL SELECT 'a', group_artist_id, 'a', member_artist_id, 'MEMBER_OF' FROM path.mb_member_of;

-- ---------------------------------------------------------------------------
-- The same surface without the IS edge class
-- ---------------------------------------------------------------------------
--
-- For information only. `IS` is the Release-to-Genre and Release-to-Style class,
-- and it is both the largest class in the surface and the one that carries the
-- fifteen Genre vertices of degree ~137,000 that
-- `gm-database-schema-9c8.3` identified as the thing that decides the cost of a
-- traversal here. The Evidence section reports every case twice, with this view
-- and with the one above, so the size of that effect is a measured number rather
-- than an inference.
--
-- This is NOT a proposal to drop `IS`. It is in `_PATH_REL_TYPES`, a path
-- through a shared genre is a real answer a user asked for, and removing it
-- changes answers. The two columns exist so that whoever later argues about
-- hub-vertex handling — a degree cap, a per-type weight, a separate traversal
-- mode — has the magnitude in front of them.

CREATE VIEW pf.edge_no_is (src_kind, src_key, dst_kind, dst_key, rel) AS
SELECT 'r'::"char", release_id, 'a'::"char", artist_id, 'BY'::text  FROM graph_mat.by_artist
UNION ALL SELECT 'a', artist_id, 'r', release_id, 'BY'              FROM graph_mat.by_artist
UNION ALL SELECT 'm', master_id, 'a', artist_id, 'BY'               FROM graph_mat.master_by_artist
UNION ALL SELECT 'a', artist_id, 'm', master_id, 'BY'               FROM graph_mat.master_by_artist
UNION ALL SELECT 'r', release_id, 'l', label_id, 'ON'               FROM graph_mat.on_label
UNION ALL SELECT 'l', label_id, 'r', release_id, 'ON'               FROM graph_mat.on_label
UNION ALL SELECT 'r', release_id, 'm', master_id, 'DERIVED_FROM'    FROM graph_mat.derived_from
UNION ALL SELECT 'm', master_id, 'r', release_id, 'DERIVED_FROM'    FROM graph_mat.derived_from
UNION ALL SELECT 'a', alias_artist_id, 'a', artist_id, 'ALIAS_OF'   FROM path.alias_of
UNION ALL SELECT 'a', artist_id, 'a', alias_artist_id, 'ALIAS_OF'   FROM path.alias_of
UNION ALL SELECT 'a', member_artist_id, 'a', group_artist_id, 'MEMBER_OF' FROM path.member_of
UNION ALL SELECT 'a', group_artist_id, 'a', member_artist_id, 'MEMBER_OF' FROM path.member_of
UNION ALL SELECT 'a', member_artist_id, 'a', group_artist_id, 'MEMBER_OF' FROM path.mb_member_of
UNION ALL SELECT 'a', group_artist_id, 'a', member_artist_id, 'MEMBER_OF' FROM path.mb_member_of;

-- The shape of the graph, on the record before any timing is taken.
CREATE VIEW pf.edge_class AS
SELECT rel, count(*) AS directed_rows FROM pf.edge GROUP BY rel;
