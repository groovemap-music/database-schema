# MusicBrainz relational migration decision

## Decision: NO-GO

The MusicBrainz relational migration is **NO-GO** as of 2026-10-04. A GO
requires both prerequisite studies to be proven, and both independently reviewed
studies remain NO-GO:

- [Schema successor source and live compatibility](../spikes/gm-database-schema-bvhb-schema-successor.md)
  finds current `main` at `4e9720d838c7da8a6bde139c64a69d781c0f67f0`
  to be the only defensible successor source, but the complete live compatibility
  of its 327 unconditional PostgreSQL statements and 38 Neo4j statements is not
  proven. The live shape or absence of the two similar-artist tables is also not
  proven, and no immutable successor has been published.
- [Full-schema maintenance controls](../spikes/gm-database-schema-73xv-schema-maintenance.md)
  finds that the managed PostgreSQL backup excludes the `musicbrainz` schema;
  no recoverable Neo4j rollback point is proven; writer quiescence and managed
  index/WAL capacity are unproven; and the disposable timeout/watchdog controls
  are not wired into the managed execution path.

This is a research decision only. It does not authorize publication, source or
managed-configuration changes, backups or restores, service stops or starts,
message operations, or live schema application. Existing approved live
operations retain their existing authorization.

## Evidence and review record

The successor study used the repository source, its
[source and inventory receipt](/Users/Robert/workspaces/github/work/beadhive-inventory-2026-10-02/dispatch/schema-successor-research-progress.json), and the
[live-store compatibility receipt](/Users/Robert/workspaces/github/work/beadhive-inventory-2026-10-02/dispatch/update75-musicbrainz-live-store-compatibility-research.json).
It preserves the original incident constraints and history: current `main`
descends from the SQL/PGQ removal floor `9a50949b1810f3e61adae2f89b86acec2e20c4a3`,
keeps Neo4j as graph-query authority, and retains the accepted compact
similar-artist storage merged at `b0208cc`. Published `v0.4.1` predates that
floor and is not an eligible migration source.

The maintenance study used repository source plus the actual managed homelab
source and durable operator receipts. Its
[control and backup evidence receipt](/Users/Robert/workspaces/github/work/beadhive-inventory-2026-10-02/dispatch/schema-maintenance-research-progress.json)
records the exact maintained source, backup exclusion, timeout experiment,
writer-control gap, and disposable validation. Its PostgreSQL 18 and Neo4j
fixture applied the full schema twice and passed `25` tests with the expected
`6` pgvector skips. The
[native capacity receipt](/Users/Robert/workspaces/github/work/beadhive-inventory-2026-10-02/dispatch/update77-native-capacity-recheck.json)
records about 12.45 GiB on the local Mac only; it supplies no Lux production
capacity evidence. The
[current consumer receipt](/Users/Robert/workspaces/github/work/beadhive-inventory-2026-10-02/dispatch/update78-live-consumer-and-resend-prerequisite-recheck.json)
shows the legacy `brainztableinator:latest` and `brainzgraphinator:latest`
containers healthy on their old images. The approved newer immutable consumer
images were cached and isolated-probed, but have not been deployed or started;
their schema prerequisite remains unmet.

Both studies received independent pristine-checkout review, correction,
resubmission, approval, and merge into epic `gm-database-schema-afan`. The
[independent review record](/Users/Robert/workspaces/github/work/beadhive-inventory-2026-10-02/dispatch/update83-independent-research-review.json)
binds the final successor study to
`917dfba8b78cf81febe0252b2c9adbd6ebfa9705` and merge
`dcd62aa6de229c1a89cc25816207fe29949ff907`, and the final maintenance
study to `eb2faaa8b1d5dce963b458390cb2b665ab38a3c5` and merge
`379dfac26d8a3d67cf3af5966791379facf1e80a`. The corresponding pristine
runs were green. Detailed review and merge transcripts are retained beside that
record as `update83-schema-successor-{independent-review,final-review,merge}.log`
and `update83-schema-maintenance-{independent-review,final-review,merge}.log`.

## Blocking prerequisites and owners

| Blocking proof | Exact owner | Evidence required to clear it |
|---|---|---|
| Complete successor compatibility | `database-schema` owner with an authorized PostgreSQL/Neo4j metadata operator | Fresh statement-by-statement comparison of every unconditional PostgreSQL and Neo4j object, the conditional capability/role branch, and proof that both similar-artist tables are absent or exactly match the compact current shape. An incompatible old shape requires a separately reviewed migration or repair. |
| Immutable successor | `database-schema` release owner | A normally approved release from `4e9720d` or a reviewed descendant preserving SQL/PGQ removal, Neo4j authority, and compact similar-artist storage; immutable image digest, provenance/SBOM, and publication receipts. |
| Recoverable pre-change state | PostgreSQL backup owner and Neo4j backup owner | Identified pre-change recovery points plus successful restore verification covering the PostgreSQL `musicbrainz` schema and the intended Neo4j database. The existing logical backup is insufficient because it excludes `musicbrainz`. |
| Writer quiescence | Managed-service operator, with the four PostgreSQL/Neo4j writer owners | Reviewed procedure and receipt proving every affected writer stopped, active transactions drained, and restart order defined without consuming, replaying, or discarding messages. |
| Bounded managed execution and reconciliation | `database-schema` owner and managed homelab operator | Supported PostgreSQL lock/statement timeouts, an outer deadline covering both stores, and an inventory/reconciliation procedure for interruption or partial DDL. `PGOPTIONS` and a watchdog are proven only in disposable tests and are absent from the managed path. |
| Production capacity | PostgreSQL and Neo4j storage operators | Fresh Lux measurements against explicit floors for index build workspace, retained pages, PostgreSQL WAL/checkpoint pressure, Neo4j index population, logs, and rollback artifacts. The local Mac capacity receipt cannot clear this gate. |

## Bounded next dependency DAG

This is a proposal for the next gated planning pass; it does not create or
authorize implementation work.

```text
A. Complete live compatibility inventory and independent review
                          ┐
B1. PostgreSQL + Neo4j recovery points and restore verification
B2. Writer quiescence and restart procedure
B3. Managed timeout, deadline, and partial-DDL reconciliation design
B4. Lux capacity preflight against explicit floors
                          ┘
                           ↓ all independently reviewed and green
C. Human-interactive replan and concrete authorization boundaries
                           ↓
D. Prepare, validate, and separately approve an immutable successor release
                           ↓
E. Two-run disposable full-schema validation of that exact image
                           ↓
F. Separately reviewed live application with before/after inventory and rollback gates
                           ↓
G. Separately approved deployment/start of the new SQL and graph consumers
```

`A` and `B1`-`B4` may proceed independently under their owners. No later node
may claim evidence from an earlier node until its receipts are independently
reviewed. A GO decision requires the successor and maintenance prerequisites to
be green together. Until then, do not publish or apply a successor through this
decision, do not deploy or start the new consumers, and do not stop the healthy
legacy consumers solely because of this research.
