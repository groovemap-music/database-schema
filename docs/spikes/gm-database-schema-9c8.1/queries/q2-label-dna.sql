-- Q2 — label-to-genre aggregation ("label DNA").
--
-- Ported from catalog-api `api/queries/label_dna_queries.py`,
-- `get_label_genre_profile`:
--
--     MATCH (r:Release)-[:ON]->(l:Label {id: $label_id}), (r)-[:IS]->(g:Genre)
--     WITH g.name AS name, count(DISTINCT r) AS count
--     RETURN name, count
--     ORDER BY count DESC
--
-- Two edge families off one release, entered from the label end. No LIMIT: the
-- genre vocabulary is fifteen names, so the result is small however large the
-- label is, and the cost is entirely in reaching the label's releases.

SELECT genre_name                    AS name,
       count(DISTINCT release_id)    AS count
FROM GRAPH_TABLE (:graph
    MATCH (l IS label)<-[IS on_label]-(r IS release)-[IS in_genre]->(g IS genre)
    WHERE l.label_id = :'seed_label'
    COLUMNS (g.name AS genre_name, r.release_id AS release_id)
) AS dna
GROUP BY genre_name
ORDER BY count DESC;
