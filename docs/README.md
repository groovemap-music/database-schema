# Database schema documentation

- [Initializer architecture](architecture.md) — repository and deployment ownership,
  source-specific producer boundaries, executable schema inventory, migrations, execution
  order, failure behavior, the two real-engine
  [integration tiers](architecture.md#integration-tiers), and the
  [graph schema](architecture.md#graph-schema) with its non-authoritative
  [bootstrap fill](architecture.md#the-bootstrap-fill) and its conditional
  [SQL/PGQ removal and recovery](sql-pgq-removal.md).
- [Runtime configuration](runtime-configuration.md) — database endpoints, credentials,
  secret files, TLS, logging, image behavior, and the metrics and spans the initializer emits.
- [Persistence compatibility contract](../contracts/persistence/) — schema evolution policy
  and the tested runtime-library boundary.
- [Spikes](spikes/) — time-boxed investigations and their verdicts. Each is a record of what
  was measured and decided, not a specification; the schema itself is documented above.
  - [GRAPH_TABLE read performance](spikes/gm-database-schema-9c8.1-graph-table-performance.md)
    — whether the graph views over JSONB can serve the hot read families, measured against
    materialized edge relations and against Neo4j. Superseded by the SQL/PGQ removal.
  - [Cypher coverage and the target relational edge model](spikes/gm-database-schema-9c8.2-cypher-coverage.md)
    — whether every `catalog-api` query has a PostgreSQL spelling, and which loader writes each
    relation.
  - [Variable-length paths](spikes/gm-database-schema-9c8.3-variable-length-paths.md) — recursive
    CTE shortest path and bounded traversal. Superseded by the procedural pathfinder spike.
  - [Procedural pathfinder](spikes/gm-database-schema-gkt.1-procedural-pathfinder.md) — a
    vertex-at-a-time bidirectional shortest path and the bounded Explore traversal.
  - [Full-schema maintenance controls](spikes/gm-database-schema-73xv-schema-maintenance.md) —
    whether the full initializer can be admitted for the MusicBrainz production prerequisite.
  - [Schema successor source and live compatibility](spikes/gm-database-schema-bvhb-schema-successor.md)
    — which source can produce a supported successor release, and whether it is compatible with
    the live stores.
- [Design decisions](design/) — dated decision records.
  - [MusicBrainz relational migration decision](design/gm-database-schema-dgmd-musicbrainz-relational-migration.md)
    and its [research receipt index](design/evidence/gm-database-schema-a704-schema-research-receipts.md).
