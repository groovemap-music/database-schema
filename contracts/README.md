# Persistence contracts

See the repository [documentation index](../docs/README.md) for schema ownership and
runtime behavior.

`database-schema` owns Neo4j and PostgreSQL compatibility. Versioned metadata in
`persistence/` defines the policy; the executable definitions are packaged from
`src/groovemap_schema/` and named by each contract version.

Additive changes may remain within a contract version. Renames, removals, type changes,
constraint changes, or changed relationship semantics require an explicit migration and
major contract version. Use expand/migrate/contract ordering so independently deployed
services never require lockstep source checkouts.

The `graph` schema is recorded in
[`persistence/v1/compatibility.json`](persistence/v1/compatibility.json) under `graph_schema`
as an additive object of contract version 1: sixty-four relations — twenty-seven loader-written
tables and thirty-seven read-only views over tables the same contract already declares — plus
four functions. Four of those views exist so a vertex label can publish the counters
Neo4j carries on the node of the same name; `graph_schema.counter_properties` records which
label carries which. The contract lists every relation with its shape and the service
that writes it, because a consumer reading one needs both: the shape says whether an index is
available, and the owner says who to chase when the relation is empty. This repository writes
no graph row, so an empty table on a fresh database is the expected state.

Three of those functions render a shared vocabulary. The fourth, `graph.bootstrap_fill`,
is the one declared object that writes a graph row, and `graph_schema.bootstrap` records
it with `authority: none`. It derives every loader-written table from the catalog documents so
an environment can be populated once before a loader has run; **it is not authoritative and the
owner recorded against each relation supersedes it on that owner's first pass.** Nothing calls
it — applying the schema declares it and writes nothing — so an empty table on a fresh database
is still the expected state.

The views carry a stricter rule than the contract's general one. `CREATE OR REPLACE VIEW` may
only append a column, and PostgreSQL refuses to drop, rename, reorder, or retype one a view
already exposes, so appending is the only view change that ships on its own; every other
change needs a coordinated `DROP ... CASCADE` under the same expand/migrate/contract ordering.
A relation that changes shape is the exception the schema handles itself, through a guarded
migration that drops the view only when one is still there and creates the table in the same
pass. Every key column is `text`, which retired the four appended `<entity>_key` columns the
Discogs vertex views carried; the contract records that under `graph_schema.key_columns`.

The `graph.catalog` property graph is recorded there too, as an additive object that is
**conditional**: it exists only on a server reporting `server_version_num` of at least
`190000` and only when the `SCHEMA_PROPERTY_GRAPH` switch is enabled. On PostgreSQL 18, and on
19 with the switch off, the object is absent while every view it would be declared over is
still present. A consumer must therefore probe for it rather than assume it, and must be able
to fall back to the views. See
[the graph schema](../docs/architecture.md#graph-schema) for the projection and
[the property graph](../docs/architecture.md#property-graph) for the declaration and its gates.

`musicbrainz_delete_reconciliation` records the additive `updated_at` column on
`musicbrainz.relationships` and `musicbrainz.external_links` — the two MusicBrainz tables that,
unlike every other entity table, were declared with `created_at` only. `musicbrainz-sql-loader`
is the consumer: it refreshes `updated_at` on every upsert and reconciles deletes by removing
rows where `updated_at < run start`, the same `purge_stale_rows` shape `discogs-sql-loader`
already runs against the Discogs entity tables' own `updated_at`. DDL ownership stays with this
repository, so the column is declared here rather than carried as a startup `ALTER` in the
loader.

`extraction_latch` records `public.loader_extraction_latch`, the loader family's durable,
per-`(loader, extraction)` latch for a post-import derived-relation refresh: one row per
loader per extraction, recording which of that loader's entity types have signalled
`extraction_complete` and whether its refresh pass has completed (`refreshed_at`), written
before the triggering delivery is acked so a restart resumes collection. It mirrors
`graphinator`'s Neo4j-side latch and is declared here — matching the single relation
`discogs-sql-loader` probes (`public.loader_extraction_latch`), the required columns' types,
the mandatory `loader` discriminator (values `discogs` and `musicbrainz`) that
`REQUIRED_COLUMNS` demands, and the `loader_extraction_latch_pkey` primary key on `(loader,
version)` that `tableinator/extraction_latch.py`'s startup probe verifies via `pg_constraint`
(a PRIMARY KEY or UNIQUE constraint over exactly those two columns; a bare unique index is
declined by design) before its `ON CONFLICT` upsert — rather than as a runtime `CREATE TABLE`
in the loader, because DDL ownership stays with this repository. `discogs-sql-loader` is the
writer today (`loader = 'discogs'`); the relation is
named for the loader family, not for one loader, so `musicbrainz-sql-loader` can write its own
rows (`loader = 'musicbrainz'`) into the same relation when it adopts the pattern; see
`design_decision` in the contract. Storage-only: `loader_extraction_latch` is a plain `public`
table, not a `graph` schema relation, and is not a property-graph element.

Catalog event shapes are not owned here. Discogs events belong to
[`discogs-ingestion`](https://github.com/groovemap-music/discogs-ingestion), and MusicBrainz
events belong to
[`musicbrainz-ingestion`](https://github.com/groovemap-music/musicbrainz-ingestion). API shape
belongs to [`catalog-api`](https://github.com/groovemap-music/catalog-api). Live database
credentials, configuration, schema application order, and the released image digest belong to
[`deployment`](https://github.com/groovemap-music/deployment). Database driver and
connection-pool implementations belong to `groovemap-runtime` in
[`python-libraries`](https://github.com/groovemap-music/python-libraries); this repository
consumes that package at the version and commit recorded in each compatibility contract.

The former combined `catalog-ingestion` repository appears only in historical records. It is
not an active producer or contract owner.
