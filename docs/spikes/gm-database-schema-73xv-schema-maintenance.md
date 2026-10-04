# Full-schema maintenance controls

## Question

Can the current full PostgreSQL and Neo4j schema initializer be admitted for the
MusicBrainz production prerequisite with bounded execution, a recoverable
pre-change backup, a defined writer-quiescence window, sufficient index/WAL
capacity, and an honest recovery path after interruption or partial DDL?

## Method

This spike inspected the initializer and both schema executors at the assigned
branch, the maintained local owner homelab source at
`73a2e18758fa96c5f0fed1aca3c0189f9b82c625`, and the durable update 75-77
operator receipts. The prior receipts bind the actual managed host to
`/opt/homelab` at `6f706edaa22ceff365fb098fc196a519d37b0617`
with 12 dirty entries that must be preserved. No remote file, database, service,
configuration, backup, or restore was changed by this spike.

Normal fixture admission was available because both repository-pinned images
were already cached and Docker reported no cleanup requirement. The repository's
owned disposable PostgreSQL 18 and Neo4j integration fixture was run once; that
fixture itself applies the complete schema twice and compares engine catalogs.
One additional owned PostgreSQL 18 container was used to observe session timeout
settings through the initializer's real connection-parameter builder, then
removed. A synthetic sleeping child process, with no store connection, exercised
an outer process watchdog.

Existing backup and restore definitions were read only. No backup, snapshot,
restore, live DDL, writer stop, message action, image pull, resource override, or
private export was performed.

## Evidence

### Full initializer behavior

`src/groovemap_schema/initializer.py` exposes only `--version`. It has no dry-run,
store selector, target-object selector, migration level, lock timeout, statement
timeout, or outer watchdog option. It ensures the PostgreSQL database exists and
then runs the complete PostgreSQL and Neo4j initializers concurrently with
`asyncio.gather`.

The PostgreSQL connection has a ten-second `connect_timeout`, which bounds only
connection establishment. `create_postgres_schema` switches the connection to
autocommit, executes the entire ordered statement list sequentially, catches and
counts each error, and continues with later statements. Neo4j follows the same
continue-after-error pattern for its full constraint/index list. The process
returns failure if either store has any failed statement, but that failure does
not undo earlier successful statements.

The required work includes ordinary, non-concurrent PostgreSQL indexes and
`ALTER TABLE` statements. Update 75 measured approximately 33.6 million rows in
`musicbrainz.relationships` and 15.0 million in
`musicbrainz.external_links`; both require new `updated_at` indexes. `ALTER
TABLE` takes `ACCESS EXCLUSIVE`, and ordinary `CREATE INDEX` conflicts with
writes. Catalog size identifies risk but does not prove duration.

### Bounded timeout and watchdog mechanisms

The source does not explicitly pass PostgreSQL session options, but libpq's
supported `PGOPTIONS` environment variable reaches the actual connection path.
Against an owned disposable PostgreSQL 18 instance, the result was:

```text
actual_connection_params: host=127.0.0.1 port=55473 dbname=groovemap
                          user=groovemap connect_timeout=10
session_lock_timeout:      750ms
session_statement_timeout: 2s
```

A parent `subprocess.run(..., timeout=0.5)` watchdog terminated an owned
synthetic 30-second child; observed elapsed time was 0.548 seconds. This proves
that a caller can enforce an outer deadline. It does not prove the managed
Compose service supplies `PGOPTIONS`, owns a deadline, or performs reviewed
post-timeout reconciliation. The actual managed definition supplies none of
these controls, while update 75 observed production `lock_timeout=0` and
`statement_timeout=0`.

Neo4j has no equivalent per-statement timeout in the initializer's current
driver calls. An outer watchdog would terminate both concurrently running store
tasks together and can leave either store partially advanced.

### Disposable two-run idempotence

`just test-integration` used the repository-owned, digest-pinned PostgreSQL 18
and Neo4j fixtures and completed with `25 passed, 6 skipped in 41.32s`. The test
`test_both_schema_initializers_are_real_engine_idempotent` applies both complete
schemas, snapshots their catalogs, inserts PostgreSQL and Neo4j sentinels,
applies both schemas again, and requires identical catalogs plus surviving
sentinels. The skips were the expected pgvector-only scenarios on the required
PostgreSQL 18 tier.

This proves two-run idempotence on empty/small synthetic stores. It does not
prove duration, lock exposure, WAL volume, or recovery on the production row
counts.

### Backup and restore evidence

The maintained homelab source defines daily logical PostgreSQL dumps and an
off-host rsync mirror. The managed target database is in the protected database list, but
the dump options deliberately exclude the entire `musicbrainz` schema. The
comments identify that exclusion as a prior operator decision based on measured
dump size. Therefore the ordinary backup cannot recover any of the six affected
MusicBrainz tables or their new columns/indexes.

A maintained `pg-restore-verify` job restores selected logical dumps into a
throwaway PostgreSQL container and publishes results. That is meaningful restore
evidence for content present in a dump. It cannot recover an excluded schema and
therefore cannot satisfy this migration prerequisite. This spike found no
maintained Neo4j backup artifact, restore rehearsal, snapshot identity, or
recovery-point receipt for this change. A database/image source rollback is not
a Neo4j data rollback.

No fresh pre-change PostgreSQL backup or Neo4j rollback point was created because
backup and restore execution were outside this research bead.

### Writer quiescence and capacity

The service graph names at least four writers: Discogs and MusicBrainz SQL
loaders write PostgreSQL, while their two graph enrichers write Neo4j. The
initializer depends on database health but has no gate that proves these writers
are stopped, transactions are drained, or queues are left untouched. The
maintained operator material describes stopping affected writers as a
prerequisite, but no reviewed, executable quiescence receipt exists for this
run.

Update 77's approximately 12.45 GiB free-space observation describes the local
Mac workspace used for native validation, not storage on the managed database
host. It supplies no production capacity evidence. No managed-host preflight
establishes safe floors for two large indexes, temporary index build space,
retained old/new index pages, PostgreSQL WAL and checkpoint pressure, Neo4j
index population, logs, and rollback artifacts. Actual managed index and WAL
headroom therefore remain unproven.

### Interruption and partial-DDL recovery

PostgreSQL autocommit makes each successful statement durable before the next
statement starts. A lock timeout, statement timeout, exception, container stop,
or outer watchdog can therefore leave a prefix of the full schema applied while
the executor continues after ordinary statement errors. Neo4j statements are
also individually committed. The process's nonzero exit is a failure signal,
not rollback.

Most statements are additive and use `IF NOT EXISTS`, so after identifying and
fixing an external blocker, a reviewed retry can converge. Recovery must first
inventory exact objects and invalid/in-progress indexes in both stores. Source
or consumer rollback retains successfully applied additive schema. If the
schema itself must be reversed, the only honest route is restoration from
separately proven PostgreSQL and Neo4j recovery points; ad hoc drops are not an
approved rollback.

## Verdict: NO-GO

The production migration does not have the required supported controls and
recoverable backup evidence. Timeout propagation and an outer watchdog work in
owned disposable fixtures, but the managed initializer does not configure them.
The maintained PostgreSQL backup explicitly omits `musicbrainz`; Neo4j recovery
is unproven; writer quiescence is unproven; and index/WAL capacity is unproven.
The successful two-run fixture cannot substitute for any of those production
gates.

## Recommendation

Do not run the full initializer on the managed stores, and do not start or
deploy the newly published consumers. Update 78 records that the legacy
consumers remain healthy on their old images; this recommendation does not call
for stopping them. Leave the currently successful additive schema unchanged.

Before a new execution authorization, require a reviewed operator runbook and
fresh receipts that:

1. identify exact recoverable pre-change PostgreSQL and Neo4j backup/snapshot
   points and show successful restore verification covering the MusicBrainz
   schema and the intended Neo4j database;
2. stop and verify every PostgreSQL and Neo4j writer, drain active transactions
   without consuming, replaying, or discarding messages, and define restart
   order;
3. inject supported PostgreSQL `lock_timeout` and `statement_timeout` settings,
   enforce a reviewed outer deadline covering both stores, and define the
   reconciliation procedure for timeout/interruption;
4. prove free-space, temporary index, WAL/checkpoint, and Neo4j index-population
   headroom against explicit floors;
5. bind the exact immutable schema image, managed target database selector,
   complete before/after object inventory,
   zero-exit first run, and zero-exit second run; and
6. treat rollback honestly: retain compatible additive schema when consumers
   roll back, or restore both independently verified store recovery points when
   schema rollback is actually required.

Adding these controls to source or managed configuration, creating backups, and
executing restore/DDL are separate implementation and operations work. They were
not authorized by this research bead.
