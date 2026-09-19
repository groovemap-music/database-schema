-- gm-database-schema-9c8.3 — the undirected path edge surface. Throwaway spike DDL.
--
-- `shortestPath((a)-[:BY|ON|IS|ALIAS_OF|MEMBER_OF|DERIVED_FROM*..d]-(b))` is
-- undirected and alternates six relationship types. In Neo4j those six types are
-- one relationship space that the expander filters by type. In the relational
-- model they are ten separate edge relations with four different key spaces, so
-- a traversal needs three things the individual relations do not give it:
--
--   1. one node identity across Artist, Label, Release, Master, Genre and Style,
--   2. both directions of every relation in one scannable surface, and
--   3. type alternation for free, because the Cypher never constrains which
--      type comes next.
--
-- `path.edge` is that surface as a view: twenty branches over the ten relations,
-- each relation once per direction, no rows stored. It is the honest starting
-- point, because it adds nothing to what `materialize.sql` already recommends
-- shipping. `path.adj` below is the alternative the Verdict has to rule on.
--
-- Node identity is `(kind, key)`, not a concatenated string. A `'r:'||release_id`
-- token would be one column and would read better, but an equality on it cannot
-- use `by_artist_pkey`; it would need an expression index on every relation in
-- both directions. Keeping the kind as a separate one-byte discriminator leaves
-- every probe on the raw indexed `text` column that 9c8.1 already built.
--
--   a = artist   l = label   r = release   m = master   g = genre   s = style
--
-- What is deliberately absent: `part_of` (style to genre), `sublabel_of`, and
-- every MusicBrainz relationship whose type is not `MEMBER_OF`.
-- `_PATH_REL_TYPES` names six types, and a MusicBrainz `COLLABORATED_WITH` is
-- not one of them however much it looks like a collaboration edge.

CREATE VIEW path.edge (src_kind, src_key, dst_kind, dst_key, rel) AS
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
-- MEMBER_OF -------------------------------------------------------------
UNION ALL SELECT 'a', member_artist_id, 'a', group_artist_id, 'MEMBER_OF' FROM path.member_of
UNION ALL SELECT 'a', group_artist_id, 'a', member_artist_id, 'MEMBER_OF' FROM path.member_of
-- MEMBER_OF, MusicBrainz provenance. One relationship space in Neo4j, two
-- relations in two key spaces here. See the note in `augment.sql`.
UNION ALL SELECT 'a', member_artist_id, 'a', group_artist_id, 'MEMBER_OF' FROM path.mb_member_of
UNION ALL SELECT 'a', group_artist_id, 'a', member_artist_id, 'MEMBER_OF' FROM path.mb_member_of;

-- ---------------------------------------------------------------------------
-- Degree, so the shape of the graph is on the record before any timing is
-- ---------------------------------------------------------------------------

CREATE VIEW path.degree AS
SELECT src_kind AS kind, src_key AS key, count(*) AS degree
FROM path.edge GROUP BY 1, 2;
