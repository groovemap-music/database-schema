# Database schema documentation

- [Initializer architecture](architecture.md) — repository and deployment ownership,
  source-specific producer boundaries, executable schema inventory, migrations, execution
  order, failure behavior, the two real-engine
  [integration tiers](architecture.md#integration-tiers), and the
  [graph schema](architecture.md#graph-schema) with its conditional
  [property graph](architecture.md#property-graph).
- [Runtime configuration](runtime-configuration.md) — database endpoints, credentials,
  secret files, TLS, logging, image behavior, and the metrics and spans the initializer emits.
- [Persistence compatibility contract](../contracts/persistence/) — schema evolution policy
  and the tested runtime-library boundary.
