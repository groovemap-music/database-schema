# Database schema documentation

- [Initializer architecture](architecture.md) — repository and deployment ownership,
  source-specific producer boundaries, executable schema inventory, migrations, execution
  order, failure behavior, and the two real-engine
  [integration tiers](architecture.md#integration-tiers).
- [Runtime configuration](runtime-configuration.md) — database endpoints, credentials,
  secret files, TLS, logging, image behavior, and the metrics and spans the initializer emits.
- [Persistence compatibility contract](../contracts/persistence/) — schema evolution policy
  and the tested runtime-library boundary.
