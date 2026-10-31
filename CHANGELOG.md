# Changelog

All notable changes to the GrooveMap database schema will be documented here.

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
