// find_shortest_path, verbatim from api/queries/neo4j_queries.py:97, with the
// three values that function interpolates rather than parameterises filled in:
// the two node labels and the depth. `max_depth` CANNOT be a Cypher parameter —
// that is the reason the function clamps it to [1, 10] server-side, and the
// reason this file is a template rather than a query.
//
// @FROM_LABEL@ @FROM_PROP@ @TO_LABEL@ @TO_PROP@ @DEPTH@
MATCH (a:@FROM_LABEL@ {@FROM_PROP@: $from_id}), (b:@TO_LABEL@ {@TO_PROP@: $to_id})
MATCH p = shortestPath((a)-[:BY|ON|IS|ALIAS_OF|MEMBER_OF|DERIVED_FROM*..@DEPTH@]-(b))
RETURN [node IN nodes(p) | {
           id: coalesce(node.id, node.name),
           name: coalesce(node.name, node.title, ''),
           labels: labels(node)
       }] AS nodes,
       [rel IN relationships(p) | type(rel)] AS rels;
