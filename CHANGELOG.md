# Changelog

All notable changes to the GrooveMap database schema will be documented here.

## v0.11.0 (2026-11-07)

### Release notes

Adds `public.release_search` (`title_tsv`, with GIN index `idx_release_search_title_tsv`), kept current by triggers on `public.releases`. Schema-init creates it under a SHARE ROW EXCLUSIVE lock held for a few milliseconds. After schema-init, run `CALL public.release_search_backfill();` once as the schema owner, outside a transaction, in a quiet window; then `SELECT * FROM public.release_search_drift();` must return `(0,0,0)` before catalog-api reads `release_search`. Also adds the per-day per-work identity ledger rollup table, and creates `pg_stat_statements` only when the server preloads it and the role is a superuser; otherwise it logs a skip. All changes are additive.

### Feat

- **postgres**: store the release title search vector in a side table
- **schema**: create pg_stat_statements in groovemap when the server preloads it
- **postgres**: add a per-day per-work rollup of the identity ledger events

### Fix

- **postgres**: run the release search backfill's SQL with a pinned search_path

## v0.10.0 (2026-11-07)

### Release notes

Adds `insights.label_longevity.is_placeholder` (boolean, default false), additive and safe for older writers. Deploy before analytics-engine v0.7.0, which writes it.

### Feat

- **schema**: add insights.label_longevity.is_placeholder

## v0.9.0 (2026-11-06)

### Release notes

Breaking: `relationships_natural_key` is dropped, and `relationships_key` (hash) is the only key. Apply this before MusicBrainz is enabled, and only with musicbrainz-sql-loader v0.6.0 or later as the writer: an older writer's upserts fail. The drop is guarded and instant on an empty table.

### BREAKING CHANGE

- an ON CONFLICT (source_mbid, target_mbid, source_entity_type,
target_entity_type, relationship_type, begin_date, end_date, attributes)
target on musicbrainz.relationships fails with "there is no unique or
exclusion constraint matching the ON CONFLICT specification". Deploy
musicbrainz-sql-loader v0.6.0 or later first.

### Feat

- **postgres**: retire the MusicBrainz relationships natural-key constraint

### Fix

- **postgres**: guard the natural-key drop with IF EXISTS

## v0.8.0 (2026-11-06)

### Release notes

Deploy this before discogs-sql-loader and musicbrainz-sql-loader: both write `loader_extraction_latch.purge_vetoed`, and check-images pairs the Discogs loader with this release. `data_completeness.completeness_pct` is nullable, and `bootstrap_fill` applies the Person name padding rule.

### Feat

- **insights**: make data_completeness.completeness_pct nullable
- **postgres**: record vetoed purges durably on the extraction latch

### Fix

- **graph**: apply the Person name padding rule in bootstrap_fill

## v0.7.0 (2026-11-05)

### Deploy order with catalog-api

This release goes on Lux at the reset, on a fresh database. Documented orders for the changes it carries:

1. `catalog-api` first: the release that drops the `relkind = 'm'` filter from its two Identity Ledger freshness statements and also requires the state row. The ledger tables were materialized views through v0.6.3 and are plain tables now.
2. This schema, applied by the initializer. Every change is additive or guarded, and nothing else reads the new columns.
3. `catalog-api`'s stream carrying `label_ids` and `artist_ids`, then `analytics-engine`'s `COPY` listing both columns, then one complete rarity run.
4. The route that reads `label_ids` and `artist_ids` last. Before that run finishes, those lookups return empty groups.

### Feat

- **musicbrainz**: add a hash key beside the relationships natural key
- **musicbrainz**: key relationships on a hash instead of the eight-column constraint
- **postgres**: add label and artist ids to insights.release_rarity
- **postgres**: maintain the Identity Ledger per item instead of refreshing views

### Fix

- **postgres**: decide whether a statement is a no-op right before sending it
- **postgres**: set the schema init lock timeout with a bound parameter
- **postgres**: bound schema init's lock waits and skip no-op index and column statements
- **postgres**: order the rarity tier index release_id ascending, as the route does
- **postgres**: write an edition's rows under the snapshot that resolved its members
- **postgres**: guard the refresh's temporary table drops
- **postgres**: spell the ledger's guarded blocks as the schema's idempotence check reads them

### Perf

- **musicbrainz**: retire the redundant relationships source_mbid index
- **graph**: compute first_year set-based and index the move ledger's item keys
- **postgres**: index the rarity tier keyset and observations.owned_copy_id

## v0.6.3 (2026-11-04)

### Fix

- **graph**: key credits on one hash and drop their person hash indexes

## v0.6.2 (2026-11-04)

### Fix

- **graph**: make credit_role_category cheap per row

## v0.6.1 (2026-11-04)

### Fix

- **graph**: key free-text relations through a fixed-size hash

## v0.6.0 (2026-10-30)

### Feat

- **schema-init**: add pre-provisioned mode with verify-only checks

### Fix

- **deps**: upgrade msgpack past CVE-2026-114613

## v0.5.0 (2026-10-27)

### Feat

- **neo4j**: index per-category Person credit counts
- **postgres**: declare the Identity Ledger materialized views and identity_ledger_refresh()
- **postgres**: add public.identity_link_events
- **postgres**: record the extraction source on extraction_history
- **postgres**: retire the queue_metrics and service_health_metrics tables
- **postgres**: add insights.style_cotags
- **postgres**: add insights.style_landmark_masters
- **postgres**: add insights.genre_year_counts
- **postgres**: add style-lifecycle and atlas-layout insights tables
- **postgres**: index musicbrainz.releases on release_group_mbid
- **schema**: compact similar artists into release-keyed arrays
- **postgres**: add artist_similar_artists and artist_embedding_releases
- **graph**: add track-level credits and track performer relations
- **embeddings**: serve artist kNN via per-model_version partial HNSW indexes

### Fix

- **activity**: enforce event idempotency across partitions
- **graph**: publish collected and wants release_id as text
- **contracts**: correct stale table counts and key-column types in the persistence contract
- **deps**: upgrade filelock to 4.2.1 past CVE-2026-112940
- **deps**: upgrade certifi and opentelemetry past PYSEC-2026-4320 and CVE-2026-114603
- **ci**: adopt verified signed coverage workflow
- **deps**: update urllib3 lock to 2.8.0
- **graph**: unwrap tracklist's raw xmltodict shape and key tracks by ordinal
- **graph**: re-declare graph.catalog when its definition changed (0.4.1) (#9)

### Refactor

- **postgres**: move genre nodes out of style_atlas_layout into genre_atlas_sectors
- **postgres**: remove SQL PGQ activation

## v0.4.1 (2026-09-25)

### Fix

- **graph**: re-declare graph.catalog when its definition changed

## v0.4.0 (2026-09-25)

### Feat

- **graph**: publish gm_item_id on the four MusicBrainz vertices
- **identity**: add catalog item supersessions, move ledger, and resolution
- **postgres**: add the artist HNSW index and its build procedure
- **postgres**: add pgvector extension, artist embeddings, and pipeline role
- **pg19**: build pgvector onto the digest-pinned PG19 beta test image
- **loader**: declare durable derived-refresh job state
- **graph**: add bounded explore traversal
- **loader-latch**: add discogs_loader_extraction_latch table
- **graph**: add graph.find_shortest_path as a vertex-at-a-time search
- **graph**: add the per-vertex degree that orders frontier expansion
- **musicbrainz**: add updated_at to relationships and external_links
- **graph**: materialize the MEMBER_OF union across both provenances
- **spike**: provision the cloud mode on IBM Cloud with Terraform
- **spike**: add the measurement harness, local and cloud
- **spike**: expand one vertex at a time and stop at first touch
- **graph**: ship a bootstrap fill of every loader-owned relation
- **graph**: replace the phase 0 graph views with loader-written tables
- **postgres**: declare the catalog property graph behind a PostgreSQL 19 gate
- **ci**: add an advisory PostgreSQL 19 beta integration tier
- **postgres**: add person, company, and media graph relations
- **postgres**: add the graph schema of vertex and edge views
- **neo4j**: add Company constraint and Release.country index
- **postgres**: add GIN indexes on identifiers and companies blocks

### Fix

- **postgres**: add a rebuild mode and validate maintenance_work_mem
- **postgres**: gate vector extension creation on superuser, not just availability
- **graph**: align counters with normalized imports
- **tests**: remove integration container volumes
- **loader-latch**: rename to loader_extraction_latch, add loader discriminator
- **spike**: generate every table instead of merging reports by hand
- **spike**: name the borrowed harness by branch, not by repository
- **graph**: stop label_stats.release_count fanning out over its joins
- **graph**: carry the counters as properties of the labels Neo4j carries them on
- **spike**: stop the Neo4j harness overwriting the PostgreSQL timings
- **postgres**: skip the id widening when a view already reads the column
- **postgres**: rename user_account view to app_user
- **postgres**: gate the MusicBrainz id widening on the column type

### Docs

- Document `graph.find_shortest_path` and `graph.explore_traversal`, including their caps,
  shared temporary seen-set design, the mandatory Explore row limit, and the owner's decision
  to retain shortest-path parity through depth 10 despite the measured cost beyond depth 4.
- Record both procedural functions and the loader ownership of
  `graph.refresh_artist_member_of()` and `graph.refresh_vertex_degree()` in the additive
  `groovemap.persistence` v1 contract metadata.
- Document the graph schema's edge model, relation ownership, and the bootstrap fill in
  `docs/architecture.md`, `docs/runtime-configuration.md`, and the README, and complete the
  `groovemap.persistence` v1 contract metadata to match.
- **loader-latch**: correct the `extraction_latch` contract's `design_decision`, the
  `loader_extraction_latch` DDL comment, the schema test docstring, `contracts/README.md`,
  and `docs/architecture.md`, which still described `discogs-sql-loader` probing a two-name
  `LATCH_CANDIDATES` list and honouring the `loader` discriminator "when present." The
  loader now probes only `public.loader_extraction_latch`, requires `loader` via
  `REQUIRED_COLUMNS`, scopes every statement to `loader = 'discogs'`, and verifies via
  `pg_constraint` a PRIMARY KEY or UNIQUE constraint over exactly `(loader, version)` (a
  bare unique index is declined by design); `musicbrainz-sql-loader` will write
  `loader = 'musicbrainz'` rows into the same relation when it adopts it.

## v0.3.0 (2026-09-14)

### Feat

- add the insights.activity_summary table
- add the partitioned activity schema with immutability and consent
- add native identity tables and provider aliases
- add gm_id range indexes to the Neo4j catalog labels
- **telemetry**: trace store initialization and sample event-loop lag

### Fix

- **telemetry**: keep exception payloads off the schema_init spans
- **ci**: accept commitizen's no-eligible-commits bump-preview state
- **deps**: bump python to 3.14.7-slim and dockerfile frontend to 1.27 (#1)

### Refactor

- **automation**: normalize recipe dependency graph
- **schema**: centralize statement execution

## v0.2.0 (2026-09-04)

### Feat

- **postgres**: add media JSONB columns, GIN indexes, and rarity columns
- **neo4j**: add Medium and MediaFamily constraints and media indexes
- **telemetry**: adopt common.telemetry and record schema-init metrics

### Fix

- **ci**: use public python libraries
- **release**: record v0.1.2 verification

## v0.1.2 (2026-08-31)

### Fixed

- Republish the unchanged initializer after the organization release-action
  allowlist prevented the v0.1.1 workflow from starting.

## v0.1.1 (2026-08-31)

### Fixed

- Accept the explicit no-new-commits result when validating an already-released
  revision in CI.
- Accept the explicit pre-tag release gap while version metadata is ahead of
  the latest immutable tag.

## v0.1.0 (2026-08-31)

### Added

- Extract the Neo4j and PostgreSQL compatibility authority into a dedicated
  GrooveMap package.
- Restore the runnable one-shot database initializer and its container image.
- Validate package, image, release, provenance, and source-boundary contracts.
