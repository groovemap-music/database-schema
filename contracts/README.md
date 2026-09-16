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
as an additive object of contract version 1: fifty-two read-only views over tables the same
contract already declares, plus two rendered functions. Its views carry a stricter rule than
the contract's general one. `CREATE OR REPLACE VIEW` may only append a column, and PostgreSQL
refuses to drop, rename, reorder, or retype one a view already exposes, so appending is the
only view change that ships on its own; every other change needs a coordinated
`DROP ... CASCADE` under the same expand/migrate/contract ordering. The four appended
`<entity>_key` columns on the Discogs vertex views are part of that additive record and are
present on every engine.

The `graph.catalog` property graph is recorded there too, as an additive object that is
**conditional**: it exists only on a server reporting `server_version_num` of at least
`190000` and only when the `SCHEMA_PROPERTY_GRAPH` switch is enabled. On PostgreSQL 18, and on
19 with the switch off, the object is absent while every view it would be declared over is
still present. A consumer must therefore probe for it rather than assume it, and must be able
to fall back to the views. See
[the graph schema](../docs/architecture.md#graph-schema) for the projection and
[the property graph](../docs/architecture.md#property-graph) for the declaration and its gates.

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
