# MusicBrainz schema research receipt index

This maintained index identifies the nonsecret evidence used by the
[MusicBrainz relational migration decision](../gm-database-schema-dgmd-musicbrainz-relational-migration.md).
The original receipts remain in the operator's durable coordination directory,
historically recorded as
`work/beadhive-inventory-2026-10-02/dispatch`. That machine-local directory is
an evidence origin, not a hosted repository link. The SHA-256 identities below
allow an authorized custodian to match the original files without copying raw
operator logs, private configuration, credentials, database rows, or source
bundles into this repository.

## Successor source and live inventory

| Receipt filename | SHA-256 | Maintained nonsecret facts and limits |
|---|---|---|
| `schema-successor-research-progress.json` | `40c1634a0200ddee9d0324a9fd8d702cd76a278c9fe6a8ca2f296e5a47ca1d33` | Pins published `v0.4.1` to `4f3efde3b6c9cb72e8ed9bce55635f611a8b3e44`, the SQL/PGQ-removal floor to `9a50949b1810f3e61adae2f89b86acec2e20c4a3`, and researched `main` to `4e9720d838c7da8a6bde139c64a69d781c0f67f0` with tree `a56fc2dd24ebf34e442ea3410f74c779aec87d0a`. Offline source enumeration found 327 unconditional PostgreSQL and 38 Neo4j statements. It records that full live comparison, both similar-artist table shapes, and an immutable successor remained unproven. |
| `update75-musicbrainz-live-store-compatibility-research.json` | `014ea43ad1f5e6e0ad754565985afcea6017855161f529dc145c6f58851a7058` | Metadata-only live research proves the named MusicBrainz columns and ordinary relational graph objects required by the new consumers were missing, while relevant legacy Neo4j identifiers were online. It does not compare all 327 PostgreSQL or 38 Neo4j statements and does not establish either similar-artist table's live shape. No mutation or message action was performed. |

These identities support the successor study's NO-GO. They do not prove full
live compatibility, authorize repair, or create a publishable successor.

## Maintenance controls and live state

| Receipt filename | SHA-256 | Maintained nonsecret facts and limits |
|---|---|---|
| `schema-maintenance-research-progress.json` | `1ab2af56c5a6c41327ab3f88f6fab04bb4a378ff6a3f845a8d1819b607246fff` | Records read-only inspection of repository and managed homelab source, the PostgreSQL backup's explicit `musicbrainz` exclusion, missing Neo4j recovery proof, missing writer-quiescence proof, and missing production capacity proof. Disposable tests proved `PGOPTIONS` propagation and an outer watchdog, but neither control was wired into managed execution. The repository-owned PostgreSQL 18 and Neo4j fixture passed 25 tests with 6 expected pgvector skips while applying the full schema twice. |
| `update77-native-capacity-recheck.json` | `3951ab08a9f7a200513c47f7a1d5e4dcf0e4f7f718eaeefe1c86d09694698ba4` | Records `13,052,424 KiB` available on the native validation machine. This is local Mac capacity only and gives no evidence of Lux database index, WAL, checkpoint, Neo4j population, log, or rollback headroom. |
| `update78-live-consumer-and-resend-prerequisite-recheck.json` | `bbe5981b4fb80a30537232fb403bbe96b2a153104f6ceb2d440e2b9cf5175416` | Records the legacy `brainztableinator:latest` and `brainzgraphinator:latest` containers healthy on their old images with zero restarts at observation time. The approved newer images were not deployed or started. No deployment, database, broker, message, or secret mutation was performed. |

These identities support the maintenance study's NO-GO and preserve the exact
proof boundary: disposable success is not production backup, quiescence,
timeout, recovery, or capacity evidence. Existing live-operation approvals are
unchanged.

## Independent review and merge

The summary receipt `update83-independent-research-review.json` has SHA-256
`2bbc7f5cd8841d29edba90d00f21ddbfaa56bf713cb65584ba321defd87aca18`.
It records the first pristine reviews, requested documentation corrections, and
the final green reviews:

| Study | First green run and result | Final commit | Final green run | Review gate | Epic merge |
|---|---|---|---|---|---|
| Successor `gm-database-schema-bvhb` | `run-4ce03ca548c98d28514592c5f4b03e15`; correction requested to distinguish new-consumer hold from healthy legacy consumers | `917dfba8b78cf81febe0252b2c9adbd6ebfa9705` | `run-6f9a2b59e19b488fad538f959f009673` | `gm-database-schema-x3fa`; approved | `dcd62aa6de229c1a89cc25816207fe29949ff907` |
| Maintenance `gm-database-schema-73xv` | `run-c4999c0952111842fb2bf456a8cae80a`; correction requested because local Mac capacity is not Lux capacity | `eb2faaa8b1d5dce963b458390cb2b665ab38a3c5` | `run-5f14dfa3e48e61fb273206d8ba81d12e` | `gm-database-schema-j4kl`; approved | `379dfac26d8a3d67cf3af5966791379facf1e80a` |

The transcript identities retained with the summary are:

| Receipt filename | SHA-256 | Recorded event |
|---|---|---|
| `update83-schema-successor-independent-review.log` | `3fe08bd84fb2e814b8214abf2a9b9c3dd55886ef91fc3f2a339a20e83dbcf490` | First pristine successor review; green run followed by changes requested. |
| `update83-schema-successor-final-review.log` | `e5b80edac881774c1171b8a92bf09a96260b55f0cdf17a952a0045e595d55556` | Corrected successor review at `917dfba`; final pristine run green. |
| `update83-schema-successor-merge.log` | `597dc2a55a305e7e9e19178f06161750ebac5cdb3808c6002d04416e8773612a` | Approved successor bead merged into the research epic. |
| `update83-schema-maintenance-independent-review.log` | `b22e6b9a64b10d12054dc05dac1f06203a2d6ab9be1ee66129c88ea63f27d1a4` | First pristine maintenance review; green run followed by changes requested. |
| `update83-schema-maintenance-final-review.log` | `228a28f075bf39cb33c4e8612e1d3dfdf8be7f89fee19a4c1ab758bd1465285d` | Corrected maintenance review at `eb2faaa`; final pristine run green. |
| `update83-schema-maintenance-merge.log` | `949cf6e4987230e88a1073d39361f92e88a066a4218b95285c9f93f04a0010ba` | Approved maintenance bead merged into the research epic. |

Review approval establishes that the two research documents accurately state
their evidence. It does not convert either NO-GO into GO or authorize
publication, live schema application, service changes, backup/restore actions,
or message operations.
