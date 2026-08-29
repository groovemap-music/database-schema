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
interface.

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

The current tree is licensed under the [MIT License](LICENSE).
History was extracted from `SimplicityGuy/discogsography` by filtering only `main` for
`schema-init/`, `tests/schema-init/`, and `LICENSE`, then promoting the owned paths to the
repository root. The exact reproducible command and source commit are in
[`docs/extraction.md`](docs/extraction.md). The original monorepo remains unchanged. See
the [documentation index](docs/README.md) for runtime ownership and provenance.
