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
```

The authoritative gate validates both schema families using in-memory fakes and static
compatibility rules. It never connects to or mutates a live database. Use `just test`,
`just build`, and `just install-check` separately as needed. `just audit` intentionally
uses network vulnerability data and is outside the fast gate.

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
records the tested `groovemap-runtime` version and source revision. The lockfile is the
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
