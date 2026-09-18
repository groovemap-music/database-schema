# Spike gm-database-schema-9c8.2: Cypher coverage and the target relational edge model

- Phase: 1 (feasibility spike) of the
  [Neo4j to PostgreSQL graph-migration program](https://github.com/groovemap-music/design/blob/main/docs/programs/neo4j-to-postgresql-graph.md)
- Decides: GO/NO-GO question 3 ("what is the target relational edge model, and which loader
  writes each relation?") and the coverage half of question 1
- Sibling spike: `gm-database-schema-9c8.3` prototypes the two variable-length workloads
- Deliverable: evidence and a decision. No product code changes. Every sibling repository was
  read only.

## Question

Two questions, both answerable from the code as it stands and neither needing a running
PostgreSQL 19:

1. **Does every Cypher query function in `catalog-api` have a PostgreSQL spelling?** For each
   one: which `GRAPH_TABLE` pattern replaces it, or which plain-SQL shape replaces it, or why it
   has neither. [ADR 0012](https://github.com/groovemap-music/design/blob/main/docs/adr/0012-postgresql-property-graph-migration.md)
   asserts that "all but two query functions are fixed one-hop or two-hop patterns that map onto
   that subset directly". That assertion is checked here against every function rather than
   assumed.
2. **What relations must exist underneath the property graph, and who writes them?** The phase 0
   `graph` schema declares `graph.catalog` over fifty-two views. Where a view is sufficient, the
   answer is "nothing to build". Where it is not, the answer is a table shape, its keys, its
   indexes, and an owning loader among `discogs-sql-loader`, `musicbrainz-sql-loader`, and
   `catalog-api`.

What this spike does **not** answer: whether `GRAPH_TABLE` over views is fast enough at
production edge counts. That is question 1's performance half and belongs to the sibling
measurement work. This document sizes the migration; it does not time it.

## Method

Four reads, all static, all against checked-out working trees:

1. **The read surface.** Every module-level `def` and `class` in `catalog-api`'s
   `api/queries/*.py` (twenty-five modules, 6,560 lines) and `api/syncer.py` (1,154 lines) was
   enumerated with `grep -n '^\(async \)\?def \|^class '` and then read in full. The enumeration
   is mechanical, so the table below is provably complete for those files: 233 rows, one per
   definition. Twelve further rows carry the Cypher the rarity pipeline runs as module-level
   constants rather than inside a function body — eleven in `rarity_queries.py` and one in
   `api/rarity/families/grooved.py`, which `rarity_queries.py` executes through its family
   registry. 245 rows in all.
2. **The write surface.** `discogs-graph-enricher` and `musicbrainz-graph-enricher` were read for
   the node keys, the full property list per label, the relationship types, and the source
   document field each edge is derived from. The edge tables below must reproduce those
   derivation rules exactly, including the two that are not obvious from the graph alone: the
   `PART_OF` single-genre guard and the MusicBrainz `direction: backward` swap.
3. **The relational surface.** `discogs-sql-loader` and `musicbrainz-sql-loader` were read for
   what they already write, what events they consume, and what batching and reconciliation
   machinery an edge writer could reuse.
4. **The phase 0 contract.** The `graph` schema in this repository
   (`src/groovemap_schema/postgres.py:1367-2500`) and its documentation (`docs/architecture.md`)
   are the naming and typing contract every rewrite is classified against. A rewrite is only
   "direct" if the property it reads is a column some phase 0 view already exposes.

### The rewrite classes

The closed set, as the bead defines it:

| Class | Meaning |
| --- | --- |
| **direct GRAPH_TABLE** | One `GRAPH_TABLE` pattern, projected straight out. No aggregation, no outer join, no post-processing. |
| **GRAPH_TABLE + SQL** | A `GRAPH_TABLE` pattern wrapped in SQL aggregation, a `LEFT JOIN`, an anti-join, or a window. This is where Cypher's `count(DISTINCT …)`, `collect()`, `OPTIONAL MATCH`, and `NOT (u)-[:X]->(r)` land. |
| **recursive CTE** | No SQL/PGQ spelling exists. A `WITH RECURSIVE` over an edge relation with a depth cap and cycle detection. |
| **SQL-only (no graph)** | Answerable without touching `graph.catalog` at all: a single-vertex lookup, a count over one relation, a read of a catalog table, or a full-text search. |
| **unsupported** | No PostgreSQL spelling of any kind; the surface itself has to change. |

Rows marked `—` in the class column are not query functions: pure helpers, dataclasses, exception
types, and scoring code that never reaches a store. Every function that reaches a store carries a
class from the closed set. That is what makes the 233-row enumeration honest rather than padded.

Two classification rules are worth stating because they move a lot of rows:

- **A single-vertex lookup is SQL-only.** `MATCH (a:Artist {id: $id}) RETURN a.id, a.name` is
  `SELECT artist_id, name FROM graph.artist WHERE artist_id = $1`. `GRAPH_TABLE` can express it,
  but a one-element pattern buys nothing over a plain select and costs a planner detour. Twelve
  functions move to SQL-only on this rule.
- **A collection- or wantlist-only pattern is SQL-only.** `graph.collected` and `graph.wants` are
  already inner joins of `user_collections`/`user_wantlists` onto `releases`. A query whose whole
  traversal is `(:User)-[:COLLECTED]->(:Release)` is a select over that view. It becomes
  `GRAPH_TABLE + SQL` only when it continues on to `BY`, `ON`, or `IS`.

## Evidence

### Summary of the read surface

| Rewrite class | Functions | Share of store-touching functions |
| --- | --- | --- |
| direct GRAPH_TABLE | 7 | 4% |
| GRAPH_TABLE + SQL aggregation or LEFT JOIN | 105 | 57% |
| recursive CTE | 2 | 1% |
| SQL-only (no graph) | 70 | 38% |
| unsupported | 0 | 0% |
| **Store-touching subtotal** | **184** | **100%** |
| — (not a query function) | 61 | — |
| **Total rows** | **245** | |

Two counts in that table are the headline. **`unsupported` is empty**: every function has a
PostgreSQL spelling. And **38% of the store-touching surface does not need the property graph at
all** — it is single-vertex lookups, catalog-table reads, full-text search, and counts over one
relation. That fraction can move before `graph.catalog` is trusted, which makes the early part of
phase 3 independent of the performance question.

### Table 1: every query function in `catalog-api`

Paths are relative to the `catalog-api` repository. "Labels" and "Relationship types" are the
Neo4j labels and types the function's Cypher names; `—` means the function issues no Cypher.

| File:line | Function | Neo4j labels | Relationship types | Rewrite class | Note |
| --- | --- | --- | --- | --- | --- |
| `api/queries/admin_queries.py:19` | `get_user_stats` | — | — | SQL-only (no graph) | `users`, `oauth_tokens`, `sync_history`. |
| `api/queries/admin_queries.py:91` | `get_sync_activity` | — | — | SQL-only (no graph) | `sync_history`. |
| `api/queries/admin_queries.py:124` | `get_neo4j_storage` | all | all | SQL-only (no graph) | **`apoc.meta.stats()` + `dbms.queryJmx`.** See "The administrative metadata call". |
| `api/queries/admin_queries.py:170` | `get_postgres_storage` | — | — | SQL-only (no graph) | `pg_stat_user_tables`. |
| `api/queries/admin_queries.py:229` | `get_audit_log` | — | — | SQL-only (no graph) | `admin_audit_log`. |
| `api/queries/admin_queries.py:278` | `get_redis_storage` | — | — | SQL-only (no graph) | Redis only; no database. |
| `api/queries/collaborator_queries.py:14` | `get_artist_identity` | `Artist` | — | SQL-only (no graph) | Single-vertex lookup on `graph.artist`. |
| `api/queries/collaborator_queries.py:23` | `get_collaborators` | `Release`, `Artist` | `BY` | GRAPH_TABLE + SQL | Two `by_artist` edges out of one release; `sum`/`min`/`max`/`collect` per year become `GROUP BY`. |
| `api/queries/collaborator_queries.py:44` | `count_collaborators` | `Release`, `Artist` | `BY` | GRAPH_TABLE + SQL | `count(DISTINCT other)`. |
| `api/queries/collection_media_queries.py:43` | `get_collection_media_summary` | — | — | SQL-only (no graph) | `user_collections.media`. |
| `api/queries/credits_queries.py:19` | `get_person_credits` | `Person`, `Release`, `Artist`, `Label` | `CREDITED_ON`, `BY`, `ON` | GRAPH_TABLE + SQL | Three patterns, `LEFT JOIN`ed and aggregated. Reads `c.category`; the view column is `role_category`. |
| `api/queries/credits_queries.py:40` | `get_person_timeline` | `Person`, `Release` | `CREDITED_ON` | GRAPH_TABLE + SQL | `GROUP BY year, role_category`. |
| `api/queries/credits_queries.py:56` | `get_release_credits` | `Person`, `Release`, `Artist` | `CREDITED_ON`, `SAME_AS` | GRAPH_TABLE + SQL | `OPTIONAL MATCH` on `same_as` becomes a `LEFT JOIN`. |
| `api/queries/credits_queries.py:74` | `get_role_leaderboard` | `Person`, `Release` | `CREDITED_ON` | GRAPH_TABLE + SQL | `count(DISTINCT r)` per person, ordered, limited. |
| `api/queries/credits_queries.py:91` | `get_shared_credits` | `Person`, `Release`, `Artist` | `CREDITED_ON`, `BY` | GRAPH_TABLE + SQL | Two `credited_on` edges into one release. |
| `api/queries/credits_queries.py:111` | `get_person_connections` | `Person`, `Release` | `CREDITED_ON` | GRAPH_TABLE + SQL | Two fixed variants (2-hop and 4-hop). `depth` selects a variant; it is not a quantifier. |
| `api/queries/credits_queries.py:156` | `autocomplete_person` | `Person` | — | SQL-only (no graph) | **`db.index.fulltext.queryNodes('person_name_fulltext')`.** See "Full-text search". |
| `api/queries/credits_queries.py:178` | `get_person_profile` | `Person`, `Release`, `Artist` | `CREDITED_ON`, `SAME_AS` | GRAPH_TABLE + SQL | Aggregate plus `LEFT JOIN`. |
| `api/queries/credits_queries.py:201` | `get_person_role_breakdown` | `Person`, `Release` | `CREDITED_ON` | GRAPH_TABLE + SQL | `GROUP BY role_category`. |
| `api/queries/fit_queries.py:69` | `collection_cache_key` | — | — | — | Pure. |
| `api/queries/fit_queries.py:139` | `empty_collection` | — | — | — | Pure. |
| `api/queries/fit_queries.py:164` | `held_title_key` | — | — | — | Pure. |
| `api/queries/fit_queries.py:174` | `fold_collection` | — | — | — | Pure. |
| `api/queries/fit_queries.py:250` | `get_collection_ids` | `User`, `Release`, `Artist`, `Label`, `Genre`, `Style`, `Master` | `COLLECTED`, `BY`, `ON`, `IS`, `DERIVED_FROM` | GRAPH_TABLE + SQL | `_COLLECTION_CYPHER` at line 82. Five `OPTIONAL MATCH`es collapse to five `LEFT JOIN`s and `array_agg(DISTINCT …)`. |
| `api/queries/fit_queries.py:285` | `get_release_context` | `Release`, `Artist`, `Label`, `Genre`, `Style`, `Medium`, `Master` | `BY`, `ON`, `IS`, `ISSUED_ON`, `DERIVED_FROM` | GRAPH_TABLE + SQL | `_RELEASE_CONTEXT_CYPHER` at line 102. The sibling hop `(r)-[:DERIVED_FROM]->(m)<-[:DERIVED_FROM]-(s)` is a fixed 2-hop pattern. |
| `api/queries/fit_queries.py:313` | `get_release_rarity` | — | — | SQL-only (no graph) | `insights.release_rarity`. |
| `api/queries/gap_queries.py:24` | `attach_gap_identity` | — | — | SQL-only (no graph) | `provider_aliases` via `api.identity`. |
| `api/queries/gap_queries.py:39` | `_build_filters` | `Medium` | `WANTS`, `ISSUED_ON` | GRAPH_TABLE + SQL | Builds the anti-join and the `EXISTS { … }` medium filter shared by the three gap queries. |
| `api/queries/gap_queries.py:62` | `get_label_gaps` | `User`, `Label`, `Release`, `Artist`, `Genre`, `Medium` | `ON`, `COLLECTED`, `WANTS`, `BY`, `IS`, `ISSUED_ON` | GRAPH_TABLE + SQL | `NOT (u)-[:COLLECTED]->(r)` becomes `NOT EXISTS`. Reads `r.formats` — **not a `graph.release` column**. |
| `api/queries/gap_queries.py:108` | `get_label_gap_summary` | `User`, `Label`, `Release` | `ON`, `COLLECTED` | GRAPH_TABLE + SQL | Two counts and a subtraction. |
| `api/queries/gap_queries.py:127` | `get_label_metadata` | `Label` | — | SQL-only (no graph) | Single-vertex lookup. |
| `api/queries/gap_queries.py:136` | `get_artist_gaps` | `User`, `Artist`, `Release`, `Label`, `Genre`, `Medium` | `BY`, `COLLECTED`, `WANTS`, `ON`, `IS`, `ISSUED_ON` | GRAPH_TABLE + SQL | Reads `r.formats`. |
| `api/queries/gap_queries.py:182` | `get_artist_gap_summary` | `User`, `Artist`, `Release` | `BY`, `COLLECTED` | GRAPH_TABLE + SQL | |
| `api/queries/gap_queries.py:201` | `get_artist_metadata` | `Artist` | — | SQL-only (no graph) | Single-vertex lookup. |
| `api/queries/gap_queries.py:210` | `get_master_gaps` | `User`, `Master`, `Release`, `Artist`, `Label`, `Genre`, `Medium` | `DERIVED_FROM`, `COLLECTED`, `WANTS`, `BY`, `ON`, `IS`, `ISSUED_ON` | GRAPH_TABLE + SQL | Reads `r.formats`. |
| `api/queries/gap_queries.py:259` | `get_master_gap_summary` | `User`, `Master`, `Release` | `DERIVED_FROM`, `COLLECTED` | GRAPH_TABLE + SQL | |
| `api/queries/gap_queries.py:278` | `get_master_metadata` | `Master` | — | SQL-only (no graph) | Single-vertex lookup. |
| `api/queries/genre_tree_queries.py:35` | `get_genre_tree` | `Release`, `Genre`, `Style` | `IS` | GRAPH_TABLE + SQL | Two `in_genre`/`in_style` patterns, nested aggregation. Whole-catalog scan; a counter relation serves it far better (see Table 3). |
| `api/queries/helpers.py:31` | `_build_query` | — | — | — | Driver plumbing. |
| `api/queries/helpers.py:44` | `_try_explain_on_error` | — | — | — | Driver plumbing. |
| `api/queries/helpers.py:69` | `run_query` | — | — | — | **The seam site.** Every Cypher call in the service passes through here. |
| `api/queries/helpers.py:113` | `run_single` | — | — | — | Seam site. |
| `api/queries/helpers.py:157` | `run_count` | — | — | — | Seam site. |
| `api/queries/insights_neo4j_queries.py:17` | `query_artist_centrality` | `Artist` | **unbound `-[]-`** | GRAPH_TABLE + SQL | `size([(a)-[]-() \| 1])`. See "Unbound relationship patterns". |
| `api/queries/insights_neo4j_queries.py:35` | `query_genre_trends` | `Release`, `Genre` | `IS` | GRAPH_TABLE + SQL | `GROUP BY genre, decade`. Whole-catalog when `genre` is null. |
| `api/queries/insights_neo4j_queries.py:63` | `query_label_longevity` | `Release`, `Label` | `ON` | GRAPH_TABLE + SQL | `min`/`max`/`count` plus a modal decade; the modal decade is a window function. |
| `api/queries/insights_neo4j_queries.py:96` | `query_monthly_anniversaries` | `Master`, `Artist` | `BY` | GRAPH_TABLE + SQL | `master_by_artist` plus `LEFT JOIN`. `Master.year` is `graph.master.year`. |
| `api/queries/insights_pg_queries.py:49` | `_query_single_entity` | — | — | SQL-only (no graph) | `count(*) FILTER` over the entity tables. |
| `api/queries/insights_pg_queries.py:78` | `query_data_completeness` | — | — | SQL-only (no graph) | |
| `api/queries/label_dna_queries.py:21` | `get_label_identity` | `Label`, `Release`, `Artist` | `ON`, `BY` | GRAPH_TABLE + SQL | Two counts. A `graph.label_stats` row answers it without a traversal. |
| `api/queries/label_dna_queries.py:37` | `get_label_genre_profile` | `Release`, `Label`, `Genre` | `ON`, `IS` | GRAPH_TABLE + SQL | |
| `api/queries/label_dna_queries.py:48` | `get_label_style_profile` | `Release`, `Label`, `Style` | `ON`, `IS` | GRAPH_TABLE + SQL | |
| `api/queries/label_dna_queries.py:59` | `get_label_decade_profile` | `Release`, `Label` | `ON` | GRAPH_TABLE + SQL | |
| `api/queries/label_dna_queries.py:71` | `get_label_active_years` | `Release`, `Label` | `ON` | GRAPH_TABLE + SQL | `SELECT DISTINCT year`. |
| `api/queries/label_dna_queries.py:83` | `get_label_format_profile` | `Release`, `Label` | `ON` | GRAPH_TABLE + SQL | `UNWIND r.formats` — **`formats` is not a `graph.release` column**. |
| `api/queries/label_dna_queries.py:96` | `get_label_media_family_counts` | `Label`, `Release`, `Medium`, `MediaFamily` | `ON`, `ISSUED_ON`, `IN_FAMILY` | GRAPH_TABLE + SQL | Fixed 3-hop. `source` is part of the `issued_on` key, so the `DISTINCT r, f` dedup is exactly what SQL needs. |
| `api/queries/label_dna_queries.py:113` | `get_label_medium_counts` | `Label`, `Release`, `Medium`, `MediaFamily` | `ON`, `ISSUED_ON`, `IN_FAMILY` | GRAPH_TABLE + SQL | `m.id` → `graph.medium.medium_id`; `m.label` → `graph.medium.label`. |
| `api/queries/label_dna_queries.py:129` | `get_label_media_families_fallback` | `Release`, `Label` | `ON` | GRAPH_TABLE + SQL | `UNWIND r.media_families` → `unnest(graph.release.media_families)`. Column exists. |
| `api/queries/label_dna_queries.py:147` | `get_label_media_profile` | — | — | — | Composition of the two above. |
| `api/queries/label_dna_queries.py:171` | `get_label_full_profile` | — | — | — | Composition. |
| `api/queries/label_dna_queries.py:216` | `get_candidate_labels_genre_vectors` | `Label`, `Release`, `Style`, `Genre` | `ON`, `IS` | GRAPH_TABLE + SQL | Fixed 3-hop style overlap. The `CALL {}` per-style barrier is a planner hint, not semantics. |
| `api/queries/label_dna_queries.py:313` | `compute_similar_labels` | — | — | — | Pure cosine scoring. |
| `api/queries/lookup_queries.py:69` | `_year` | — | — | — | Pure. |
| `api/queries/lookup_queries.py:86` | `resolve_alias_native_id` | — | — | SQL-only (no graph) | `provider_aliases`. |
| `api/queries/lookup_queries.py:110` | `releases_for_native_id` | — | — | SQL-only (no graph) | |
| `api/queries/media_coverage_queries.py:45` | `UnknownProviderError` | — | — | — | Exception type. |
| `api/queries/media_coverage_queries.py:53` | `known_providers` | — | — | — | Pure. |
| `api/queries/media_coverage_queries.py:58` | `_release_table` | — | — | — | Pure. |
| `api/queries/media_coverage_queries.py:75` | `_unmapped_array` | — | — | — | Pure SQL fragment. |
| `api/queries/media_coverage_queries.py:87` | `_coverage_query` | — | — | SQL-only (no graph) | |
| `api/queries/media_coverage_queries.py:99` | `_top_names_query` | — | — | SQL-only (no graph) | |
| `api/queries/media_coverage_queries.py:119` | `get_unmapped_media` | — | — | SQL-only (no graph) | |
| `api/queries/media_filters.py:16` | `UnknownMediaIdsError` | — | — | — | Exception type. |
| `api/queries/media_filters.py:24` | `_known_media_ids` | — | — | — | Pure. |
| `api/queries/media_filters.py:28` | `validate_media_ids` | — | — | — | Pure. |
| `api/queries/media_filters.py:49` | `media_ids_from_formats` | — | — | — | Pure. |
| `api/queries/media_filters.py:68` | `split_media_ids` | — | — | — | Pure. |
| `api/queries/media_filters.py:79` | `resolve_media_filter` | — | — | — | Pure. |
| `api/queries/metrics_queries.py:35` | `_bucket_to_trunc_unit` | — | — | — | Pure. |
| `api/queries/metrics_queries.py:44` | `_round_or_int` | — | — | — | Pure. |
| `api/queries/metrics_queries.py:53` | `_round_rate` | — | — | — | Pure. |
| `api/queries/metrics_queries.py:65` | `get_queue_history` | — | — | SQL-only (no graph) | |
| `api/queries/metrics_queries.py:140` | `get_health_history` | — | — | SQL-only (no graph) | |
| `api/queries/musicbrainz_queries.py:12` | `get_artist_musicbrainz` | `Artist` | — | SQL-only (no graph) | Reads `a.mbid`, `a.mb_type`, `a.mb_gender`, `a.mb_begin_date`, `a.mb_end_date`, `a.mb_area`, `a.mb_begin_area`, `a.mb_disambiguation` — **none is a `graph.artist` column**. All are columns of `musicbrainz.artists`, joined on `discogs_artist_id`. |
| `api/queries/musicbrainz_queries.py:39` | `get_artist_mb_relationships` | `Artist` | **unbound `-[r]->` filtered on `r.source`** | GRAPH_TABLE + SQL | See "Unbound relationship patterns" and "The MusicBrainz relationship-type vocabulary". |
| `api/queries/musicbrainz_queries.py:58` | `get_artist_external_links` | — | — | SQL-only (no graph) | `musicbrainz.external_links`. |
| `api/queries/musicbrainz_queries.py:74` | `get_enrichment_status` | `Artist`, `Label`, `Release`, all | **unbound `MATCH ()-[r]->()`** | SQL-only (no graph) | Both endpoints and the relationship are unbound. Becomes `count(*)` over `musicbrainz.*` tables. |
| `api/queries/neo4j_queries.py:33` | `_escape_lucene_query` | — | — | — | Lucene metacharacter escaping; disappears with Lucene. |
| `api/queries/neo4j_queries.py:38` | `_build_autocomplete_query` | — | — | — | Builds the Lucene prefix query. |
| `api/queries/neo4j_queries.py:45` | `_AutocompleteSpec` | — | — | — | Dataclass. |
| `api/queries/neo4j_queries.py:50` | `_autocomplete` | `Artist`, `Label`, `Genre`, `Style` | — | SQL-only (no graph) | **`db.index.fulltext.queryNodes`.** |
| `api/queries/neo4j_queries.py:97` | `find_shortest_path` | `Artist`, `Label`, `Genre`, `Style`, `Master`, `Release`, **and label-free `(a {id: …})`** | `BY`, `ON`, `IS`, `ALIAS_OF`, `MEMBER_OF`, `DERIVED_FROM` (`*..10`) | **recursive CTE** | `shortestPath`. Depth clamped `[1, 10]`, default 6, 120 s timeout. Surfaces as `GET /api/path`, NLQ `find_path`, MCP `find_path`. Prototyped by spike 9c8.3. |
| `api/queries/neo4j_queries.py:150` | `autocomplete_artist` | `Artist` | — | SQL-only (no graph) | Full-text. |
| `api/queries/neo4j_queries.py:155` | `autocomplete_label` | `Label` | — | SQL-only (no graph) | Full-text. |
| `api/queries/neo4j_queries.py:160` | `autocomplete_genre` | `Genre` | — | SQL-only (no graph) | Full-text. |
| `api/queries/neo4j_queries.py:165` | `autocomplete_style` | `Style` | — | SQL-only (no graph) | Full-text. |
| `api/queries/neo4j_queries.py:173` | `explore_artist` | `Artist`, `Release`, `Label` | `ALIAS_OF`, `MEMBER_OF`, `BY`, `ON` | GRAPH_TABLE + SQL | `COUNT { MATCH … }` subqueries become scalar subqueries; the alias/group/member dedup becomes `UNION` then `count(DISTINCT)`. |
| `api/queries/neo4j_queries.py:203` | `explore_genre` | `Genre` | — | SQL-only (no graph) | Reads `g.release_count`, `g.artist_count`, `g.label_count`, `g.style_count` — **pre-computed by `graphinator`, no view carries them**. Needs `graph.genre_stats`. |
| `api/queries/neo4j_queries.py:232` | `explore_label` | `Label`, `Release`, `Artist`, `Genre` | `ON`, `BY`, `IS` | GRAPH_TABLE + SQL | Reads `l.release_count`/`l.artist_count`/`l.genre_count` first and falls back to a live traversal. Needs `graph.label_stats` for the fast path. |
| `api/queries/neo4j_queries.py:279` | `explore_style` | `Style` | — | SQL-only (no graph) | Reads `s.release_count`, `s.artist_count`, `s.label_count`, `s.genre_count`. Needs `graph.style_stats`. |
| `api/queries/neo4j_queries.py:321` | `_year_filter` | — | — | — | Pure fragment builder. |
| `api/queries/neo4j_queries.py:328` | `_query_params` | — | — | — | Pure. |
| `api/queries/neo4j_queries.py:346` | `_AggregateExpandSpec` | — | — | — | Dataclass. |
| `api/queries/neo4j_queries.py:351` | `_expand_aggregate` | `Release`, `Artist`, `Label`, `Genre`, `Style` | `BY`, `ON`, `IS` | GRAPH_TABLE + SQL | The engine behind nine `expand_*` functions. Two-hop pattern, `count(DISTINCT r)`, `ORDER BY … , id`, `OFFSET`/`LIMIT`. |
| `api/queries/neo4j_queries.py:408` | `_expand_releases` | `Release`, `Artist`, `Label`, `Genre`, `Style` | `BY`, `ON`, `IS` | direct GRAPH_TABLE | One edge, projected with `ORDER BY`/`OFFSET`/`LIMIT`. No aggregation. |
| `api/queries/neo4j_queries.py:423` | `expand_artist_releases` | `Release`, `Artist` | `BY` | direct GRAPH_TABLE | |
| `api/queries/neo4j_queries.py:430` | `expand_artist_labels` | `Release`, `Artist`, `Label` | `BY`, `ON` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:437` | `expand_artist_aliases` | `Artist` | `ALIAS_OF`, `MEMBER_OF` | GRAPH_TABLE + SQL | Three patterns (`alias_of` out, `member_of` out, `member_of` in) `UNION`ed and deduped. |
| `api/queries/neo4j_queries.py:468` | `expand_genre_releases` | `Release`, `Genre` | `IS` | direct GRAPH_TABLE | |
| `api/queries/neo4j_queries.py:475` | `expand_genre_artists` | `Release`, `Genre`, `Artist` | `IS`, `BY` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:482` | `expand_genre_labels` | `Release`, `Genre`, `Label` | `IS`, `ON` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:489` | `expand_genre_styles` | `Release`, `Genre`, `Style` | `IS` | GRAPH_TABLE + SQL | Both `IS` edges; in PGQ they are two labels, `in_genre` and `in_style`. |
| `api/queries/neo4j_queries.py:496` | `expand_label_releases` | `Release`, `Label` | `ON` | direct GRAPH_TABLE | |
| `api/queries/neo4j_queries.py:503` | `expand_label_artists` | `Release`, `Label`, `Artist` | `ON`, `BY` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:510` | `expand_label_genres` | `Release`, `Label`, `Genre` | `ON`, `IS` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:517` | `expand_style_releases` | `Release`, `Style` | `IS` | direct GRAPH_TABLE | |
| `api/queries/neo4j_queries.py:524` | `expand_style_artists` | `Release`, `Style`, `Artist` | `IS`, `BY` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:531` | `expand_style_labels` | `Release`, `Style`, `Label` | `IS`, `ON` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:538` | `expand_style_genres` | `Release`, `Style`, `Genre` | `IS` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:549` | `_CountSpec` | — | — | — | Dataclass. |
| `api/queries/neo4j_queries.py:555` | `_count` | `Release`, `Artist`, `Label`, `Genre`, `Style` | `BY`, `ON`, `IS` | GRAPH_TABLE + SQL | The engine behind twelve `count_*` functions. |
| `api/queries/neo4j_queries.py:588` | `count_artist_releases` | `Release`, `Artist` | `BY` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:593` | `count_artist_labels` | `Release`, `Artist`, `Label` | `BY`, `ON` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:598` | `count_artist_aliases` | `Artist` | `ALIAS_OF`, `MEMBER_OF` | GRAPH_TABLE + SQL | Must produce exactly the row count `expand_artist_aliases` enumerates. |
| `api/queries/neo4j_queries.py:622` | `count_genre_releases` | `Release`, `Genre` | `IS` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:627` | `count_genre_artists` | `Release`, `Genre`, `Artist` | `IS`, `BY` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:632` | `count_genre_labels` | `Release`, `Genre`, `Label` | `IS`, `ON` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:637` | `count_genre_styles` | `Release`, `Genre`, `Style` | `IS` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:642` | `count_label_releases` | `Release`, `Label` | `ON` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:647` | `count_label_artists` | `Release`, `Label`, `Artist` | `ON`, `BY` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:652` | `count_label_genres` | `Release`, `Label`, `Genre` | `ON`, `IS` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:657` | `count_style_releases` | `Release`, `Style` | `IS` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:662` | `count_style_artists` | `Release`, `Style`, `Artist` | `IS`, `BY` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:667` | `count_style_labels` | `Release`, `Style`, `Label` | `IS`, `ON` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:672` | `count_style_genres` | `Release`, `Style`, `Genre` | `IS` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:680` | `get_artist_details` | `Artist`, `Release`, `Genre`, `Style` | `BY`, `IS`, `MEMBER_OF` | GRAPH_TABLE + SQL | Four `LEFT JOIN`s plus `array_agg(DISTINCT …)`. |
| `api/queries/neo4j_queries.py:697` | `get_release_details` | `Release`, `Artist`, `Label`, `Genre`, `Style` | `BY`, `ON`, `IS` | GRAPH_TABLE + SQL | Returns `r.formats` — **not a `graph.release` column**. |
| `api/queries/neo4j_queries.py:722` | `get_label_details` | `Label`, `Release` | `ON` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:733` | `get_genre_details` | `Genre`, `Release`, `Artist` | `IS`, `BY` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:744` | `get_style_details` | `Style`, `Release`, `Artist` | `IS`, `BY` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:758` | `trends_artist` | `Release`, `Artist` | `BY` | GRAPH_TABLE + SQL | `GROUP BY year`. |
| `api/queries/neo4j_queries.py:770` | `trends_genre` | `Genre`, `Release` | `IS` | GRAPH_TABLE + SQL | The `CALL {}` barrier is a Neo4j planner workaround with no SQL counterpart. |
| `api/queries/neo4j_queries.py:794` | `trends_label` | `Release`, `Label` | `ON` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:806` | `trends_style` | `Style`, `Release` | `IS` | GRAPH_TABLE + SQL | |
| `api/queries/neo4j_queries.py:830` | `get_year_range` | `Release` | — | SQL-only (no graph) | `min`/`max` over `graph.release.year`; an index on `releases` serves it. |
| `api/queries/neo4j_queries.py:855` | `get_genre_emergence` | `Genre`, `Style` | — | SQL-only (no graph) | Reads `g.first_year`/`s.first_year` — **pre-computed by `graphinator`, no view carries them**. Needs `graph.genre_stats`/`graph.style_stats`. |
| `api/queries/neo4j_queries.py:895` | `node_label_to_type` | — | — | — | Pure label-string normalization. |
| `api/queries/neo4j_queries.py:984` | `get_graph_stats` | `Artist`, `Label`, `Release`, `Master`, `Genre`, `Style` | — | SQL-only (no graph) | Six `count(*)`s. |
| `api/queries/network_queries.py:18` | `get_artist_identity` | `Artist` | — | SQL-only (no graph) | Single-vertex lookup. |
| `api/queries/network_queries.py:27` | `get_multi_hop_collaborators` | `Artist`, `Release` | `BY` | GRAPH_TABLE + SQL | **The pilot family.** Fixed 1-hop `UNION` fixed 2-hop, plus a `NOT EXISTS` anti-join. `$depth` selects a branch; it is not a quantifier. |
| `api/queries/network_queries.py:68` | `count_multi_hop_collaborators` | `Artist`, `Release` | `BY` | GRAPH_TABLE + SQL | Must be ported with its sibling or pagination totals diverge. |
| `api/queries/network_queries.py:96` | `get_artist_centrality` | `Artist`, `Release` | **unbound `-[]-`**, `BY`, `MEMBER_OF`, `ALIAS_OF` | GRAPH_TABLE + SQL | `size([(a)-[]-() \| 1])`. See "Unbound relationship patterns". |
| `api/queries/network_queries.py:131` | `get_artist_cluster` | `Artist`, `Release`, `Genre` | `BY`, `IS` | GRAPH_TABLE + SQL | Fixed 2-hop plus a modal genre; `collect(genre)[0]` after `ORDER BY` is `mode() WITHIN GROUP`. |
| `api/queries/rarity_queries.py:122` (`_RELEASE_ID_PAGE_QUERY`) | (constant, run by `_fetch_release_id_page`) | `Release` | — | SQL-only (no graph) | Keyset page over `graph.release.release_id`. |
| `api/queries/rarity_queries.py:131` (`_RELEASE_COUNT_QUERY`) | (constant, run by `_warn_on_incomplete_coverage`) | `Release` | — | SQL-only (no graph) | `count(*)`. |
| `api/queries/rarity_queries.py:141` (`_RELEASE_QUERY`) | (constant, run by `_fetch_page_signals`) | `Release`, `Artist` | `BY` | GRAPH_TABLE + SQL | |
| `api/queries/rarity_queries.py:157` (`_MEDIA_QUERY`) | (constant) | `Release`, `Medium` | `ISSUED_ON` | GRAPH_TABLE + SQL | Returns `r.formats` — **not a `graph.release` column**. |
| `api/queries/rarity_queries.py:168` (`_LABEL_QUERY`) | (constant) | `Release`, `Label` | `ON` | GRAPH_TABLE + SQL | Reads `l.release_count` — **pre-computed**. Needs `graph.label_stats`. |
| `api/queries/rarity_queries.py:176` (`_TEMPORAL_QUERY`) | (constant) | `Release`, `Master` | `DERIVED_FROM` | GRAPH_TABLE + SQL | Fixed 2-hop sibling pattern. |
| `api/queries/rarity_queries.py:191` (`_DEGREE_QUERY`) | (constant) | `Release` | **unbound `COUNT { (r)--() }`** | GRAPH_TABLE + SQL | See "Unbound relationship patterns". |
| `api/queries/rarity_queries.py:200` (`_ARTIST_DEGREE_QUERY`) | (constant) | `Release`, `Artist` | `BY`, **unbound `COUNT { (a)--() }`** | GRAPH_TABLE + SQL | |
| `api/queries/rarity_queries.py:208` (`_LABEL_SIZE_QUERY`) | (constant) | `Release`, `Label` | `ON` | GRAPH_TABLE + SQL | Reads `l.release_count` — pre-computed. |
| `api/queries/rarity_queries.py:216` (`_GENRE_COUNT_QUERY`) | (constant) | `Release`, `Genre` | `IS` | GRAPH_TABLE + SQL | Reads `g.release_count` — pre-computed. |
| `api/rarity/families/grooved.py:48` (`PRESSING_QUERY`) | (constant, run through `family_queries()`) | `Release`, `Master` | `DERIVED_FROM` | GRAPH_TABLE + SQL | The `m IS NULL` branch distinguishes "no master" from "unique pressing"; a `LEFT JOIN` plus `CASE` reproduces it. |
| `api/queries/rarity_queries.py:238` | `_percentile_rank` | — | — | — | Pure. |
| `api/queries/rarity_queries.py:245` | `_fetch_release_id_page` | `Release` | — | SQL-only (no graph) | Runs `_RELEASE_ID_PAGE_QUERY`. |
| `api/queries/rarity_queries.py:258` | `_load_community_counts` | — | — | SQL-only (no graph) | `insights.community_counts`. |
| `api/queries/rarity_queries.py:274` | `_fetch_page_signals` | (see constants above) | (see constants above) | GRAPH_TABLE + SQL | Runs the eight core queries plus every family extension's, sequentially. |
| `api/queries/rarity_queries.py:303` | `_index_by_release` | — | — | — | Pure. |
| `api/queries/rarity_queries.py:308` | `fetch_all_rarity_signals` | (see constants above) | (see constants above) | GRAPH_TABLE + SQL | The paging driver. `RARITY_PAGE_SIZE = 20_000`, `RARITY_QUERY_TIMEOUT_SECONDS = 120.0`. |
| `api/queries/rarity_queries.py:474` | `_warn_on_incomplete_coverage` | `Release` | — | SQL-only (no graph) | |
| `api/queries/rarity_queries.py:501` | `get_rarity_for_release` | — | — | SQL-only (no graph) | `insights.release_rarity`. |
| `api/queries/rarity_queries.py:520` | `get_rarity_leaderboard` | — | — | SQL-only (no graph) | |
| `api/queries/rarity_queries.py:569` | `get_rarity_hidden_gems` | — | — | SQL-only (no graph) | |
| `api/queries/rarity_queries.py:601` | `get_rarity_by_artist` | `Artist`, `Release` | `BY` | direct GRAPH_TABLE | Two Cypher round trips then a PostgreSQL `= ANY`. In PostgreSQL the whole thing is one query: `GRAPH_TABLE` joined to `insights.release_rarity`. |
| `api/queries/rarity_queries.py:659` | `get_rarity_by_label` | `Label`, `Release` | `ON` | direct GRAPH_TABLE | Same collapse. |
| `api/queries/recommend_queries.py:38` | `get_artist_identity` | `Artist`, `Release` | `BY` | GRAPH_TABLE + SQL | Unlike the other two `get_artist_identity`s, this one counts releases. |
| `api/queries/recommend_queries.py:49` | `get_artist_profile` | `Release`, `Artist`, `Genre`, `Style`, `Label` | `BY`, `IS`, `ON` | GRAPH_TABLE + SQL | Four parallel two-hop aggregates. |
| `api/queries/recommend_queries.py:86` | `_batch_artist_profiles` | `Release`, `Artist`, `Genre`, `Style`, `Label` | `BY`, `IS`, `ON` | GRAPH_TABLE + SQL | `UNWIND $ids` becomes `WHERE artist_id = ANY($1)`. |
| `api/queries/recommend_queries.py:147` | `get_candidate_artists` | `Artist`, `Release`, `Genre` | `BY`, `IS` | GRAPH_TABLE + SQL | Fixed 3-hop with per-genre caps. The `LIMIT 100000` inner sample is a Neo4j cost control; SQL needs an equivalent or the mega-genre blowup returns. |
| `api/queries/recommend_queries.py:212` | `compute_similar_artists` | — | — | — | Pure. |
| `api/queries/recommend_queries.py:272` | `_normalize_scores` | — | — | — | Pure. |
| `api/queries/recommend_queries.py:282` | `merge_recommendation_candidates` | — | — | — | Pure. |
| `api/queries/recommend_queries.py:334` | `get_collector_counts` | `Release`, `User` | `COLLECTED` | GRAPH_TABLE + SQL | Or plain SQL over `user_collections`. |
| `api/queries/recommend_queries.py:348` | `get_label_affinity_candidates` | `User`, `Release`, `Label`, `Artist`, `Genre` | `COLLECTED`, `ON`, `WANTS`, `BY`, `IS` | GRAPH_TABLE + SQL | Two anti-joins. |
| `api/queries/recommend_queries.py:373` | `get_blindspot_candidates` | `User`, `Release`, `Artist`, `Genre` | `COLLECTED`, `BY`, `IS` | GRAPH_TABLE + SQL | `collect(DISTINCT other)[0..5]` is a windowed `row_number() <= 5`. |
| `api/queries/recommend_queries.py:403` | `get_explore_traversal` | `Genre`, `Style`, `Artist`, `Label`, `Release`, `Master` | `BY`, `ON`, `IS`, `ALIAS_OF`, `MEMBER_OF`, `DERIVED_FROM` (`*1..n`, n ≤ 3) | **recursive CTE** | Variable-length. Returns the shortest path per discovered node. Prototyped by spike 9c8.3. |
| `api/queries/recommend_queries.py:450` | `score_discoveries` | — | — | — | Pure. |
| `api/queries/release_media_queries.py:28` | `get_release_media` | — | — | SQL-only (no graph) | `releases.media`. |
| `api/queries/release_media_queries.py:50` | `_items` | — | — | — | Pure. |
| `api/queries/release_media_queries.py:65` | `get_release_catalog_blocks` | — | — | SQL-only (no graph) | |
| `api/queries/search_queries.py:58` | `_valid_media_ids` | — | — | — | Pure. |
| `api/queries/search_queries.py:63` | `split_media_filter` | — | — | — | Pure. |
| `api/queries/search_queries.py:84` | `cache_key` | — | — | — | Pure. |
| `api/queries/search_queries.py:111` | `_year_filter_clause` | — | — | — | Pure SQL fragment. |
| `api/queries/search_queries.py:133` | `_genre_filter_clause` | — | — | — | Pure SQL fragment. |
| `api/queries/search_queries.py:151` | `_media_filter_clause` | — | — | — | Pure SQL fragment. |
| `api/queries/search_queries.py:185` | `_country_filter_clause` | — | — | — | Pure SQL fragment. |
| `api/queries/search_queries.py:210` | `_entity_select` | — | — | SQL-only (no graph) | |
| `api/queries/search_queries.py:285` | `_build_union` | — | — | SQL-only (no graph) | |
| `api/queries/search_queries.py:335` | `_run_results` | — | — | SQL-only (no graph) | |
| `api/queries/search_queries.py:392` | `_run_total` | — | — | SQL-only (no graph) | |
| `api/queries/search_queries.py:433` | `_run_type_counts` | — | — | SQL-only (no graph) | |
| `api/queries/search_queries.py:463` | `_run_genre_facets` | — | — | SQL-only (no graph) | |
| `api/queries/search_queries.py:487` | `_run_decade_facets` | — | — | SQL-only (no graph) | |
| `api/queries/search_queries.py:516` | `_run_media_facets` | — | — | SQL-only (no graph) | |
| `api/queries/search_queries.py:542` | `_format_result` | — | — | — | Pure. |
| `api/queries/search_queries.py:574` | `execute_search` | — | — | SQL-only (no graph) | Already PostgreSQL. The whole module is the proof that the full-text half of the product does not need Neo4j. |
| `api/queries/similarity.py:7` | `to_genre_vector` | — | — | — | Pure. |
| `api/queries/similarity.py:15` | `cosine_similarity` | — | — | — | Pure. |
| `api/queries/taste_queries.py:15` | `get_collection_count` | `User`, `Release` | `COLLECTED` | SQL-only (no graph) | `count(*)` over `graph.collected`. |
| `api/queries/taste_queries.py:24` | `get_taste_heatmap` | `User`, `Release`, `Genre` | `COLLECTED`, `IS` | GRAPH_TABLE + SQL | `GROUP BY genre, decade`. |
| `api/queries/taste_queries.py:50` | `get_obscurity_score` | `User`, `Release` | `COLLECTED` | GRAPH_TABLE + SQL | A self-join of `graph.collected` on `release_id` with `other.user_id <> u.user_id`. |
| `api/queries/taste_queries.py:93` | `get_taste_drift` | `User`, `Release`, `Genre` | `COLLECTED`, `IS` | GRAPH_TABLE + SQL | `substring(c.date_added, 0, 4)`; `graph.collected.date_added` is a timestamp, so the rewrite uses `date_trunc`. Parity must check the off-by-one in Cypher's 0-based `substring`. |
| `api/queries/taste_queries.py:113` | `get_blind_spots` | `User`, `Release`, `Artist`, `Genre` | `COLLECTED`, `BY`, `IS` | GRAPH_TABLE + SQL | Anti-join plus a zero-count guard. |
| `api/queries/taste_queries.py:142` | `get_top_labels` | `User`, `Release`, `Label` | `COLLECTED`, `ON` | GRAPH_TABLE + SQL | |
| `api/queries/user_queries.py:23` | `attach_release_identity` | — | — | SQL-only (no graph) | `provider_aliases` + `owned_copies`. |
| `api/queries/user_queries.py:55` | `get_user_collection` | `User`, `Release`, `Artist`, `Label`, `Genre`, `Style` | `COLLECTED`, `BY`, `ON`, `IS` | GRAPH_TABLE + SQL | Reads `r.catalog_number` — **not a `graph.release` column**; written into Neo4j by the syncer (see below). |
| `api/queries/user_queries.py:96` | `get_user_wantlist` | `User`, `Release`, `Artist`, `Label`, `Genre`, `Style` | `WANTS`, `BY`, `ON`, `IS` | GRAPH_TABLE + SQL | Reads `r.catalog_number`. |
| `api/queries/user_queries.py:136` | `get_user_recommendations` | `User`, `Release`, `Artist`, `Label`, `Genre` | `COLLECTED`, `BY`, `WANTS`, `ON`, `IS` | GRAPH_TABLE + SQL | Two anti-joins. |
| `api/queries/user_queries.py:171` | `get_user_collection_stats` | `User`, `Release`, `Genre`, `Label`, `Artist` | `COLLECTED`, `IS`, `ON`, `BY` | GRAPH_TABLE + SQL | Seven parallel aggregates. |
| `api/queries/user_queries.py:241` | `get_user_collection_timeline` | `User`, `Release`, `Genre`, `Style`, `Label` | `COLLECTED`, `IS`, `ON` | GRAPH_TABLE + SQL | `reduce(acc = [], …)` is `array_agg` over the unnested lists. |
| `api/queries/user_queries.py:352` | `get_user_collection_evolution` | `User`, `Release`, `Genre`/`Style`/`Label` | `COLLECTED`, `IS`/`ON` | GRAPH_TABLE + SQL | The label and relationship type are chosen from a closed set at call time; in PGQ that is a choice of edge label, still not interpolation. |
| `api/queries/user_queries.py:405` | `check_releases_user_status` | `User`, `Release` | `COLLECTED`, `WANTS` | SQL-only (no graph) | Two `EXISTS` over `user_collections`/`user_wantlists`. |
| `api/syncer.py:46` | `DiscogsSyncError` | — | — | — | Exception type. |
| `api/syncer.py:72` | `_discard_event` | — | — | — | Telemetry. |
| `api/syncer.py:79` | `configure` | — | — | — | Module wiring. |
| `api/syncer.py:217` | `_content_hash` | — | — | — | Pure. |
| `api/syncer.py:228` | `_as_text` | — | — | — | Pure. |
| `api/syncer.py:233` | `_emit_events` | — | — | SQL-only (no graph) | Activity events. |
| `api/syncer.py:246` | `_alias_refs` | — | — | — | Pure. |
| `api/syncer.py:251` | `_native_ids_by_release` | — | — | — | Pure. |
| `api/syncer.py:256` | `_ensure_owned_copies` | — | — | SQL-only (no graph) | `owned_copies`. |
| `api/syncer.py:296` | `_collection_page_events` | — | — | — | Pure. |
| `api/syncer.py:329` | `_persist_collection_page` | — | — | SQL-only (no graph) | `user_collections` upsert. |
| `api/syncer.py:372` | `_persist_wantlist_page` | — | — | SQL-only (no graph) | `user_wantlists` upsert. |
| `api/syncer.py:407` | `_write_collection_snapshot` | — | — | SQL-only (no graph) | |
| `api/syncer.py:430` | `_auth_header` | — | — | — | Pure. |
| `api/syncer.py:462` | `sync_collection` | `User`, `Release` | `COLLECTED` (**write**) | SQL-only (no graph) | Cypher at line 609: `MERGE (u:User)`, `MERGE (u)-[c:COLLECTED {instance_id}]->(r)`, and `SET r += rel.metadata`. Every fact is already in the `user_collections` row written twenty lines earlier. **Except `catalog_number`** — see below. |
| `api/syncer.py:689` | `_reconcile_stale_collection` | `User` | `COLLECTED` (**delete**) | SQL-only (no graph) | Cypher at line 717 deletes edges whose `synced_at` predates the run; the SQL `DELETE` at line 710 already removed the rows. |
| `api/syncer.py:740` | `sync_wantlist` | `User`, `Release` | `WANTS` (**write**) | SQL-only (no graph) | Cypher at line 858. Same shape, same `catalog_number` side effect. |
| `api/syncer.py:930` | `_reconcile_stale_wantlist` | `User` | `WANTS` (**delete**) | SQL-only (no graph) | Cypher at line 953. |
| `api/syncer.py:965` | `run_full_sync` | — | — | — | Timeout wrapper. |
| `api/syncer.py:991` | `_run_full_sync` | — | — | SQL-only (no graph) | Orchestration plus `sync_history`. |
| `api/syncer.py:1131` | `reconcile_stale_sync_history` | — | — | SQL-only (no graph) | |

### Unbound relationship patterns

Five sites traverse without naming a relationship type. Every one has a replacement; none is
`unsupported`, because in PostgreSQL the edge relations are addressable individually and a
`UNION ALL` over them is an ordinary relation.

| Site | Pattern | What it means | Replacement |
| --- | --- | --- | --- |
| `api/queries/rarity_queries.py:194` | `COUNT { (r)--() }` | Total degree of one release, both directions. | `graph.release_degree`, a counter relation: `by_artist + on_label + in_genre + in_style + derived_from + credited_on + credited_to + issued_on + collected + wants`, grouped by `release_id`. Computing it per release inside the rarity page is the shape that has to be avoided; precomputing it is the shape the rarity pipeline already expects (it reads three other pre-computed counters). |
| `api/queries/rarity_queries.py:203` | `max(COUNT { (a)--() })` | Max degree of a release's artists. | `graph.artist_degree`: `by_artist + master_by_artist + member_of (both endpoints) + alias_of (both endpoints) + same_as`, grouped by `artist_id`. |
| `api/queries/network_queries.py:109` | `size([(a)-[]-() \| 1])` | Degree of one artist. | Same `graph.artist_degree` row. The list-comprehension form is already documented as a memory hazard at `rarity_queries.py:187-190`; the counter relation removes the hazard rather than porting it. |
| `api/queries/insights_neo4j_queries.py:25` | `size([(a)-[]-() \| 1])` over **all** artists, ordered, top 100 | Degree centrality leaderboard. | `SELECT artist_id, name, degree FROM graph.artist_degree ORDER BY degree DESC LIMIT $1`. Today this is a whole-graph scan building a list cell per edge; the counter relation turns it into an index scan. |
| `api/queries/musicbrainz_queries.py:43,49` | `(a:Artist)-[r]->(target:Artist) WHERE r.source = 'musicbrainz'`, both directions | Any MusicBrainz-sourced artist-artist edge. | `GRAPH_TABLE` over the shared `mb_related` label, or plain SQL over `musicbrainz.relationships` where both entity types are `artist`, joined to `graph.mb_artist` twice for `discogs_artist_id`. The `r.source` filter disappears: in PostgreSQL the MusicBrainz edges live in a different relation from the Discogs ones. |
| `api/queries/musicbrainz_queries.py:120` | `MATCH ()-[r]->() WHERE r.source = 'musicbrainz'` | Count every MusicBrainz-sourced edge. | `SELECT count(*) FROM musicbrainz.relationships`. |

**One behaviour change falls out of the last two rows, and it is a fix rather than a regression.**
`musicbrainz-graph-enricher` writes `SET r.source = 'musicbrainz'` as a post-`MERGE` assignment
(`brainzgraphinator/_projections.py:146-150`), not as part of the merge key. `MEMBER_OF` is a type
both enrichers write — Discogs from `members[]`/`groups[]`, MusicBrainz from `member of band` — so
a Discogs-derived membership edge that MusicBrainz also asserts is retro-stamped
`source='musicbrainz'` and is returned today by `get_artist_mb_relationships`. In PostgreSQL the
two assertions live in `graph.member_of` and `graph.mb_rel_artist_artist` and cannot be confused.
Parity for this one function will therefore show a diff, and the diff is the Neo4j side being
wrong. It needs an explicit decision at rewrite time rather than a silent reconciliation.

### The MusicBrainz relationship-type vocabulary

A second, sharper mismatch sits under the same function. `type(r)` in Cypher returns the
**enricher's** type name — `MEMBER_OF`, `COLLABORATED_WITH`, `TAUGHT`, `TRIBUTE_TO`, `FOUNDED`,
`SUPPORTED`, `SUBGROUP_OF`, `RENAMED_TO` — because `brainzgraphinator/_projections.py:17-26` maps
MusicBrainz's raw strings through `MB_RELATIONSHIP_MAP` before creating the edge.
`musicbrainz-sql-loader` does no such mapping: it stores `relations[].type` verbatim
(`brainztableinator/_record_processing.py:141-166`), so `graph.mb_rel_artist_artist.relationship_type`
holds `member of band`, `collaboration`, `teacher`, `tribute`, `founder`, `supporting musician`,
`subgroup`, `artist rename`.

ADR 0012's edge mapping says "`relationship_type` carries the original type", which is true of the
relational side and not of the Neo4j side. The rewrite must apply `MB_RELATIONSHIP_MAP` — in the
view, in the query, or in the API response shape — or `/api/artist/{id}/relationships` changes its
payload vocabulary. Putting the map in a `graph` schema function alongside
`graph.credit_role_category` and `graph.medium_label` is the consistent placement: those two
already render a `groovemap-runtime` vocabulary into an `IMMUTABLE` SQL function at
statement-build time.

The loader also normalizes `direction: backward` by swapping the endpoints
(`brainztableinator/_record_processing.py:147-152`), exactly as the enricher does
(`_projections.py:142-143`). Those two agree, so no work falls out of it.

### The administrative metadata call

`get_neo4j_storage` (`api/queries/admin_queries.py:124`) is the one function that reads Neo4j's own
metadata rather than the catalog. It makes two calls:

- `CALL apoc.meta.stats() YIELD labels, relTypesCount` — node count per label and edge count per
  type.
- `CALL dbms.queryJmx('org.neo4j:instance=kernel#0,name=Store sizes')` — on-disk store sizes,
  already best-effort and already allowed to fail.

The replacement is a catalog query, and it is strictly better: `apoc.meta.stats()` is an estimate
sampled from Neo4j's count store, while PostgreSQL can give exact counts per relation and exact
sizes per relation.

```sql
-- Node counts per label: one row per vertex view.
SELECT c.relname AS label, (SELECT count(*) FROM ...) AS count   -- or n_live_tup for an estimate
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'graph';

-- Store sizes: the underlying base tables, which the views do not have of their own.
SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) AS total_size
FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC;
```

The second half already exists in this same module as `get_postgres_storage`
(`api/queries/admin_queries.py:170`), so the replacement is a merge of the two panels rather than
new code. Note the one thing the catalog cannot answer while the graph is view-backed: a view has
no size. If the panel is to keep showing "how big is the graph", it must report the sizes of the
base tables or of the materialized edge tables Table 2 proposes — which is one more argument for
materializing them. ADR 0012 defers what the panel should show to `catalog-api`; this spike only
establishes that nothing is lost.

### Full-text search: six functions ADR 0012 does not count

ADR 0012 says "all but two query functions are fixed one-hop or two-hop patterns". That is true of
the *traversals*. It misses a third category: six functions do not traverse at all, and instead
call Neo4j's Lucene full-text indexes.

| Function | Index | Backing index in `database-schema` |
| --- | --- | --- |
| `api/queries/neo4j_queries.py:150` `autocomplete_artist` | `artist_name_fulltext` | `src/groovemap_schema/neo4j.py` |
| `api/queries/neo4j_queries.py:155` `autocomplete_label` | `label_name_fulltext` | same |
| `api/queries/neo4j_queries.py:160` `autocomplete_genre` | `genre_name_fulltext` | same |
| `api/queries/neo4j_queries.py:165` `autocomplete_style` | `style_name_fulltext` | same |
| `api/queries/credits_queries.py:156` `autocomplete_person` | `person_name_fulltext` | same |
| (`api/queries/neo4j_queries.py:50` `_autocomplete` is their shared engine) | | |

`GRAPH_TABLE` has nothing to say about these. They are `SQL-only (no graph)`, and PostgreSQL
serves them better than Lucene does — `pg_trgm` with a `GIN` index gives prefix and fuzzy matching
without the Lucene metacharacter escaping that `_escape_lucene_query`
(`api/queries/neo4j_queries.py:33`) exists to work around, and whose absence once produced an
unhandled 500 on names like `AC/DC` (the comment at `credits_queries.py:162-165` records it).

The work is real and it is not in the program plan: a `GIN` index per name column on
`graph.artist`, `graph.label`, `graph.genre`, `graph.style`, and `graph.person` — and for
`graph.genre`, `graph.style`, and `graph.person` an index cannot be created on a view, so those
three need the name-keyed tables Table 3 specifies before autocomplete can move at all.

### Properties Cypher reads that no `graph` view exposes

This is the gap list. Everything else in the coverage table maps to a column that
`docs/architecture.md` already publishes.

| Property read | Read by | Where it comes from today | Relational answer |
| --- | --- | --- | --- |
| `Genre.release_count`, `.artist_count`, `.label_count`, `.style_count`, `.first_year` | `explore_genre`, `get_genre_emergence`, `_GENRE_COUNT_QUERY` | `graphinator.compute_genre_style_stats`, post-import pass (`graphinator/graphinator.py:866-900`) | **New: `graph.genre_stats`.** See Table 3. |
| `Style.release_count`, `.artist_count`, `.label_count`, `.genre_count`, `.first_year` | `explore_style`, `get_genre_emergence` | same pass (`graphinator.py:902-936`) | **New: `graph.style_stats`.** |
| `Label.release_count`, `.artist_count`, `.genre_count` | `explore_label`, `_LABEL_QUERY`, `_LABEL_SIZE_QUERY` | same pass (`graphinator.py:942-964`) | **New: `graph.label_stats`.** |
| `Release.formats` | `get_release_details`, `get_label_format_profile`, `get_label_gaps`, `get_artist_gaps`, `get_master_gaps`, `_MEDIA_QUERY` | `graphinator` flattens `data->'formats'[].name` | Add a `formats text[]` column to `graph.release` — it is `ARRAY(SELECT value->>'name' FROM jsonb_array_elements(data->'formats'))`, purely additive, and the contract allows appending a view column. |
| `Release.catalog_number` | `get_user_collection`, `get_user_wantlist` | Written **twice**: by `graphinator` from `labels[0].catno`, and by `api/syncer.py:621,869` via `SET r += rel.metadata` | `user_collections` and `user_wantlists` have no `catno` column, so the syncer's copy has no relational home. The Discogs copy does: add `catalog_number` to `graph.release` as `data->'labels'->0->>'catno'`. The syncer's write then has nothing left to do, which is what phase 4 wants. |
| `Artist.mbid`, `.mb_type`, `.mb_gender`, `.mb_begin_date`, `.mb_end_date`, `.mb_area`, `.mb_begin_area`, `.mb_disambiguation` | `get_artist_musicbrainz`, `get_enrichment_status` | `brainzgraphinator` `SET`s them onto the Discogs node | Already relational: `musicbrainz.artists` joined on `discogs_artist_id`. `graph.mb_artist` exposes every one of them. No new relation. |
| `CREDITED_ON.category` | `get_person_credits`, `get_person_timeline`, `get_release_credits`, `get_role_leaderboard`, `get_person_profile`, `get_person_role_breakdown` | `graphinator` sets `category` | `graph.credited_on` exposes the same value as **`role_category`**. A pure rename at rewrite time; no relation changes. Worth stating because six functions touch it and a missed rename is a silent null. |
| `User.discogs_username` | (written by the syncer; read by nothing) | `api/syncer.py:611` | `graph.app_user` deliberately omits it. No read depends on it, so it dies with the syncer's Cypher. |

The first three rows are the only **blocking** gap in the whole coverage matrix. They are also the
highest-stakes one: `explore_genre`'s docstring records that reading `g.release_count` "replaces 4
independent traversal queries (~200M DB hits for Rock) with a single property read (~3 DB hits)".
A rewrite that drops the counter and re-aggregates on request does not merely get slower, it
reproduces the exact failure that took the rarity pipeline down for thirty-three consecutive days
(`api/queries/rarity_queries.py:87-93`).

### Node labels that need name-keyed tables

Four Neo4j labels are keyed on `name`, and four more are keyed on an id derived at projection time
rather than read from a catalog table. All eight are served today by a `DISTINCT`-over-unnest view,
which means every lookup of one genre name scans `releases` and `masters` end to end, and none of
them can carry an index.

| Neo4j label | Phase 0 view | How the view is built | Needs a table? |
| --- | --- | --- | --- |
| `:Genre` | `graph.genre` | `SELECT DISTINCT` over `jsonb_array_elements_text` of `genres` across `releases ∪ masters` | **Yes** — name-keyed, and autocomplete needs a `GIN` index on it |
| `:Style` | `graph.style` | same over `styles` | **Yes** |
| `:Person` | `graph.person` | `SELECT DISTINCT` over `extraartists[].name` across every release | **Yes** — keyed on the verbatim name, exactly as Neo4j keys `:Person` |
| `:MediaFamily` | `graph.media_family` | `SELECT DISTINCT` over both providers' `media.items[].family` | **Yes** |
| `:Medium` | `graph.medium` | `DISTINCT ON (medium_id)` over both providers' media blocks | **Yes** — id-keyed but derived the same way |
| `:Company` | `graph.company` | `DISTINCT ON (company_id)` over the canonical companies block; id is the Discogs id or `name:<folded name>` | **Yes** — the fallback id is a folded name, so this is a name-keyed table in all but spelling |
| `:Artist`, `:Label`, `:Master`, `:Release` | `graph.artist`, … | Straight projections of `artists`, `labels`, `masters`, `releases` | No — real tables already |
| `:User` | `graph.app_user` | Projection of `users` | No |

Note one detail the tables must preserve: `graph.company`'s identity rule is the producer's, and
the view's comment says so — a whole Discogs id when one is supplied, otherwise `name:` plus the
name case-folded with inner whitespace collapsed, with punctuation deliberately left alone.
PostgreSQL's `lower` is an approximation of Python's `casefold`. A table must be written by the
same rule the view encodes, or the two disagree on the German eszett and a handful of other
characters.

### Table 2: the target relational edge model

"Owner" is the service that writes the relation. "Shape" is `view` where the phase 0 view is
sufficient and `table` where it is not. Key columns are the ones `CREATE PROPERTY GRAPH` already
declares (`src/groovemap_schema/postgres.py:2321-2452`), so a materialized table can replace a view
without touching the property-graph statement.

| PGQ edge label | Neo4j type | Shape | Key columns | Endpoint columns | Indexes | Owner | Why |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `by_artist` | `(:Release)-[:BY]->(:Artist)` | **table** | `(release_id, artist_id)` | `release_id` → `artist_id` | PK on the key; `(artist_id, release_id)` | discogs-sql-loader | Hottest edge in the graph. Every collaborator, recommendation, rarity and expand query walks it. Re-unnesting `data->'artists'` per query is the shape least likely to hold. |
| `on_label` | `(:Release)-[:ON]->(:Label)` | **table** | `(release_id, label_id)` | `release_id` → `label_id` | PK; `(label_id, release_id)` | discogs-sql-loader | Same argument. |
| `in_genre` | `(:Release)-[:IS]->(:Genre)` | **table** | `(release_id, genre_name)` | `release_id` → `genre_name` | PK; `(genre_name, release_id)` | discogs-sql-loader | Mega-genre cardinality (Rock ≈ 7M releases). |
| `in_style` | `(:Release)-[:IS]->(:Style)` | **table** | `(release_id, style_name)` | `release_id` → `style_name` | PK; `(style_name, release_id)` | discogs-sql-loader | |
| `derived_from` | `(:Release)-[:DERIVED_FROM]->(:Master)` | **table** | `(release_id, master_id)` | `release_id` → `master_id` | PK; `(master_id, release_id)` | discogs-sql-loader | The sibling pattern joins it to itself; the reverse index is what makes that cheap. |
| `master_by_artist` | `(:Master)-[:BY]->(:Artist)` | **table** | `(master_id, artist_id)` | `master_id` → `artist_id` | PK; `(artist_id, master_id)` | discogs-sql-loader | |
| `master_in_genre` | `(:Master)-[:IS]->(:Genre)` | **table** | `(master_id, genre_name)` | `master_id` → `genre_name` | PK; `(genre_name, master_id)` | discogs-sql-loader | |
| `master_in_style` | `(:Master)-[:IS]->(:Style)` | **table** | `(master_id, style_name)` | `master_id` → `style_name` | PK; `(style_name, master_id)` | discogs-sql-loader | |
| `part_of` | `(:Style)-[:PART_OF]->(:Genre)` | view | `(style_name, genre_name)` | `style_name` → `genre_name` | — | discogs-sql-loader (if materialized) | Tiny: 757 styles × 16 genres bounds it. The single-genre guard (`jsonb_array_length(genres) = 1`) must survive materialization verbatim. |
| `member_of` | `(:Artist)-[:MEMBER_OF]->(:Artist)` | **table** | `(member_artist_id, group_artist_id)` | both → `graph.artist` | PK; reverse index | discogs-sql-loader | Read in both directions by `expand_artist_aliases` and `count_artist_aliases`; a view cannot index the reverse. |
| `alias_of` | `(:Artist)-[:ALIAS_OF]->(:Artist)` | **table** | `(alias_artist_id, artist_id)` | both → `graph.artist` | PK; reverse index | discogs-sql-loader | |
| `sublabel_of` | `(:Label)-[:SUBLABEL_OF]->(:Label)` | view | `(sublabel_id, parent_label_id)` | both → `graph.label` | — | discogs-sql-loader (if materialized) | Nothing in `api/queries/` reads it today. Materialize only when a caller appears. |
| `credited_on` | `(:Person)-[:CREDITED_ON]->(:Release)` | **table** | `(person_name, release_id, role)` | `person_name` → `release_id` | PK; `(release_id, person_name)`; `(role_category, person_name)` | discogs-sql-loader | Nine credits functions; the whole feature is one unnest of `extraartists` away from a full scan. `role_category` stays a generated column over `graph.credit_role_category(role)`. |
| `same_as` | `(:Person)-[:SAME_AS]->(:Artist)` | **table** | `(person_name, artist_id)` | `person_name` → `artist_id` | PK; `(artist_id)` | discogs-sql-loader | Small, but joined on every credits read. |
| `credited_to` | `(:Release)-[:CREDITED_TO]->(:Company)` | **table** | `(release_id, company_id, role, source)` | `release_id` → `company_id` | PK; `(company_id, release_id)` | discogs-sql-loader | `source` stays in the key, as ADR 0011 and the view already have it. |
| `issued_on` | `(:Release)-[:ISSUED_ON]->(:Medium)` | **table** | `(release_id, medium_id, source)` | `release_id` → `medium_id` | PK; `(medium_id, release_id)` | **both** — discogs-sql-loader writes `source='discogs'`, musicbrainz-sql-loader writes `source='musicbrainz'` | `source` is already part of the key precisely so the two providers do not collide. This mirrors the enrichers exactly, including each one's source-scoped prune. |
| `in_family` | `(:Medium)-[:IN_FAMILY]->(:MediaFamily)` | view | `(medium_id, family_name)` | `medium_id` → `family_name` | — | (derived from `graph.medium`) | Bounded by the media taxonomy — a few dozen rows. A view over the `medium` table is enough. |
| `collected` | `(:User)-[:COLLECTED]->(:Release)` | view | `(collection_id)` | `user_id` → `release_id` | `user_collections` already indexes `user_id` and `release_id` | catalog-api | ADR 0012 phase 4 makes this a view on purpose. `user_collections` is a real table with real indexes; nothing to materialize. |
| `wants` | `(:User)-[:WANTS]->(:Release)` | view | `(wantlist_id)` | `user_id` → `release_id` | as above on `user_wantlists` | catalog-api | |
| `owns` | (none — native) | view | `(owned_copy_id)` | `user_id` → `item_id` | `owned_copies` is FK'd to both endpoints | catalog-api | Has no Neo4j counterpart; it is additive. |
| `mb_rel_<src>_<dst>` (16) | the eight artist-artist types **and** every other endpoint pair | view | `(relationship_id)` | `source_mbid` → `target_mbid` | **new** on `musicbrainz.relationships`: `(source_entity_type, target_entity_type, source_mbid)` and the mirrored `(…, target_mbid)`; `(relationship_type)` | musicbrainz-sql-loader | `musicbrainz.relationships` is already a real, typed, directed edge table with a natural key and `direction: backward` normalization. The sixteen views are pure projection. **What it lacks is the endpoint-pair indexes**: each view filters on `source_entity_type`/`target_entity_type` and inner-joins both endpoint tables, and today nothing indexes that filter. |

**Two structural gaps in `musicbrainz.relationships` that phase 2 must close and that this spike is
the first to name.** The natural-key constraint
(`src/groovemap_schema/postgres.py:1166-1184`) guarantees uniqueness but is not a useful access
path for the endpoint-pair filter every one of the sixteen views applies. And
`musicbrainz-sql-loader` performs **no delete-reconciliation at all** — the table is append-only
(no `updated_at < started_at` sweep, no orphan cleanup), so a relationship removed upstream stays
forever. `brainzgraphinator`, by contrast, prunes its `ISSUED_ON` edges by source. The relational
side is therefore not yet edge-parity with the graph side for MusicBrainz relationships, and a
parity harness will find it. `discogs-sql-loader` already has the machinery to copy: a
`purge_stale_rows` keyed on `updated_at < started_at`, capped at a 90% delete fraction, and vetoed
by any dead-letter this run (`tableinator/record_persistence.py:26-112`).

### Table 3: new relations with no Neo4j edge behind them

These are not edges. They are the pre-computed node properties `graphinator`'s post-import pass
writes, which eight query functions read as if they were free. A property graph declared over views
has nowhere to put them, so they become relations of their own.

| Relation | Replaces | Columns | Key | Indexes | Owner |
| --- | --- | --- | --- | --- | --- |
| `graph.genre_stats` | `Genre.release_count`, `.artist_count`, `.label_count`, `.style_count`, `.first_year` | `name`, `release_count`, `artist_count`, `label_count`, `style_count`, `first_year` | `(name)` | PK; `(first_year)` | discogs-sql-loader |
| `graph.style_stats` | `Style.release_count`, `.artist_count`, `.label_count`, `.genre_count`, `.first_year` | `name`, `release_count`, `artist_count`, `label_count`, `genre_count`, `first_year` | `(name)` | PK; `(first_year)` | discogs-sql-loader |
| `graph.label_stats` | `Label.release_count`, `.artist_count`, `.genre_count` | `label_id`, `release_count`, `artist_count`, `genre_count` | `(label_id)` | PK; `(release_count)` | discogs-sql-loader |
| `graph.artist_degree` | `size([(a)-[]-() \| 1])`, `COUNT { (a)--() }` | `artist_id`, `degree` | `(artist_id)` | PK; `(degree DESC)` | discogs-sql-loader |
| `graph.release_degree` | `COUNT { (r)--() }` | `release_id`, `degree` | `(release_id)` | PK | discogs-sql-loader |
| `graph.genre` / `graph.style` / `graph.person` / `graph.media_family` / `graph.medium` / `graph.company` | the `DISTINCT`-over-unnest vertex views | as the views publish, plus a `GIN (name gin_trgm_ops)` on the three that autocomplete reads | as declared | PK on the key; trigram index on `name` | discogs-sql-loader (`medium`/`media_family` upserted by both loaders, `ON CONFLICT DO NOTHING`, mirroring `brainzgraphinator`'s `ON CREATE`-only medium write) |

Three notes on how these get written, all of which follow the loaders' existing shape rather than
inventing one:

- **The refresh trigger already exists.** `discogs-sql-loader` handles the
  `extraction_complete` message (`tableinator/tableinator.py:694-765`) and already does whole-table
  work at that point — the stale-row purge. That is the same latch `graphinator` uses to start its
  post-import pass, after all four `extraction_complete` signals
  (`graphinator/graphinator.py:703-755`). The counters belong on the same hook, which means phase 2
  buys them without a new scheduler.
- **The degree relations are sums over the edge tables.** Once `by_artist` and its siblings are
  tables, `graph.artist_degree` is one `INSERT … SELECT` over a `UNION ALL`. It does not need to
  re-read a single JSONB document.
- **`graph.release_degree` and `graph.collected`/`graph.wants` disagree about ownership.** Release
  degree as Neo4j computes it counts `COLLECTED` and `WANTS` edges, which `catalog-api` writes and
  the loader never sees. Either the loader's counter excludes them — a parity diff the rarity
  scoring must be re-tuned for — or degree is a view summing a loader-written base count plus a
  live count over `user_collections`/`user_wantlists`. **The second is correct and it is cheap**,
  because both tables index `release_id`. This is the one place in the whole edge model where a
  relation must be split across two owners, and it should be recorded as such rather than resolved
  by whoever writes it first.

### Why `discogs-sql-loader` owns the Discogs edges

The bead asks which loader owns each relation. The answer is not symmetric, and the asymmetry is
worth stating because it sizes phase 2 unevenly:

- `musicbrainz-sql-loader` **already materializes edges**. `musicbrainz.relationships` and
  `musicbrainz.external_links` are real child tables written from unnested arrays
  (`brainztableinator/_record_processing.py:141-191`). Giving it the MusicBrainz half of the edge
  model is extending something it does.
- `discogs-sql-loader` **materializes nothing**. It writes four tables, one document per row, whole
  document as JSONB, and unnests exactly one thing: the `identifiers` block into `provider_aliases`
  (`tableinator/identity.py:62-148`). Giving it fifteen edge tables plus six vertex tables plus five
  counter relations is new capability, and it is the larger half of phase 2 by a wide margin.
- Both consume the **same fanout exchanges the graph enrichers consume**, on their own queues
  (`contracts/catalog-events/v1/contract.json` in each repo lists both consumers). That is what
  makes retiring the enrichers in phase 4 possible: the loaders already see every fact the
  enrichers see. Nothing new has to be published.
- Both already have the write mechanics. `discogs-sql-loader` batches 100 rows per transaction with
  `executemany` and `ON CONFLICT DO UPDATE`, gates on a content hash, and purges stale rows
  (`tableinator/batch_writer.py:50-183`, `tableinator/record_persistence.py:26-112`).
  `musicbrainz-sql-loader` runs one transaction per message with `executemany` per child table
  (`brainztableinator/_persistence.py:104-125`). Neither uses `COPY` or staging tables; neither
  needs to.

## Verdict

**GO on full coverage.** Every one of the 184 store-touching query functions and query constants in
`catalog-api`'s `api/queries/*.py` and `api/syncer.py` has a PostgreSQL spelling. The `unsupported`
class is empty.

The evidence behind that, in the order it matters:

1. **No query function is unsupported.** 7 are a single `GRAPH_TABLE` pattern, 105 are a
   `GRAPH_TABLE` pattern wrapped in ordinary SQL, 2 need a recursive CTE, and 70 need no graph at
   all. Nothing in the read surface requires a construct PostgreSQL lacks.
2. **ADR 0012's "all but two" claim holds for traversals and undercounts the work.** The two
   variable-length functions are exactly the two the record names. But the record's inventory
   misses six full-text functions, which do not traverse and which need trigram indexes on three
   relations that are views today and therefore cannot carry an index at all. That is scope phase 3
   must carry, and it is scope no measurement will surface because it is a correctness gap rather
   than a performance one.
3. **Five unbound relationship patterns and one `apoc.meta.stats()` call all have replacements**,
   and each replacement is better than what it replaces: three degree computations become an
   indexed counter read instead of a list-materializing scan that is already documented as a memory
   hazard; the two MusicBrainz unbound patterns become reads of a table that already exists; and
   the metadata call becomes an exact catalog query instead of a sampled estimate.
4. **One blocking gap, and it is a write-side gap rather than a read-side one.** Eight functions
   read node properties that `graphinator` computes in a post-import pass and that no `graph` view
   carries: the genre, style, and label counters and the two `first_year` values. Without the
   counter relations in Table 3, those eight rewrites silently become whole-catalog aggregations at
   mega-genre cardinality. `explore_genre`'s own docstring records that the property read replaced
   "~200M DB hits for Rock" with "~3 DB hits", and `rarity_queries.py:87-93` records what happened
   the last time a whole-catalog scan met a transaction timeout: thirty-three consecutive failed
   daily cycles. **Phase 2 is therefore a hard precondition for the parts of phase 3 that touch
   `explore_*`, `get_genre_emergence`, and the rarity signal batch** — not a parallel track.
5. **Three smaller gaps are additive view columns**, allowed by the persistence contract and
   costing nothing: `formats` and `catalog_number` on `graph.release`, and a rename of
   `CREDITED_ON.category` to the `role_category` the view already publishes.
6. **Two parity diffs are known in advance**, which is better than discovering them in the harness.
   `get_artist_mb_relationships` will return fewer rows in PostgreSQL because Neo4j retro-stamps
   Discogs membership edges with `source='musicbrainz'`; and its `relationship_type` values will be
   the raw MusicBrainz strings unless `MB_RELATIONSHIP_MAP` is rendered into a `graph` schema
   function the way `graph.credit_role_category` and `graph.medium_label` already are.

The GO is on coverage only. It says the migration is expressible; it does not say it is fast. The
performance half of question 1 and the depth caps of question 2 stay open and belong to the sibling
work. If either returns NO-GO, this matrix is still the input phase 2 and 3 need, because it is a
statement about the code rather than about any engine's planner.

## Recommendation

### The families, in migration order

Sizes are query functions and the lines of the modules they live in. "New relations" names what
Table 2 or Table 3 must deliver before the family can cross.

| # | Family | Modules | Size | New relations needed | Why here |
| --- | --- | --- | --- | --- | --- |
| 1 | **Vertex lookups and store statistics** | `collaborator_queries`, `network_queries`, `gap_queries` (`*_metadata`), `neo4j_queries` (`get_year_range`, `get_graph_stats`), `admin_queries` | 9 functions | none | Nine functions that touch no edge. They prove the seam end to end against the phase 0 views before a single traversal is ported, and they need neither phase 2 nor PostgreSQL 19. |
| 2 | **Full-text autocomplete** | `neo4j_queries:50-168`, `credits_queries:156` | 6 functions, ~90 lines | `graph.genre`, `graph.style`, `graph.person` as **tables** with trigram indexes | Independent of `GRAPH_TABLE` entirely. Retires the Lucene escaping and its 500-on-`AC/DC` class of bug. Blocked only on three vertex tables, which are the cheapest rows in Table 3. |
| 3 | **Collaborators and network** (the pilot) | `network_queries` (179), `collaborator_queries` (52) | 5 functions | `by_artist` table | The bead for the phase 0 pilot names `network_queries.get_multi_hop_collaborators`. Port `count_multi_hop_collaborators` with it or pagination totals diverge — the count query has its own `UNION` and its own anti-join. Note that `/api/collaborators/{id}`, which NLQ and MCP actually call, is the *different* one-hop `collaborator_queries.get_collaborators`; both move together or the two collaborator surfaces disagree. |
| 4 | **Credits and provenance** | `credits_queries` (211) | 9 functions | `credited_on`, `same_as` tables | Self-contained: one edge type, one vertex label, one router. The `category` → `role_category` rename is the only subtlety, and it is mechanical. |
| 5 | **Media and label DNA** | `label_dna_queries` (341) | 13 functions | `on_label`, `in_genre`, `in_style` tables; `medium`, `media_family` tables | Exercises the fixed 3-hop `ON`/`ISSUED_ON`/`IN_FAMILY` pattern — the deepest fixed pattern in the service and the best single test of `GRAPH_TABLE` join planning. `get_label_identity` gets far cheaper once `graph.label_stats` lands, but does not need it to be correct. |
| 6 | **Collection, wantlist, taste, and gaps** | `user_queries` (433), `taste_queries` (154), `gap_queries` (284), `collection_media_queries`, `release_media_queries` | 28 functions | `collected`/`wants` stay views; needs `by_artist`, `on_label`, `in_genre`, `in_style`, `derived_from`, `issued_on` tables | The largest user-facing block. Every anti-join in the service lives here. `graph.collected` and `graph.wants` need no work, which is the point of ADR 0012 phase 4 — this family is where that claim gets tested. Needs `catalog_number` on `graph.release` first. |
| 7 | **Recommendations and fit** | `recommend_queries` (502, minus the traversal), `fit_queries` (337) | 17 functions | the same edge tables as 6 | Heavy aggregation, no new relations beyond family 6's. `get_candidate_artists`'s per-genre `LIMIT 100000` sample is a Neo4j cost control with no automatic SQL equivalent; the rewrite needs a deliberate decision about whether to keep sampling or to rely on the indexes. |
| 8 | **Explore, expand, count, trends, details, genre tree** | `neo4j_queries` (1,003, minus autocomplete and the path), `genre_tree_queries` (41) | 45 functions | **`graph.genre_stats`, `graph.style_stats`, `graph.label_stats` — blocking** | Much the biggest family and the first that phase 2 gates hard. `explore_genre`, `explore_style`, and `get_genre_emergence` are pure counter reads today; without the counter relations they become mega-genre scans. Put this after 1-7 precisely so the counter relations have a full phase to land. |
| 9 | **Rarity signal batch** | `rarity_queries` (714), `api/rarity/families/grooved.py` | 11 Cypher constants + 6 driver functions | `graph.label_stats`, `graph.genre_stats`, `graph.artist_degree`, `graph.release_degree`; `formats` on `graph.release` | Highest risk in the program. It is the one workload with a documented 600-second failure history, it reads three pre-computed counters and two unbound degrees, and its chunking contract (`rarity_queries.py:82-110`) is load-bearing: `UNWIND $ids` becomes `WHERE release_id = ANY($1)` and the 20,000-row page stays. The batch also gets *simpler* — `get_rarity_by_artist` and `get_rarity_by_label` collapse from two Cypher round trips plus a PostgreSQL `= ANY` into one query. |
| 10 | **Insights computations** | `insights_neo4j_queries` (130) | 4 functions | `graph.artist_degree`; edge tables from 5 and 6 | Background, daily, 1,800-second client budget. `query_artist_centrality` is the cleanest win in the program: a whole-graph list-materializing scan becomes `ORDER BY degree DESC LIMIT 100` on an indexed counter. |
| 11 | **MusicBrainz enrichment** | `musicbrainz_queries` (124) | 4 functions | indexes on `musicbrainz.relationships`; delete-reconciliation in `musicbrainz-sql-loader`; `MB_RELATIONSHIP_MAP` as a `graph` schema function | Small, but the only family with two known parity diffs. Do it late enough that the harness is mature enough to tell a real diff from a fixed bug. |
| 12 | **The two variable-length workloads** | `neo4j_queries:97` `find_shortest_path`, `recommend_queries:403` `get_explore_traversal` | 2 functions | the edge tables from 3, 5, and 6 | Gated on spike 9c8.3's depth caps and latency. `find_shortest_path` moves as one unit with `GET /api/path`, NLQ `find_path`, and MCP `find_path`. Both stay on Neo4j regardless of how many other families have crossed, exactly as the program plan says. |
| 13 | **Write cutover and the admin panel** | `api/syncer.py` (1,154), `admin_queries:124` | 4 sync functions + 1 panel | none | Phase 4/5. The four sync writes delete outright: every fact is already in `user_collections`/`user_wantlists`. The one loose end is the syncer's `SET r += rel.metadata`, which writes `catalog_number` onto the catalog `Release` node from a user's sync — a cross-boundary write with no relational home until `catalog_number` becomes a `graph.release` column sourced from the Discogs document. |

### What to file for phase 2

In dependency order, because the loaders' work gates most of phase 3:

1. **`database-schema`:** add `formats` and `catalog_number` to `graph.release` (additive view
   columns, contract stays v1). Add `graph.credit_role_category`'s sibling: a
   `graph.mb_relationship_type(text)` function rendering `MB_RELATIONSHIP_MAP`.
2. **`database-schema`:** DDL for the six name-keyed vertex tables and the fifteen Discogs edge
   tables in Table 2, plus the five counter relations in Table 3, with the indexes both tables
   specify. Every one is additive; the property-graph statement does not change, because the key
   columns are the ones it already declares.
3. **`database-schema`:** the two missing endpoint-pair indexes on `musicbrainz.relationships`.
4. **`discogs-sql-loader`:** write the edge tables and the vertex tables from the same events it
   already consumes, inside the existing 100-row batch transaction, and refresh the counter
   relations on `extraction_complete` alongside the existing purge. Reproduce three derivation
   rules verbatim: the `PART_OF` single-genre guard, the `member_of`/`sublabel_of` both-ends union,
   and `graph.company`'s casefolded-name fallback id.
5. **`musicbrainz-sql-loader`:** add delete-reconciliation for `musicbrainz.relationships`, modelled
   on `discogs-sql-loader`'s `purge_stale_rows` with its delete-fraction cap and dead-letter veto.
   Write the `musicbrainz` half of `issued_on`.
6. **`catalog-api`:** nothing in phase 2. `collected`, `wants`, and `owns` stay views over tables it
   already writes.

### What phase 3 should carry that ADR 0012 does not yet name

- The six full-text functions and their trigram indexes.
- The `CREDITED_ON.category` → `role_category` rename across nine credits call sites.
- A parity harness. None exists: no test anywhere compares a Cypher result to a SQL result.
  `catalog-api`'s `tests/test_real_databases.py` is the right host, because it is the only test that
  already builds a live PostgreSQL pool and a live Neo4j driver in one process and applies the real
  schema through `groovemap_schema.postgres.create_postgres_schema`.
- The two known parity diffs above, recorded as expected before the harness runs rather than
  triaged after.
