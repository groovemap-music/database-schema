# GrooveMap database schema

Versioned Neo4j and PostgreSQL schema definitions for GrooveMap. This repository is the
compatibility authority for constraints, indexes, tables, and migrations; it does not own
database credentials, environment configuration, or deployment orchestration.

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

The built `groovemap-database-schema` wheel contains the Python definitions and the
versioned JSON compatibility contract. Consumers call `create_neo4j_schema` and
`create_postgres_schema` with drivers/pools supplied by their deployment runtime.

## Compatibility and runtime boundary

Additive changes may remain within a contract version. Renames, removals, type changes,
constraint changes, or changed relationship semantics require a new major contract and an
explicit migration. Rollouts follow expand, migrate consumers, then contract.

[`contracts/persistence/v1/compatibility.json`](contracts/persistence/v1/compatibility.json)
records the tested `groovemap-runtime` version and immutable source commit. The optional
`runtime` dependency is private and pinned to that commit; it is not needed to validate or
build the schema definitions. A deployment repository owns live application and rollback.

## Versioning and release safety

PEP 621 metadata is the version authority. Commitizen uses annotated `v$version` tags.
`just bump-preview` is non-mutating, while `just bump` changes local version/changelog files
only. The release workflow builds checksums and a CycloneDX SBOM as short-lived workflow
artifacts; it does not publish a package or GitHub Release.

## License and history

The current tree is licensed under the [MIT License](LICENSE).
History was extracted from `SimplicityGuy/discogsography` by filtering only `main` for
`schema-init/`, `tests/schema-init/`, and `LICENSE`, then promoting the owned paths to the
repository root. The exact reproducible command and source commit are in
[`docs/extraction.md`](docs/extraction.md). The original monorepo remains unchanged.
