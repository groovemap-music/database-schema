// get_explore_traversal, verbatim from api/queries/recommend_queries.py:403,
// with `hops` filled in. Like `max_depth` above it is interpolated, not bound.
//
// @DEPTH@
MATCH (start:Artist {id: $entity_id})
MATCH path = (start)-[:BY|ON|IS|ALIAS_OF|MEMBER_OF|DERIVED_FROM*1..@DEPTH@]-(discovered)
WHERE discovered <> start
  AND (discovered:Artist OR discovered:Label OR discovered:Genre OR discovered:Style)
WITH discovered,
     [n IN nodes(path) | coalesce(n.name, n.title, n.id)] AS path_names,
     [r IN relationships(path) | type(r)] AS rel_types,
     length(path) AS dist
ORDER BY dist
WITH discovered, collect({path_names: path_names, rel_types: rel_types, dist: dist})[0] AS best
RETURN coalesce(discovered.id, discovered.name) AS id,
       coalesce(discovered.name, discovered.id) AS name,
       CASE
         WHEN discovered:Artist THEN 'artist'
         WHEN discovered:Label THEN 'label'
         WHEN discovered:Genre THEN 'genre'
         WHEN discovered:Style THEN 'style'
       END AS type,
       best.path_names AS path_names, best.rel_types AS rel_types, best.dist AS dist
ORDER BY best.dist
LIMIT 100;
