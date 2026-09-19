// Q3 — artist MusicBrainz relationship listing.
// Verbatim from catalog-api api/queries/musicbrainz_queries.py
// :: get_artist_mb_relationships. Parameter $discogs_id is set by the runner.
//
// The relationship is untyped and the provenance filter is an edge PROPERTY, so
// Neo4j expands every artist-to-artist edge off the seed and discards the
// Discogs-sourced ones. That is the structural difference from the PostgreSQL
// port, where the MusicBrainz relation is already provider-scoped.
MATCH (a:Artist {id: $discogs_id})-[r]->(target:Artist)
WHERE r.source = 'musicbrainz'
RETURN type(r) AS type, target.id AS target_id, target.name AS target_name,
       'outgoing' AS direction, r.begin_date AS begin_date,
       r.end_date AS end_date, r.attributes AS attributes
UNION ALL
MATCH (source:Artist)-[r]->(a:Artist {id: $discogs_id})
WHERE r.source = 'musicbrainz'
RETURN type(r) AS type, source.id AS target_id, source.name AS target_name,
       'incoming' AS direction, r.begin_date AS begin_date,
       r.end_date AS end_date, r.attributes AS attributes;
