-- catalog-api's reference definitions of the precomputed Identity Ledger, verbatim.
--
-- Rendered from catalog-api 6d744e2 (origin/main), scripts/ledger_scale.py, by
-- `_view_statements()`: every view and index statement, in order, each followed
-- by a semicolon. The refresh state table and function are left out; this
-- repository's own are what the suite tests. Names are unqualified on purpose:
-- tests/integration/test_identity_ledger.py runs this file with a scratch schema
-- first on the search_path, so the reference views land there, read one another
-- there, and read every base table from `public`, `graph` and `musicbrainz`.
--
-- Regenerate it only when catalog-api's route SQL changes, from that commit's
-- `_view_statements()`; never edit it by hand.

-- identity_ledger_edition_events
CREATE MATERIALIZED VIEW identity_ledger_edition_events AS
WITH 
kind_alias AS (
    SELECT alias.native_id, alias.provider, alias.external_id, alias.valid_from, alias.valid_to,
           min(alias.valid_from) OVER item AS mint,
           min(alias.valid_from) FILTER (WHERE alias.provider = 'discogs') OVER item AS discogs_at
    FROM provider_aliases AS alias
    JOIN catalog_items AS catalog_item ON catalog_item.id = alias.native_id AND catalog_item.kind = 'release'
    WINDOW item AS (PARTITION BY alias.native_id)
),
moved_in AS (
    SELECT survivor_id AS item_id, valid_from AS at FROM catalog_item_supersessions
    UNION
    SELECT superseded_id, valid_to FROM catalog_item_supersessions WHERE valid_to IS NOT NULL
),
moved_out AS (
    SELECT superseded_id AS item_id, valid_from AS at FROM catalog_item_supersessions
    UNION
    SELECT survivor_id, valid_to FROM catalog_item_supersessions WHERE valid_to IS NOT NULL
),
later AS (
    SELECT kind_alias.native_id, kind_alias.provider, kind_alias.valid_from, kind_alias.discogs_at,
           row_number() OVER (
               PARTITION BY kind_alias.native_id, kind_alias.provider = 'musicbrainz'
               ORDER BY kind_alias.valid_from, kind_alias.external_id
           ) AS position
    FROM kind_alias
    LEFT JOIN moved_in ON moved_in.item_id = kind_alias.native_id AND moved_in.at = kind_alias.valid_from
    WHERE kind_alias.valid_from > kind_alias.mint AND moved_in.item_id IS NULL
),
item_event AS (
    SELECT DISTINCT native_id AS item_id, mint AS at, 'minted' AS type, 'groovemap' AS dated_from
    FROM kind_alias
    UNION ALL
    SELECT native_id, valid_from,
           CASE WHEN provider = 'musicbrainz' AND discogs_at IS NOT NULL AND position = 1
                THEN 'linked_at_ingest' ELSE 'evidence_attached' END,
           'groovemap'
    FROM later
    UNION ALL
    SELECT kind_alias.native_id, kind_alias.valid_to, 'alias_closed', 'groovemap'
    FROM kind_alias
    LEFT JOIN moved_out ON moved_out.item_id = kind_alias.native_id AND moved_out.at = kind_alias.valid_to
    WHERE kind_alias.valid_to IS NOT NULL AND moved_out.item_id IS NULL
    UNION ALL
    SELECT holder.native_id, link.event_at, link.event,
           CASE link.dated_from WHEN 'musicbrainz_fullexport' THEN 'musicbrainz_fullexport' ELSE 'groovemap' END
    FROM identity_link_events AS link
    LEFT JOIN provider_aliases AS holder
           ON holder.provider = link.provider AND holder.entity_kind = link.entity_kind
          AND holder.external_id = link.external_id AND holder.valid_to IS NULL
    WHERE link.provider = 'musicbrainz' AND link.entity_kind = 'release'
),
supersession_of_kind AS (
    SELECT supersession.*
    FROM catalog_item_supersessions AS supersession
    JOIN catalog_items AS superseded ON superseded.id = supersession.superseded_id AND superseded.kind = 'release'
),
side_event AS (
    SELECT item_id, at, type, dated_from FROM item_event WHERE type <> 'minted' AND item_id IS NOT NULL
    UNION ALL
    SELECT side.item_id, supersession.valid_from,
           CASE supersession.cause WHEN 'catalog_reattachment' THEN 'reattached' ELSE 'promoted' END, 'groovemap'
    FROM supersession_of_kind AS supersession
    CROSS JOIN LATERAL (VALUES (supersession.superseded_id), (supersession.survivor_id)) AS side (item_id)
    UNION ALL
    SELECT side.item_id, supersession.valid_to, 'reverted', 'groovemap'
    FROM supersession_of_kind AS supersession
    CROSS JOIN LATERAL (VALUES (supersession.superseded_id), (supersession.survivor_id)) AS side (item_id)
    WHERE supersession.valid_to IS NOT NULL
)
SELECT COALESCE(edition_open.survivor_id, side_event.item_id) AS edition_id, side_event.at, side_event.type, count(*) AS events
FROM side_event
LEFT JOIN catalog_item_supersessions AS edition_open ON edition_open.superseded_id = side_event.item_id AND edition_open.valid_to IS NULL
GROUP BY 1, 2, 3;

-- identity_ledger_edition_events key
CREATE UNIQUE INDEX ON identity_ledger_edition_events (edition_id, at, type);

-- identity_ledger_edition_events at
CREATE INDEX ON identity_ledger_edition_events (at);

-- identity_ledger_edition_events type
CREATE INDEX ON identity_ledger_edition_events (type, at);

-- identity_ledger_edition_parents
CREATE MATERIALIZED VIEW identity_ledger_edition_parents AS
WITH member AS MATERIALIZED (
    SELECT edition_id, edition_id AS item_id FROM (SELECT DISTINCT edition_id FROM identity_ledger_edition_events) AS evented
    UNION
    SELECT supersession.survivor_id, supersession.superseded_id
    FROM catalog_item_supersessions AS supersession
    WHERE supersession.valid_to IS NULL
      AND supersession.survivor_id IN (SELECT edition_id FROM identity_ledger_edition_events)
),

member_mbid AS MATERIALIZED (
    SELECT member.edition_id, release.external_id::uuid AS mbid
    FROM member
    JOIN provider_aliases AS release ON release.native_id = member.item_id
    WHERE release.provider = 'musicbrainz' AND release.entity_kind = 'release' AND release.valid_to IS NULL
),
edition_parent AS (
    SELECT member.edition_id, COALESCE(work_open.survivor_id, master.native_id) AS work_id
    FROM member
    JOIN provider_aliases AS release
      ON release.native_id = member.item_id AND release.provider = 'discogs'
     AND release.entity_kind = 'release' AND release.valid_to IS NULL
    JOIN graph.derived_from AS derived ON derived.release_id = release.external_id
    JOIN provider_aliases AS master
      ON master.provider = 'discogs' AND master.entity_kind = 'master'
     AND master.external_id = derived.master_id AND master.valid_to IS NULL
    LEFT JOIN catalog_item_supersessions AS work_open ON work_open.superseded_id = master.native_id AND work_open.valid_to IS NULL
    UNION
    SELECT member_mbid.edition_id, COALESCE(work_open.survivor_id, master.native_id)
    FROM member_mbid
    JOIN musicbrainz.releases AS mb_release ON mb_release.mbid = member_mbid.mbid
    JOIN provider_aliases AS master
      ON master.provider = 'musicbrainz' AND master.entity_kind = 'master'
     AND master.external_id = mb_release.release_group_mbid::text AND master.valid_to IS NULL
    LEFT JOIN catalog_item_supersessions AS work_open ON work_open.superseded_id = master.native_id AND work_open.valid_to IS NULL
)
SELECT DISTINCT edition_id, work_id FROM edition_parent;

-- identity_ledger_edition_parents key
CREATE UNIQUE INDEX ON identity_ledger_edition_parents (edition_id, work_id);

-- identity_ledger_work_summary
CREATE MATERIALIZED VIEW identity_ledger_work_summary AS
WITH by_type AS (
    SELECT parent.work_id, event.edition_id, event.type, min(event.at) AS first_at, max(event.at) AS last_at
    FROM identity_ledger_edition_events AS event
    JOIN identity_ledger_edition_parents AS parent ON parent.edition_id = event.edition_id
    GROUP BY parent.work_id, event.edition_id, event.type
),
by_edition AS (
    SELECT work_id, edition_id, min(first_at) AS first_at, max(last_at) AS last_at, bool_or(type IN ('reattached', 'promoted', 'reverted')) AS repaired
    FROM by_type
    GROUP BY work_id, edition_id
)
SELECT work_id, 'any'::text AS event, count(*) AS evented_editions, count(*) FILTER (WHERE repaired) AS repaired_editions,
       min(first_at) AS first_event, max(last_at) AS last_event
FROM by_edition
GROUP BY work_id
UNION ALL
SELECT work_id, type, count(*), count(*) FILTER (WHERE type IN ('reattached', 'promoted', 'reverted')), min(first_at), max(last_at)
FROM by_type
GROUP BY work_id, type;

-- identity_ledger_work_summary key
CREATE UNIQUE INDEX ON identity_ledger_work_summary (event, work_id);

-- identity_ledger_work_summary page
CREATE INDEX ON identity_ledger_work_summary (event, last_event DESC, work_id);

-- identity_ledger_density
CREATE MATERIALIZED VIEW identity_ledger_density AS
(WITH 
kind_alias AS (
    SELECT alias.native_id, alias.provider, alias.external_id, alias.valid_from, alias.valid_to,
           min(alias.valid_from) OVER item AS mint,
           min(alias.valid_from) FILTER (WHERE alias.provider = 'discogs') OVER item AS discogs_at
    FROM provider_aliases AS alias
    JOIN catalog_items AS catalog_item ON catalog_item.id = alias.native_id AND catalog_item.kind = 'release'
    WINDOW item AS (PARTITION BY alias.native_id)
),
moved_in AS (
    SELECT survivor_id AS item_id, valid_from AS at FROM catalog_item_supersessions
    UNION
    SELECT superseded_id, valid_to FROM catalog_item_supersessions WHERE valid_to IS NOT NULL
),
moved_out AS (
    SELECT superseded_id AS item_id, valid_from AS at FROM catalog_item_supersessions
    UNION
    SELECT survivor_id, valid_to FROM catalog_item_supersessions WHERE valid_to IS NOT NULL
),
later AS (
    SELECT kind_alias.native_id, kind_alias.provider, kind_alias.valid_from, kind_alias.discogs_at,
           row_number() OVER (
               PARTITION BY kind_alias.native_id, kind_alias.provider = 'musicbrainz'
               ORDER BY kind_alias.valid_from, kind_alias.external_id
           ) AS position
    FROM kind_alias
    LEFT JOIN moved_in ON moved_in.item_id = kind_alias.native_id AND moved_in.at = kind_alias.valid_from
    WHERE kind_alias.valid_from > kind_alias.mint AND moved_in.item_id IS NULL
),
item_event AS (
    SELECT DISTINCT native_id AS item_id, mint AS at, 'minted' AS type, 'groovemap' AS dated_from
    FROM kind_alias
    UNION ALL
    SELECT native_id, valid_from,
           CASE WHEN provider = 'musicbrainz' AND discogs_at IS NOT NULL AND position = 1
                THEN 'linked_at_ingest' ELSE 'evidence_attached' END,
           'groovemap'
    FROM later
    UNION ALL
    SELECT kind_alias.native_id, kind_alias.valid_to, 'alias_closed', 'groovemap'
    FROM kind_alias
    LEFT JOIN moved_out ON moved_out.item_id = kind_alias.native_id AND moved_out.at = kind_alias.valid_to
    WHERE kind_alias.valid_to IS NOT NULL AND moved_out.item_id IS NULL
    UNION ALL
    SELECT holder.native_id, link.event_at, link.event,
           CASE link.dated_from WHEN 'musicbrainz_fullexport' THEN 'musicbrainz_fullexport' ELSE 'groovemap' END
    FROM identity_link_events AS link
    LEFT JOIN provider_aliases AS holder
           ON holder.provider = link.provider AND holder.entity_kind = link.entity_kind
          AND holder.external_id = link.external_id AND holder.valid_to IS NULL
    WHERE link.provider = 'musicbrainz' AND link.entity_kind = 'release'
),
supersession_of_kind AS (
    SELECT supersession.*
    FROM catalog_item_supersessions AS supersession
    JOIN catalog_items AS superseded ON superseded.id = supersession.superseded_id AND superseded.kind = 'release'
),
event AS (
    SELECT at, type, dated_from FROM item_event
    UNION ALL
    SELECT valid_from, CASE cause WHEN 'catalog_reattachment' THEN 'reattached' ELSE 'promoted' END, 'groovemap'
    FROM supersession_of_kind
    UNION ALL
    SELECT valid_to, 'reverted', 'groovemap' FROM supersession_of_kind WHERE valid_to IS NOT NULL
)
SELECT 'release'::text AS kind, to_char(at AT TIME ZONE 'UTC', 'YYYY-MM') AS month, type, dated_from, count(*) AS events
FROM event
GROUP BY 1, 2, 3, 4
ORDER BY 2, 3, 4)
UNION ALL
(WITH 
kind_alias AS (
    SELECT alias.native_id, alias.provider, alias.external_id, alias.valid_from, alias.valid_to,
           min(alias.valid_from) OVER item AS mint,
           min(alias.valid_from) FILTER (WHERE alias.provider = 'discogs') OVER item AS discogs_at
    FROM provider_aliases AS alias
    JOIN catalog_items AS catalog_item ON catalog_item.id = alias.native_id AND catalog_item.kind = 'master'
    WINDOW item AS (PARTITION BY alias.native_id)
),
moved_in AS (
    SELECT survivor_id AS item_id, valid_from AS at FROM catalog_item_supersessions
    UNION
    SELECT superseded_id, valid_to FROM catalog_item_supersessions WHERE valid_to IS NOT NULL
),
moved_out AS (
    SELECT superseded_id AS item_id, valid_from AS at FROM catalog_item_supersessions
    UNION
    SELECT survivor_id, valid_to FROM catalog_item_supersessions WHERE valid_to IS NOT NULL
),
later AS (
    SELECT kind_alias.native_id, kind_alias.provider, kind_alias.valid_from, kind_alias.discogs_at,
           row_number() OVER (
               PARTITION BY kind_alias.native_id, kind_alias.provider = 'musicbrainz'
               ORDER BY kind_alias.valid_from, kind_alias.external_id
           ) AS position
    FROM kind_alias
    LEFT JOIN moved_in ON moved_in.item_id = kind_alias.native_id AND moved_in.at = kind_alias.valid_from
    WHERE kind_alias.valid_from > kind_alias.mint AND moved_in.item_id IS NULL
),
item_event AS (
    SELECT DISTINCT native_id AS item_id, mint AS at, 'minted' AS type, 'groovemap' AS dated_from
    FROM kind_alias
    UNION ALL
    SELECT native_id, valid_from,
           CASE WHEN provider = 'musicbrainz' AND discogs_at IS NOT NULL AND position = 1
                THEN 'linked_at_ingest' ELSE 'evidence_attached' END,
           'groovemap'
    FROM later
    UNION ALL
    SELECT kind_alias.native_id, kind_alias.valid_to, 'alias_closed', 'groovemap'
    FROM kind_alias
    LEFT JOIN moved_out ON moved_out.item_id = kind_alias.native_id AND moved_out.at = kind_alias.valid_to
    WHERE kind_alias.valid_to IS NOT NULL AND moved_out.item_id IS NULL
    UNION ALL
    SELECT holder.native_id, link.event_at, link.event,
           CASE link.dated_from WHEN 'musicbrainz_fullexport' THEN 'musicbrainz_fullexport' ELSE 'groovemap' END
    FROM identity_link_events AS link
    LEFT JOIN provider_aliases AS holder
           ON holder.provider = link.provider AND holder.entity_kind = link.entity_kind
          AND holder.external_id = link.external_id AND holder.valid_to IS NULL
    WHERE link.provider = 'musicbrainz' AND link.entity_kind = 'master'
),
supersession_of_kind AS (
    SELECT supersession.*
    FROM catalog_item_supersessions AS supersession
    JOIN catalog_items AS superseded ON superseded.id = supersession.superseded_id AND superseded.kind = 'master'
),
event AS (
    SELECT at, type, dated_from FROM item_event
    UNION ALL
    SELECT valid_from, CASE cause WHEN 'catalog_reattachment' THEN 'reattached' ELSE 'promoted' END, 'groovemap'
    FROM supersession_of_kind
    UNION ALL
    SELECT valid_to, 'reverted', 'groovemap' FROM supersession_of_kind WHERE valid_to IS NOT NULL
)
SELECT 'master'::text AS kind, to_char(at AT TIME ZONE 'UTC', 'YYYY-MM') AS month, type, dated_from, count(*) AS events
FROM event
GROUP BY 1, 2, 3, 4
ORDER BY 2, 3, 4)
UNION ALL
(WITH 
kind_alias AS (
    SELECT alias.native_id, alias.provider, alias.external_id, alias.valid_from, alias.valid_to,
           min(alias.valid_from) OVER item AS mint,
           min(alias.valid_from) FILTER (WHERE alias.provider = 'discogs') OVER item AS discogs_at
    FROM provider_aliases AS alias
    JOIN catalog_items AS catalog_item ON catalog_item.id = alias.native_id AND catalog_item.kind = 'artist'
    WINDOW item AS (PARTITION BY alias.native_id)
),
moved_in AS (
    SELECT survivor_id AS item_id, valid_from AS at FROM catalog_item_supersessions
    UNION
    SELECT superseded_id, valid_to FROM catalog_item_supersessions WHERE valid_to IS NOT NULL
),
moved_out AS (
    SELECT superseded_id AS item_id, valid_from AS at FROM catalog_item_supersessions
    UNION
    SELECT survivor_id, valid_to FROM catalog_item_supersessions WHERE valid_to IS NOT NULL
),
later AS (
    SELECT kind_alias.native_id, kind_alias.provider, kind_alias.valid_from, kind_alias.discogs_at,
           row_number() OVER (
               PARTITION BY kind_alias.native_id, kind_alias.provider = 'musicbrainz'
               ORDER BY kind_alias.valid_from, kind_alias.external_id
           ) AS position
    FROM kind_alias
    LEFT JOIN moved_in ON moved_in.item_id = kind_alias.native_id AND moved_in.at = kind_alias.valid_from
    WHERE kind_alias.valid_from > kind_alias.mint AND moved_in.item_id IS NULL
),
item_event AS (
    SELECT DISTINCT native_id AS item_id, mint AS at, 'minted' AS type, 'groovemap' AS dated_from
    FROM kind_alias
    UNION ALL
    SELECT native_id, valid_from,
           CASE WHEN provider = 'musicbrainz' AND discogs_at IS NOT NULL AND position = 1
                THEN 'linked_at_ingest' ELSE 'evidence_attached' END,
           'groovemap'
    FROM later
    UNION ALL
    SELECT kind_alias.native_id, kind_alias.valid_to, 'alias_closed', 'groovemap'
    FROM kind_alias
    LEFT JOIN moved_out ON moved_out.item_id = kind_alias.native_id AND moved_out.at = kind_alias.valid_to
    WHERE kind_alias.valid_to IS NOT NULL AND moved_out.item_id IS NULL
    UNION ALL
    SELECT holder.native_id, link.event_at, link.event,
           CASE link.dated_from WHEN 'musicbrainz_fullexport' THEN 'musicbrainz_fullexport' ELSE 'groovemap' END
    FROM identity_link_events AS link
    LEFT JOIN provider_aliases AS holder
           ON holder.provider = link.provider AND holder.entity_kind = link.entity_kind
          AND holder.external_id = link.external_id AND holder.valid_to IS NULL
    WHERE link.provider = 'musicbrainz' AND link.entity_kind = 'artist'
),
supersession_of_kind AS (
    SELECT supersession.*
    FROM catalog_item_supersessions AS supersession
    JOIN catalog_items AS superseded ON superseded.id = supersession.superseded_id AND superseded.kind = 'artist'
),
event AS (
    SELECT at, type, dated_from FROM item_event
    UNION ALL
    SELECT valid_from, CASE cause WHEN 'catalog_reattachment' THEN 'reattached' ELSE 'promoted' END, 'groovemap'
    FROM supersession_of_kind
    UNION ALL
    SELECT valid_to, 'reverted', 'groovemap' FROM supersession_of_kind WHERE valid_to IS NOT NULL
)
SELECT 'artist'::text AS kind, to_char(at AT TIME ZONE 'UTC', 'YYYY-MM') AS month, type, dated_from, count(*) AS events
FROM event
GROUP BY 1, 2, 3, 4
ORDER BY 2, 3, 4)
UNION ALL
(WITH 
kind_alias AS (
    SELECT alias.native_id, alias.provider, alias.external_id, alias.valid_from, alias.valid_to,
           min(alias.valid_from) OVER item AS mint,
           min(alias.valid_from) FILTER (WHERE alias.provider = 'discogs') OVER item AS discogs_at
    FROM provider_aliases AS alias
    JOIN catalog_items AS catalog_item ON catalog_item.id = alias.native_id AND catalog_item.kind = 'label'
    WINDOW item AS (PARTITION BY alias.native_id)
),
moved_in AS (
    SELECT survivor_id AS item_id, valid_from AS at FROM catalog_item_supersessions
    UNION
    SELECT superseded_id, valid_to FROM catalog_item_supersessions WHERE valid_to IS NOT NULL
),
moved_out AS (
    SELECT superseded_id AS item_id, valid_from AS at FROM catalog_item_supersessions
    UNION
    SELECT survivor_id, valid_to FROM catalog_item_supersessions WHERE valid_to IS NOT NULL
),
later AS (
    SELECT kind_alias.native_id, kind_alias.provider, kind_alias.valid_from, kind_alias.discogs_at,
           row_number() OVER (
               PARTITION BY kind_alias.native_id, kind_alias.provider = 'musicbrainz'
               ORDER BY kind_alias.valid_from, kind_alias.external_id
           ) AS position
    FROM kind_alias
    LEFT JOIN moved_in ON moved_in.item_id = kind_alias.native_id AND moved_in.at = kind_alias.valid_from
    WHERE kind_alias.valid_from > kind_alias.mint AND moved_in.item_id IS NULL
),
item_event AS (
    SELECT DISTINCT native_id AS item_id, mint AS at, 'minted' AS type, 'groovemap' AS dated_from
    FROM kind_alias
    UNION ALL
    SELECT native_id, valid_from,
           CASE WHEN provider = 'musicbrainz' AND discogs_at IS NOT NULL AND position = 1
                THEN 'linked_at_ingest' ELSE 'evidence_attached' END,
           'groovemap'
    FROM later
    UNION ALL
    SELECT kind_alias.native_id, kind_alias.valid_to, 'alias_closed', 'groovemap'
    FROM kind_alias
    LEFT JOIN moved_out ON moved_out.item_id = kind_alias.native_id AND moved_out.at = kind_alias.valid_to
    WHERE kind_alias.valid_to IS NOT NULL AND moved_out.item_id IS NULL
    UNION ALL
    SELECT holder.native_id, link.event_at, link.event,
           CASE link.dated_from WHEN 'musicbrainz_fullexport' THEN 'musicbrainz_fullexport' ELSE 'groovemap' END
    FROM identity_link_events AS link
    LEFT JOIN provider_aliases AS holder
           ON holder.provider = link.provider AND holder.entity_kind = link.entity_kind
          AND holder.external_id = link.external_id AND holder.valid_to IS NULL
    WHERE link.provider = 'musicbrainz' AND link.entity_kind = 'label'
),
supersession_of_kind AS (
    SELECT supersession.*
    FROM catalog_item_supersessions AS supersession
    JOIN catalog_items AS superseded ON superseded.id = supersession.superseded_id AND superseded.kind = 'label'
),
event AS (
    SELECT at, type, dated_from FROM item_event
    UNION ALL
    SELECT valid_from, CASE cause WHEN 'catalog_reattachment' THEN 'reattached' ELSE 'promoted' END, 'groovemap'
    FROM supersession_of_kind
    UNION ALL
    SELECT valid_to, 'reverted', 'groovemap' FROM supersession_of_kind WHERE valid_to IS NOT NULL
)
SELECT 'label'::text AS kind, to_char(at AT TIME ZONE 'UTC', 'YYYY-MM') AS month, type, dated_from, count(*) AS events
FROM event
GROUP BY 1, 2, 3, 4
ORDER BY 2, 3, 4);

-- identity_ledger_density key
CREATE UNIQUE INDEX ON identity_ledger_density (kind, month, type, dated_from);

