# Changelog

All notable changes to the GrooveMap database schema will be documented here.

## Unreleased

### Feat

- **loader-latch**: add `loader_extraction_latch` as the loader family's durable
  extraction-complete latch, keyed on `(loader, version)`, for `discogs-sql-loader`'s
  post-import counter refresh and reconciliation pass, matching the exact name, column
  types, and primary key its startup probe requires so DDL ownership moves here and
  `musicbrainz-sql-loader` can share the same relation
- **musicbrainz**: add `updated_at` to `musicbrainz.relationships` and
  `musicbrainz.external_links` as `musicbrainz-sql-loader`'s delete-reconciliation key

### Docs

- Document the graph schema's edge model, relation ownership, and the bootstrap fill in
  `docs/architecture.md`, `docs/runtime-configuration.md`, and the README, and complete the
  `groovemap.persistence` v1 contract metadata to match.
- **loader-latch**: correct the `extraction_latch` contract's `design_decision`, the
  `loader_extraction_latch` DDL comment, and the schema test docstring, which still
  described `discogs-sql-loader` probing a two-name `LATCH_CANDIDATES` list and honouring
  the `loader` discriminator "when present." The loader now probes only
  `public.loader_extraction_latch`, requires `loader` via `REQUIRED_COLUMNS`, scopes every
  statement to `loader = 'discogs'`, and verifies via `pg_constraint` a PRIMARY KEY or
  UNIQUE constraint over exactly `(loader, version)`; `musicbrainz-sql-loader` will write
  `loader = 'musicbrainz'` rows into the same relation when it adopts it.

### Fix

- **graph**: stop `graph.label_stats.release_count` fanning out over a label's `by_artist` and
  `in_genre` joins, so a release with several artists or genres counts once per label.

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
