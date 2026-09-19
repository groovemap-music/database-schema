// Q1 — multi-hop collaborators at depth 2.
// Verbatim from catalog-api api/queries/network_queries.py
// :: get_multi_hop_collaborators. Parameters $artist_id, $depth, $limit are set
// by the runner with cypher-shell `:param`.
MATCH (a:Artist {id: $artist_id})
CALL {
    WITH a
    MATCH path = (a)<-[:BY]-(:Release)-[:BY]->(hop1:Artist)
    WHERE hop1 <> a
    WITH a, hop1, count(DISTINCT nodes(path)[1]) AS shared
    RETURN hop1 AS collaborator, 1 AS hops, shared
    UNION
    WITH a
    MATCH (a)<-[:BY]-(:Release)-[:BY]->(mid:Artist)<-[:BY]-(:Release)-[:BY]->(hop2:Artist)
    WHERE mid <> a AND hop2 <> a AND $depth >= 2
          AND NOT EXISTS { MATCH (a)<-[:BY]-(:Release)-[:BY]->(hop2) }
    WITH hop2 AS collaborator, 2 AS hops, count(DISTINCT mid) AS shared
    WHERE hops <= $depth
    RETURN collaborator, hops, shared
}
WITH collaborator, min(hops) AS distance, sum(shared) AS collaboration_count
ORDER BY distance ASC, collaboration_count DESC
LIMIT $limit
RETURN collaborator.id AS artist_id,
       collaborator.name AS artist_name,
       distance,
       collaboration_count;
