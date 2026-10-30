# GrooveMap database schema

Versioned Neo4j and PostgreSQL schema initialization for GrooveMap. This repository owns
the schema definitions, runnable initializer, compatibility contracts, and
`ghcr.io/groovemap-music/database-schema` image. Deployment owns credentials and service
orchestration.

## Who this is for

- **Schema contributors** change the Neo4j or PostgreSQL definitions here.
- **Service developers** (`catalog-api`, the loaders, `analytics-engine`) read the
  [compatibility contract](contracts/persistence/v1/compatibility.json) to see what they may rely on.
- **Operators** run the image once per deployment. See the
  [runtime configuration](docs/runtime-configuration.md).

## Quick start

```bash
mise install          # pins python, uv, just, and gitleaks
just setup            # installs dependencies from the lockfile
just check            # the full gate; never touches a live database
just test-integration # needs Docker; real PostgreSQL 18 and Neo4j
```

The installed command has a local-only help and version surface that does not contact a
database:

```bash
database-schema --help
database-schema --version
```

Running `database-schema` without an informational option applies the schemas. It returns zero
only after PostgreSQL and Neo4j both succeed; deployment uses that exit status as its readiness
dependency.

## Checks and tests

| Command | What it does |
| --- | --- |
| `just check` | Formatting, lint, type checking, coverage, compatibility and repository checks, package/install verification, license checks, secret scanning, and a non-mutating version-bump preview |
| `just test`, `just build`, `just install-check` | Individual pieces of `just check` |
| `just test-integration` | Required tier. Starts disposable, loopback-only PostgreSQL 18 and Neo4j containers |
| `just test-integration-pg19` | Advisory tier. Same script and Neo4j image against a digest-pinned PostgreSQL 19 beta |
| `just image` | Builds and verifies the local one-shot image |
| `just release-dry-run` | `just check` plus release-artifact validation |
| `just audit` | Network vulnerability audit; outside the fast gate on purpose |

`just check` validates both schema families with in-memory fakes and static compatibility
rules. It never connects to or mutates a live database. Schema application, version changes
(`just bump`), tagging, and publishing are separate operations.

The integration tiers apply both production schema initializers twice, compare the schema
catalogs after each pass, and prove sentinel data survives. The containers and their volumes
are removed on exit. The required tier must pass for a change to merge. CI runs it through the
shared reusable workflow, and runs the advisory tier as a separate, non-blocking job. See the
[integration tiers](docs/architecture.md#integration-tiers) for what each proves and how the
beta tier is promoted at PostgreSQL 19 general availability.

## What the initializer builds

The built `groovemap-database-schema` wheel contains the initializer, Python definitions, and
the versioned JSON compatibility contract. The container creates the configured PostgreSQL
database when needed and applies both schema families. See the
[runtime configuration](docs/runtime-configuration.md) for its environment and secret-file
interface, and its [telemetry section](docs/runtime-configuration.md#telemetry) for the
metrics and spans it emits: a `groovemap.schema_init.duration` measurement and a root
`schema_init {store}` span per store, the database spans and process metrics the shared runtime
contributes, and the event-loop lag histogram. Both signals export over OTLP/HTTP and are
flushed before the one-shot process exits.

### The `graph` schema

The PostgreSQL schema includes a `graph` schema: sixty-eight relations re-presenting the
catalog as the vertex and edge relations the Neo4j enrichers build. Thirty-one are tables the
ingestion loaders write and thirty-seven are read-only views. This repository declares all of
them and writes rows into none. Every relation is applied on every supported PostgreSQL engine.

| Owner | Relations |
| --- | --- |
| `discogs-sql-loader` | 38 |
| `musicbrainz-sql-loader` | 20 (the MusicBrainz half) |
| `discogs-sql-loader` and `musicbrainz-sql-loader` | 4, co-owned (the shared medium vocabulary) |
| `catalog-api` | 5 (the personal-collection views) |
| `discogs-sql-loader` and `catalog-api` | 1: `graph.release_degree`, split |

The contract names the owner of each relation individually. See the
[graph schema](docs/architecture.md#graph-schema) and the
[SQL/PGQ removal notes](docs/sql-pgq-removal.md).

## Compatibility and runtime boundary

Additive changes may remain within a contract version. Renames, removals, type changes,
constraint changes, or changed relationship semantics require a new major contract and an
explicit migration. Rollouts follow expand, migrate consumers, then contract.

[`contracts/persistence/v1/compatibility.json`](contracts/persistence/v1/compatibility.json)
records the tested `groovemap-runtime` version and source revision, and records the `graph`
schema and its relations as additive objects of contract version 1. The lockfile is the
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
