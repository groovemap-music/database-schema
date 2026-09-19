-- Q3 — artist MusicBrainz relationship listing.
--
-- Ported from catalog-api `api/queries/musicbrainz_queries.py`,
-- `get_artist_mb_relationships`. The Cypher is two UNION ALL legs over an
-- UNTYPED relationship between two `:Artist` nodes, separated by an edge
-- property:
--
--     MATCH (a:Artist {id: $discogs_id})-[r]->(target:Artist)
--     WHERE r.source = 'musicbrainz'
--
-- The shapes differ here in a way worth naming, because it is a property of the
-- two data models rather than of this spike. In Neo4j one relationship space
-- holds every artist-to-artist edge from every provider, so `r.source` is a
-- filter the query has to apply and the planner has to expand past. In
-- PostgreSQL `musicbrainz.relationships` is MusicBrainz-only by construction —
-- it has no `source` column — and `graph.mb_rel_artist_artist` already restricts
-- to the artist-to-artist pair, so the provenance filter costs nothing because
-- the relation IS the filter. The Discogs-sourced edges the Cypher excludes live
-- in `graph.alias_of` and `graph.member_of` and are never touched.
--
-- The entry point also differs. The Cypher starts from a Discogs artist id
-- directly; here it starts from `mb_artist.discogs_artist_id`, the column that
-- carries the same link, so the cost of crossing from the Discogs key space into
-- the MusicBrainz key space is inside the measurement rather than assumed away.

SELECT relationship_type AS type,
       target_id,
       target_name,
       'outgoing'::text  AS direction,
       begin_date,
       end_date,
       attributes
FROM GRAPH_TABLE (:graph
    MATCH (s IS mb_artist)-[e IS mb_rel_artist_artist]->(t IS mb_artist)
    WHERE s.discogs_artist_id = :seed_artist_id
    COLUMNS (e.relationship_type AS relationship_type,
             t.discogs_artist_id AS target_id,
             t.name              AS target_name,
             e.begin_date        AS begin_date,
             e.end_date          AS end_date,
             e.attributes        AS attributes)
) AS outgoing
UNION ALL
SELECT relationship_type,
       target_id,
       target_name,
       'incoming'::text,
       begin_date,
       end_date,
       attributes
FROM GRAPH_TABLE (:graph
    MATCH (s IS mb_artist)<-[e IS mb_rel_artist_artist]-(t IS mb_artist)
    WHERE s.discogs_artist_id = :seed_artist_id
    COLUMNS (e.relationship_type AS relationship_type,
             t.discogs_artist_id AS target_id,
             t.name              AS target_name,
             e.begin_date        AS begin_date,
             e.end_date          AS end_date,
             e.attributes        AS attributes)
) AS incoming;
