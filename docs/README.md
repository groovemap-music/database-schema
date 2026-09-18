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
- [Spikes](spikes/) — time-boxed investigations and their verdicts. Each is a record of what
  was measured and decided, not a specification; the schema itself is documented above.
  - [GRAPH_TABLE read performance](spikes/gm-database-schema-9c8.1-graph-table-performance.md)
    — whether the graph views over JSONB can serve the hot read families, measured against
    materialized edge relations and against Neo4j.
