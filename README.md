# GrooveMap database schema

Versioned Neo4j and PostgreSQL schema initialization for GrooveMap. This repository owns
the schema definitions, runnable initializer, compatibility contracts, and
`ghcr.io/groovemap-music/database-schema` image. Deployment owns credentials and service
orchestration.

## Development

```bash
mise install
just setup
just check
just test-integration
```

The authoritative gate validates both schema families using in-memory fakes and static
compatibility rules. It never connects to or mutates a live database. Use `just test`,
`just build`, and `just install-check` separately as needed. `just test-integration`
requires Docker and starts disposable, loopback-only PostgreSQL and Neo4j containers. It
applies both production schema initializers twice, compares the schema catalogs after each
pass, and proves sentinel data survives; the containers and their volumes are removed on
exit. `just audit` intentionally uses network vulnerability data and is outside the fast
gate.

The real-engine proof runs as two tiers over one parameterized script. `just test-integration`
is the required tier and pins PostgreSQL 18; it must pass for a change to merge.
`just test-integration-pg19` is the advisory tier and pins a digest-identified
PostgreSQL 19 beta engine, reusing the same Neo4j image and the same test. CI runs the
required tier through the shared reusable workflow and the advisory tier as a separate,
non-required job that reports without blocking. See the
[integration tiers](docs/architecture.md#integration-tiers) for what each tier proves and
how the beta tier is promoted at PostgreSQL 19 general availability.

The PostgreSQL schema includes a `graph` schema: sixty-five relations re-presenting the catalog
as the vertex and edge relations the Neo4j enrichers build. Twenty-eight are tables the ingestion
loaders write and thirty-seven are read-only views; this repository declares all of them and
writes rows into none. `discogs-sql-loader` owns most of them, `musicbrainz-sql-loader` owns the
MusicBrainz half and co-owns the shared medium vocabulary, `catalog-api` owns the
personal-collection views, and `graph.release_degree` is split across `discogs-sql-loader` and
`catalog-api`; the contract names the owner of each relation individually. Every relation is
applied on every engine. The `graph.catalog` SQL/PGQ property graph declared over them is not —
`SCHEMA_PROPERTY_GRAPH` is off by default and, even when enabled, the declaration is skipped
with a logged reason on a server below PostgreSQL 19, so a consumer must probe for it rather
than assume it. See the [graph schema](docs/architecture.md#graph-schema) and
[the property graph](docs/architecture.md#property-graph).

`just check` expands to formatting, lint, type checking, coverage, compatibility and repository
checks, package/install verification, license checks, secret scanning, and a non-mutating
version-bump preview. `just image` builds and then verifies the local one-shot image;
`just release-dry-run` adds release-artifact validation. Schema application, version changes
(`just bump`), tagging, and publishing remain separate operations.

The built `groovemap-database-schema` wheel contains the initializer, Python definitions,
and versioned JSON compatibility contract. The container creates the configured PostgreSQL
database when needed and applies both schema families. See the
[runtime configuration](docs/runtime-configuration.md) for its environment and secret-file
interface, and its [telemetry section](docs/runtime-configuration.md#telemetry) for the metrics
and spans it emits: a `groovemap.schema_init.duration` measurement and a root
`schema_init {store}` span per store, the database spans and process metrics the shared runtime
contributes, and the event-loop lag histogram. Both signals export over OTLP/HTTP and are
flushed before the one-shot process exits.

The installed command exposes a local-only help and version surface without contacting a
database:

```bash
database-schema --help
database-schema --version
```

Running `database-schema` without an informational option applies the schemas. The command
returns zero only after PostgreSQL and Neo4j both succeed; deployment uses that process exit
status as its readiness dependency.

## Compatibility and runtime boundary

Additive changes may remain within a contract version. Renames, removals, type changes,
constraint changes, or changed relationship semantics require a new major contract and an
explicit migration. Rollouts follow expand, migrate consumers, then contract.

[`contracts/persistence/v1/compatibility.json`](contracts/persistence/v1/compatibility.json)
records the tested `groovemap-runtime` version and source revision, and records the `graph`
schema, its views, and the conditional `graph.catalog` property graph as additive objects of
contract version 1. The lockfile is the
machine-readable dependency authority. A deployment repository owns rollout and rollback.

## Versioning and release safety

PEP 621 metadata is the version authority. Commitizen uses annotated `v$version` tags.
`just bump-preview` is non-mutating, while `just bump` changes local version/changelog files
only. An approved `v*` tag publishes a provenance-attested, SBOM-enabled image to
`ghcr.io/groovemap-music/database-schema`; branch and scheduled runs never publish.

## License and history

The current tree is licensed under the [MIT License](LICENSE). The accompanying
[NOTICE](NOTICE) records the prior-license boundary. See the
[documentation index](docs/README.md) for architecture, runtime ownership, and configuration.
