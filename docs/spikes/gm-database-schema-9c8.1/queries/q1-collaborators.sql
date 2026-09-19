-- Q1 — multi-hop collaborators at depth 2.
--
-- Ported from catalog-api `api/queries/network_queries.py`,
-- `get_multi_hop_collaborators`. The Cypher UNIONs a one-hop leg and a two-hop
-- leg inside a CALL, takes `min(hops)` and `sum(shared)` per collaborator, and
-- ORDERs and LIMITs BEFORE projecting the collaborator's name. The name join is
-- therefore after the LIMIT here too: doing it before would read fifty thousand
-- artist rows to return fifty.
--
-- `:graph` selects the declaration under test — `graph.catalog` over the shipped
-- JSONB views, or `graph_mat.catalog` over the materialized edge relations. The
-- query text is otherwise identical between backends, which is the whole point:
-- a difference in the measured time is a difference in access path.
--
-- `:artist_relation` is the vertex relation the post-LIMIT name join reads, and
-- has to track `:graph`.

WITH hop1 AS (
    SELECT collaborator, count(DISTINCT release_id) AS shared
    FROM GRAPH_TABLE (:graph
        MATCH (a IS artist)<-[IS by_artist]-(r IS release)-[IS by_artist]->(h IS artist)
        WHERE a.artist_id = :'seed_artist' AND h.artist_id <> :'seed_artist'
        COLUMNS (h.artist_id AS collaborator, r.release_id AS release_id)
    ) AS leg
    GROUP BY collaborator
),
-- The anti-join against hop1 is the Cypher's
-- `NOT EXISTS { MATCH (a)<-[:BY]-(:Release)-[:BY]->(hop2) }`: a collaborator
-- already reachable in one hop is never re-reported at distance two.
hop2 AS (
    SELECT collaborator, count(DISTINCT mid) AS shared
    FROM GRAPH_TABLE (:graph
        MATCH (a IS artist)<-[IS by_artist]-(r1 IS release)-[IS by_artist]->(m IS artist)
              <-[IS by_artist]-(r2 IS release)-[IS by_artist]->(h IS artist)
        WHERE a.artist_id = :'seed_artist'
          AND m.artist_id <> :'seed_artist'
          AND h.artist_id <> :'seed_artist'
        COLUMNS (h.artist_id AS collaborator, m.artist_id AS mid)
    ) AS leg
    WHERE NOT EXISTS (SELECT 1 FROM hop1 WHERE hop1.collaborator = leg.collaborator)
    GROUP BY collaborator
),
ranked AS (
    SELECT collaborator,
           min(hops)   AS distance,
           sum(shared) AS collaboration_count
    FROM (
        SELECT collaborator, 1 AS hops, shared FROM hop1
        UNION ALL
        SELECT collaborator, 2 AS hops, shared FROM hop2
    ) AS legs
    GROUP BY collaborator
    ORDER BY distance ASC, collaboration_count DESC
    LIMIT :collab_limit
)
SELECT ranked.collaborator       AS artist_id,
       vertex.name               AS artist_name,
       ranked.distance,
       ranked.collaboration_count
FROM ranked
JOIN :artist_relation AS vertex ON vertex.artist_id = ranked.collaborator
ORDER BY ranked.distance ASC, ranked.collaboration_count DESC;
