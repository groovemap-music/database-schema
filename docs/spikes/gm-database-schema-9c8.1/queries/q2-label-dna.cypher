// Q2 — label-to-genre aggregation ("label DNA").
// Verbatim from catalog-api api/queries/label_dna_queries.py
// :: get_label_genre_profile. Parameter $label_id is set by the runner.
MATCH (r:Release)-[:ON]->(l:Label {id: $label_id}), (r)-[:IS]->(g:Genre)
WITH g.name AS name, count(DISTINCT r) AS count
RETURN name, count
ORDER BY count DESC;
