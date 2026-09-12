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
