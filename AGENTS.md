# Repository instructions

- Treat Neo4j/PostgreSQL definitions as independently deployed compatibility contracts.
- Prefer additive changes; breaking changes require an explicit migration and rollback policy.
- Never connect to or mutate a live database from `just check` or CI.
- Keep database credentials and environment configuration out of this repository.
- Run `just check` before proposing a change.
- Tagging, publishing, releasing, and live schema application require separate approval.
