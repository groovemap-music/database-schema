# Index, view and function audit, 2026-11-04

Bead: `gm-database-schema-wclt`. Schema read at `8391b9e`, which has the same DDL as v0.6.1.
v0.6.2, tagged while this audit ran, changes only `graph.credit_role_category` (finding 1).
Nothing was implemented here. The harness is in
[this folder](2026-11-04-index-view-function-audit/).

## Summary

**Indexes are not what makes the load slow.** At the rate lux held, about 500,000 releases an
hour, about 31 hours of load remain. Dropping every unread index would save about 1 hour of
that. The role function fix saves about 3.2 hours and is already merged. The other 27 or so
hours are not explained here, and one live read during the load would show where they go (see
[Live reads still wanted](#live-reads-still-wanted)).

The gains from the index work are in disk, WAL and the monthly reload:

| Finding | Evidence | Gain | State |
| --- | --- | --- | --- |
| 1. `graph.credit_role_category` cost 110 µs a row | Lux, 100,000 per-row calls: 11,013 ms | About 3.2 h of the load left | Fixed in `gm-database-schema-uz24`, v0.6.2 |
| 2. 38 indexes have no reader | Live scans plus a search of every consumer repository | 6.4 GB live, about 28 GB full scale | Proposed, B1 and B2 |
| 3. The v0.6.1 credit key is 2.3 times the size of the v0.6.0 key, and two hash indexes have no reader | 168 MB against 73 MB per million rows | 16 GB at 147M rows, 24% faster load | [Ready to file](#proposal-for-schema-v063-b1), B1 |
| 4. A reload rewrites every unchanged row | 196 µs and 6.5 KB of WAL a row, no HOT updates | About 64 min and 127 GB of WAL per reload of `releases` | Proposed, B3 |
| 5. `identity_ledger_refresh` repeats one sort per kind | Lux: density took 43.6 s to return 4 rows | Target 20 s at this scale | Proposed, B4 |
| 6. `first_year` reads every document once per tag | Lux: over 120 s, against 33.7 s set-based | At least 3.6 times | Proposed, B5 |

Three premises in the bead no longer hold:

- `graph.person` is a table, not a view. The `SELECT DISTINCT` over a full unnest survives only
  in `graph.bootstrap_fill()`, which no production path calls.
- The property graph is removed, not deferred. PostgreSQL 19 reverted SQL/PGQ, so it changes
  no verdict here (see [`sql-pgq-removal.md`](../sql-pgq-removal.md)). The rule that every edge
  table is indexed both ways now rests only on the path functions, and no consumer calls those
  today. Neo4j still answers path queries.
- The schema is 388 statements, not about 250.

## Datasets

Every number below comes from one of these three.

| Name | What it is | Limits |
| --- | --- | --- |
| **Lux** | Live PostgreSQL 18.6, read through the approved read-only wrapper, 2026-11-04 between 19:59 and 20:37 UTC, Postgres idle | Partial: 3.97M of 19.6M releases and 7.9M of 10.3M artists; labels and masters complete; MusicBrainz tables empty. Still the **v0.6.0** index shape. Counters date from 2026-10-31 and are almost entirely load traffic |
| **Local credits** | PostgreSQL 18.6 on a shared laptop, 1M synthetic credits in dump order with power-law names, 45 real role strings, one commit per 260 rows | Fully cached. Timings are medians of 3 unless stated; other containers were idle for those runs |
| **Local documents** | The same server, 300,000 synthetic release documents, one commit per 100 rows | Documents store 3.5 KB each against 2.5 KB on lux, so expression-index costs read somewhat high |

Local runs used a `max_wal_size` of 1 GB and a `shared_buffers` of 1 GB, to fit the disk limit.
Lux has 8 GB and 6 GB. `pg_stat_statements` is not installed on lux, so read evidence is scan
counters, `EXPLAIN`, and a text search of the consumer repositories.

**Scaling.** Partial timings are lower bounds. A statement that scans a table scales at least
linearly with its rows: multiply by 4.9 for releases, 3.5 for `provider_aliases`, 4.4 for
`track_credited_on`, and 2.8 for `credited_on`. Local costs are for a cached index. Once an index
outgrows `shared_buffers`, random inserts get slower, so treat them as lower bounds too.
Full-scale sizes use the dump counts where known (51.0M credits, 96.5M track credits,
24.4M track performers). Elsewhere they multiply by the release ratio, which overstates, because
low-numbered releases carry more credits: lux holds 4.5 credits per release against 2.6 overall.

## Findings

### 1. `graph.credit_role_category` was the most expensive thing per row

The function computes the stored `role_category` column on both credit tables. It was
`LANGUAGE sql` with a `FROM` clause, so it could not be inlined, and each call ran a 148-branch
`CASE` through the SQL-function executor.

| Measurement | Shipped | plpgsql twin |
| --- | ---: | ---: |
| Lux, 100,000 rows, per-row argument | 11,013 ms (110 µs a call) | not run |
| Local, 100,000 calls | 15,975 ms | 210 ms |
| Local, real insert path, 200,000 rows, heap only | 36.5 s | 3.2 s |

The twin matched the shipped function on all 596 vocabulary, compound and edge-case inputs, and
the shipped function matched Python's `categorize_role`.

This contradicts the 27 to 41 µs a row implied by the 137 s and 205 s loads in
[Keys over free text](../architecture.md#keys-over-free-text). Lux settles it at 110 µs.

Timing an `IMMUTABLE` function needs a per-row argument. The constant query first proposed for
this audit would have folded at plan time and shown 7 ms. The dispatcher caught that and ran
the per-row form.

`graph.text_key` is cheap, about 1.8 µs a call locally, because it inlines. `mb_relationship_type`
and `medium_label` have no `FROM` clause, so they should inline too; they were not timed.

### 2. 38 indexes have no reader

The [inventory](#index-inventory) lists them. They fall into four groups:

- **No query uses the access path.** `hash` on all four entity tables, the name and title
  indexes, GIN on labels, identifiers and companies, and the hash indexes on
  `credited_on.person_name`, `track_credited_on.person_name`, `credited_to.company_id`,
  `company.company_id` and `person.name`. `graph.same_as` is the only relation any consumer
  reads by name.
- **The consumer's filter cannot use the index.** With `enable_seqscan = off`, the `search_queries.py`
  filter shapes still scan for genres, media and year. `(c IS NULL OR c ?| ...)` has no GIN
  path for the `IS NULL` arm, and `(data->>'year')::int` does not match the indexed expression.
  Country does use its index, so it stays.
- **The planner prefers a scan.** The analytics credit read filters on `role_category` and
  matches 81% of rows, so lux plans a Seq Scan. `credited_on_role_category` has 0 scans, and
  the 102 on the track index read 0 tuples. `source` has one distinct value.
- **Left prefixes of a key.** These include `idx_mb_rels_source`, `idx_mb_links_mbid`,
  `idx_user_collections_user_id` and `idx_user_wantlists_user_id`.

**What this does not prove.** Zero scans on lux mostly reflects a load, not a week of read
traffic. A text search also cannot see SQL composed at run time. Both are reasons to re-read the
counters before dropping (B2). The MusicBrainz verdicts are static only, because those tables
are empty; dropping them before that load costs nothing. `person_name_trgm` and the hash indexes
are worth keeping if person reads move off Neo4j, and the
[note on that move](../architecture.md#keys-over-free-text) already names the index such a read
would need.

### 3. The v0.6.1 credit key is large, and random inserts make it larger

Per million `credited_on` rows, local credits:

| Shape | Load | Index size | WAL |
| --- | ---: | ---: | ---: |
| v0.6.0: primary key, reverse, role | 28.9 s | 201 MB | 502 MB |
| v0.6.1 as shipped | 37.5 s | 256 MB | 552 MB |
| v0.6.1 key only | 30.0 s | 168 MB | 355 MB |
| v0.6.1, hash and role built after the load | 30.7 s | 234 MB | 460 MB |
| **One combined hash**, key only | 29.9 s | 92 MB | 281 MB |
| One combined hash, with the hash and role indexes (1 run) | 33.1 s | 180 MB | 464 MB |
| Heap only, no key (1 run) | 9.2 s | 0 | 99 MB |

All runs used the fast role function, so the function does not mask index costs.

v0.6.1 stores two 32-byte hashes per row. One hash over a length-prefixed `person_name` and
`role` keeps the same uniqueness rule at about half the width. The direct comparison of the
proposed shape is in [the proposal](#proposal-for-schema-v063-b1). An earlier single run of the
key-only shape took 21.0 s; three repeats took 29.8 to 30.0 s, so that run was not representative
and is not used.

Building an index after the data is loaded packs it far tighter: the key was 96 MB against
168 MB, and the role index 31 MB against 53 MB. A `REINDEX` after the initial load should recover
about 40% of each. That is the cheap way to get this, because the alternative of deferring the
key is not available: the loader's document-scoped `DELETE` and its `ON CONFLICT` both need it.

### 4. A reload rewrites every unchanged row

`batch_writer.py` skips an unchanged document, then runs `UPDATE ... SET updated_at = NOW()`,
because the stale-row purge keys on `updated_at`. That column is indexed, so no update can be
HOT, and every index on the table gets a new entry. Lux shows `n_tup_upd = 0`, so this has not
run there yet. The first monthly reload will be the first time it does.

Per 300,000 unchanged rows, local documents:

| `releases` index set | Time | WAL | HOT updates |
| --- | ---: | ---: | ---: |
| As shipped, 13 indexes | 58.8 s | 1,951 MB | 0 |
| After B2, 5 indexes | 24.4 s | 401 MB | 0 |
| After B2, with `updated_at` as BRIN | 21.3 s | 389 MB | 20,364 (7%) |
| Primary key only | 3.8 s | 201 MB | 22,036 (7%) |

At 19.6M releases the shipped set costs about 64 minutes and 127 GB of WAL per reload, even if
nothing changed. B2 alone cuts that to about 26 minutes and 26 GB.

**BRIN does not solve this.** It removes the indexed-column block, as
[the documentation says](https://www.postgresql.org/docs/current/storage-hot.html), but HOT also
needs free space on the page, and a freshly loaded heap has none. Only 7% of updates went HOT.
Fillfactor would have to be near 50 to fit one new version of every row. The fix is to stop
writing the entity row, and record "seen in this extraction" somewhere narrow.

### 5. `identity_ledger_refresh` repeats one sort per kind

On lux, with 25.4M aliases:

| Statement | Time | Rows returned |
| --- | ---: | ---: |
| `identity_ledger_density` body | 43.6 s (release 18.3 s, artist 13.7 s) | 4 |
| `identity_ledger_edition_events` body | 11.5 s | not recorded |
| Last recorded whole refresh, 14:18 UTC | 45.1 s | |

Density dominates the refresh. Its `kind_alias` CTE sorts every alias of a kind for a window
function, once per kind, and spilled to temp files on lux. So the same table is scanned four
times to fill a view that holds four rows today. At about 89M aliases, expect at least 3.5 times
this, which is about 3 minutes per refresh, plus the diff that `CONCURRENTLY` runs.

### 6. `first_year` reads every document once per tag

`genre_stats` and `style_stats` find `first_year` by joining each tag edge to `graph.release`,
which reads and detoasts the document. A release with three tags is read three times.

| Lux, `first_year` for all 15 genres | Time |
| --- | ---: |
| Correlated body, as the loader runs it | Over 120 s (cancelled at the limit) |
| Set-based, each release read once | 33.7 s |

One genre shows the shape: `Blues` took 2.1 s for 62,401 edges. `graph.release` also adds a
`provider_aliases` probe per row, because the planner does not remove that `LEFT JOIN` even when
`gm_id` is unread. Lux shows the same on `graph.artist`, and that probe took about a third of the
`Blues` time. The cause is untested; the partial unique index is the likely reason. The bodies
can read `public.releases` directly, since none of them needs `gm_id`.

[The refresh budget note](https://github.com/groovemap-music/discogs-sql-loader/blob/main/docs/derived-refresh-ack-budget.md)
has this pass at about 2,056 s by linear illustration. This is the largest reducible part of it
that this audit measured.

### Hash indexes slow down on repeated values

A hash insert walks the bucket's overflow chain to find room, so its cost grows with the number
of duplicates of that value. A btree's does not.

| Duplicates of one value | Hash insert | Btree insert |
| ---: | ---: | ---: |
| 10,000 | 10.4 µs | 7.8 µs |
| 100,000 | 30.2 µs | 6.9 µs |
| 1,000,000 | 905.7 µs | 6.7 µs |

Lux statistics put `Traditional` at 0.40% of `track_credited_on`, heading for about 386,000 rows.
The average cost across all inserts stays low, because only the most repeated names pay it. This
is a reason not to keep these two indexes, not a load bottleneck. The 1M-duplicate run stopped
early, after 8.5 minutes, so 2M has no figure.

### Views and functions that need no change

- `graph.part_of` unnests every release and master, but nothing reads it, and `style_atlas.py`
  says it must not be an input. It costs nothing until someone queries it.
- `graph.release_degree` runs a regular expression and two counts per row, and has no reader.
- `graph.find_shortest_path`, `graph.explore_traversal` and `graph.bootstrap_fill` have no
  production caller. `catalog-api`'s `find_shortest_path` is its own Neo4j function.
- A common-term search for `live` took 582 ms for 63,086 hits on lux, using `idx_releases_fts`.
  Expect about 3 s at full scale, because `ts_rank` recomputes each hit. Worth watching, not yet
  worth changing.

### Statistics, bloat, BRIN and partitioning

- **Statistics are fresh.** Estimates are within 3% of live counts on the large tables.
- **Bloat is negligible today**, because nothing has been updated: `graph.person` has the most
  dead rows at 83,906. Finding 4 is where bloat would start.
- **BRIN:** `updated_at` has correlation 1.000 on all four entity tables, so BRIN suits the
  monthly purge once the touch is gone. It would take that index from 31 MB to under 1 MB on
  `releases` at this scale.
- **Partitioning:** nothing merits it. Graph writes are per document, every key would have to
  include the partition key, and the `activity` tables already have it.

### Missing indexes

Six foreign keys have no supporting index: `artifacts.created_by`, `owned_copies.artifact_id`,
`observations.owned_copy_id`, `catalog_item_moves.from_item_id` and `.to_item_id`, and
`extraction_history.triggered_by`. A delete on the referenced row scans the child table.
`observations.owned_copy_id` also cascades. These tables are small or empty, so the fix is cheap
now and the risk is low (B6).

## Load-time savings

For the 15.6M releases still to load, about 107.6M credit rows and 31 hours at lux's rate:

| Change | Rate used | Time saved | WAL saved |
| --- | ---: | ---: | ---: |
| Role function (done) | 108 µs a credit, lux | 3.2 h | none |
| Drop the entity indexes (B2) | 128 µs a release, 41% oversized documents | 33 min | 52 GB |
| Combined key, no hash indexes (B1) | 10.2 µs a credit, 5 pairs | 18 min | 19 GB |
| **Total** | | **About 4.1 h, 13% of what remains** | **About 71 GB** |

- The times are single-backend estimates built from laptop costs. They hold only if the loader
  writes one batch at a time, which is not confirmed.
- Index costs should grow as indexes outgrow lux's cache, which the cached local runs cannot
  show. The rate on lux did not fall over 8 hours, so that had not started by 3.97M releases.
- Deferring indexes is not worth a separate load mode. Deferring the credit hash and role indexes
  took 30.7 s against 37.5 s, and dropping them for good gets the same result. Deferring every
  index took 11.8 s, but the current writer needs its keys while it loads.

## Proposal for schema v0.6.3 (B1)

**Bead title:** Shrink the credit keys and drop the credit hash indexes before the first v0.6.x
apply.

**Why now:** lux is still on v0.6.0, so nothing has been built in the v0.6.1 shape. Changing it
later means rebuilding about 26 GB of key.

**The change**

1. Add an inlinable function, after `graph.text_key`:

   ```sql
   CREATE OR REPLACE FUNCTION graph.credit_key(person_name text, role text)
   RETURNS bytea LANGUAGE sql IMMUTABLE PARALLEL SAFE RETURNS NULL ON NULL INPUT
   AS $credit_key$
   SELECT graph.text_key(length(person_name)::text || ':' || person_name || role)
   $credit_key$
   ```

   The length prefix keeps `('ab', 'c')` and `('a', 'bc')` apart.

2. In `_FREE_TEXT_KEYS`, key `credited_on` on `release_id, graph.credit_key(person_name, role)`
   and `track_credited_on` on `release_id, track_ordinal, sub_track_ordinal,
   graph.credit_key(person_name, role)`.

3. Give those two relations no lookup index, so `credited_on_person_name_hash` and
   `track_credited_on_person_name_hash` are never created. `same_as_person_name_hash` stays: it
   serves the only `person_name` lookup any consumer runs.

No column changes, and no writer or reader changes. All writers use a target-less
`ON CONFLICT DO NOTHING`, and no repository names these indexes. The `role_category` indexes are
left to B2.

**Measured effect, per million `credited_on` rows.** Five interleaved pairs, with other
containers busy:

| Shape | Load, median | Range | Indexes | WAL |
| --- | ---: | ---: | ---: | ---: |
| v0.6.1: two-hash key, hash, role | 42.4 s | 36.8 to 44.7 s | 256 MB | 552 MB |
| Proposed: combined key, role | 31.9 s | 31.5 to 36.3 s | 146 MB | 377 MB |

The proposed shape won all five pairs, by a median of 10.2 s (24%). Index size and WAL were
identical on every run. The load effect is about 18 minutes over the 107.6M credit rows left.

> **Superseded by the final code, 2026-11-04.** `gm-database-schema-bfuh` re-measured this on the
> real tables with [`measure_credit_key.py`](2026-11-04-index-view-function-audit/measure_credit_key.py).
> The estimate for the track key below was too low: it measures 140 MB per million rows, not about
> 108 MB, because the disk table assumed only the ordinals added to it. So the saving is 13.7 GB
> (44%), from 31.5 GB to 17.8 GB, not 15.9 GB (52%), and 10.5 GB rather than 9.6 GB after a rebuild.
> WAL saved is about 27 GB. The paired load gain was 7.8 s on `credited_on` and 9.6 s on
> `track_credited_on`, in 9 of 10 pairs. [Keys over free text](../architecture.md#keys-over-free-text)
> holds the final figures.

**Disk at 147.5M credit rows**

| Key plus person hash | v0.6.1 | Proposed | After `REINDEX` |
| --- | ---: | ---: | ---: |
| `credited_on`, 51.0M rows | 10.1 GB | 4.5 GB | 2.8 GB |
| `track_credited_on`, 96.5M rows | 20.5 GB | 10.2 GB | 6.8 GB |
| **Total** | **30.6 GB** | **14.7 GB** | **9.6 GB** |

That saves 15.9 GB (52%), or 21.0 GB (69%) after a rebuild, plus 26 GB of WAL over a full load.
The `credited_on` figures are measured per-row costs at 1M rows. `track_credited_on` was not
measured; it adds 16 bytes of ordinals per key entry.

**Upgrade paths**

| Starting point | Path | Status |
| --- | --- | --- |
| Fresh database | `CREATE UNIQUE INDEX` with the new key | Normal initializer path |
| v0.6.0, which includes lux | The existing primary-key migration builds the new key instead of the old one, and no hash index | Same template, new key text. Rerun the interrupted-upgrade test against it |
| v0.6.1 or v0.6.2, test and local only | The guarded statement below, once per relation, after `graph.credit_key` exists | Tested on 1M rows |

`CREATE UNIQUE INDEX IF NOT EXISTS` will not replace an index of the same name, so a database
already on v0.6.1 needs this statement. The new index is built first, so the relation always has
a uniqueness rule:

```sql
DO $rekey_credit_credited_on$
BEGIN
    IF pg_get_indexdef(to_regclass('graph.credited_on_key')) LIKE '%text_key(role)%' THEN
        CREATE UNIQUE INDEX credited_on_key_v2 ON graph.credited_on
            (release_id, graph.credit_key(person_name, role));
        DROP INDEX graph.credited_on_key;
        ALTER INDEX graph.credited_on_key_v2 RENAME TO credited_on_key;
    END IF;
    DROP INDEX IF EXISTS graph.credited_on_person_name_hash;
END
$rekey_credit_credited_on$
```

On a 1M-row table in the v0.6.1 shape, tested with
[`mig.sql`](2026-11-04-index-view-function-audit/mig.sql):

- The migration took 1.75 s, which suggests about 5 minutes at 147.5M rows. Allow more, because
  a build that outgrows memory is slower. Run it with the loaders stopped.
- A second run did nothing.
- A transaction that fails after the drop left the old key in place.
- Indexes went from 256 MB to 109 MB, because a rebuilt key is packed: 56 MB.
- The natural key held: pairs that concatenate alike stayed two rows, a target-less
  `ON CONFLICT DO NOTHING` still deduped, a plain duplicate raised, and a 5,000-character value
  stored whole.
- The document `DELETE` still used the key.

**Rollback:** code only. An older service image works unchanged. An older schema-init also
works, because its `IF NOT EXISTS` skips the key and it only recreates the hash index. Restoring
the v0.6.1 key means the same swap in reverse, which took 4.75 s at 1M rows with the hash index.

**Cost to accept:** a person-led read of `credited_on` becomes a sequential scan. No consumer
runs one. If person queries move off Neo4j, add `(graph.text_key(person_name), release_id)`, as
[the architecture note](../architecture.md#keys-over-free-text) already says.

**Files to change**

- `src/groovemap_schema/postgres.py`: the function, `_FREE_TEXT_KEYS`, and the helpers that assume
  a lookup column.
- `contracts/persistence/v1/compatibility.json`, `graph_schema.free_text_keys`, and
  `scripts/check-contracts.py`.
- `docs/architecture.md`, "Keys over free text", whose tables and hash-index text change.
- `tests/integration/test_free_text_keys.py:255`, `tests/test_identity_schema.py:366` and `:373`,
  and `tests/test_graph_schema.py:802`.

**Acceptance:**

- The key is at or below 95 MB per million rows, and the shipped shape at or below 380 MB of WAL.
- The load is at least 20% faster than v0.6.1 on the same data.
- `just check` and both integration tiers pass, and the v0.6.1 upgrade is tested interrupted and
  converged.

## Follow-up beads

None is filed yet. Each has a measured target and an upgrade path, and the schema initializer
runs with the loaders stopped for all of them.

| Bead | Change | Measured target | Upgrade path |
| --- | --- | --- | --- |
| **B1** P1 | Combined credit key, no credit hash indexes. Schema v0.6.3, before lux applies v0.6.x | See [the proposal](#proposal-for-schema-v063-b1) | See the proposal |
| **B2** P2 | Drop the other 36 indexes marked `drop` | 6.4 GB live and about 23 GB full scale; reload WAL at or below 25% of today's | First re-read `pg_stat_all_indexes` after a week of read traffic, and keep anything scanned. Then `DROP INDEX IF EXISTS` guarded statements in the initializer. Rollback restores the statement and rebuilds |
| **B3** P2 | Stop rewriting unchanged rows. Spans `database-schema`, `discogs-sql-loader` and `musicbrainz-sql-loader` | Reload of an unchanged row at or below 0.5 KB of WAL, from 6.5 KB; purge still removes exactly the stale rows | Add a narrow seen-in-extraction relation (additive), move the writers, then move the purge, then change `updated_at` to BRIN. Old loaders keep working until the last step |
| **B4** P2 | Fill the density view in one pass over `provider_aliases` | Density at or below 20 s at 25.4M aliases, from 43.6 s, with identical rows | Guarded drop and recreate, as the ledger's "Changing a definition" note requires. Views are unpopulated until the next refresh. The integration fixture pins the rows |
| **B5** P2 | Compute `first_year` set-based, and read `public.releases` directly. Change `_COUNTER_BOOTSTRAP` and `discogs-sql-loader`'s `COUNTER_BODIES` together | All-genre `first_year` at or below 35 s at 3.97M releases, from over 120 s, with identical values | Function and loader text only, no table change. Deploy in either order |
| **B6** P3 | Index the six foreign keys | Delete on a referenced row uses an index | `CREATE INDEX IF NOT EXISTS` on small tables |
| **B7** P3 | `REINDEX` the credit keys after the initial load, in the `deployment` runbook | About 40% smaller keys | Operator step after the load. `CONCURRENTLY` allows reads throughout |
| **B8** P3 | Enable `pg_stat_statements` and `track_functions` in `deployment` | Query-level evidence for the next audit | Needs `shared_preload_libraries` and a restart, so pair it with a maintenance window |

## Live reads still wanted

1. **During the load:** sample `pg_stat_activity` wait events and active queries for a few
   minutes. This is the one read that would explain the 27 hours.
2. **After the load:** repeat the size and scan queries, for real sizes on the shipped shape.
3. **After a week of read traffic:** repeat the scan query. B2 depends on it.

## Index inventory

Sorted by verdict, then size. Live size and scans are lux's v0.6.0 shape; `—` means the index
does not exist there yet. `retired` indexes exist on lux and v0.6.1 replaces them. The
per-partition copies of the `activity` indexes are left out; all are empty. Full-scale sizes are
estimates, and the v0.6.1 keys use the local bytes per row.

| Relation | Index | Type | Live size | Scans | Full-scale est. | Verdict | Why |
| --- | --- | --- | ---: | ---: | ---: | --- | --- |
| `graph.track_credited_on` | `track_credited_on_role_category` | btree | 1.2 GB | 102 | 5.2 GB | drop | no plan reads it; 102 scans read 0 tuples |
| `public.artists` | `idx_artists_hash` | btree | 942 MB | 0 | 1.2 GB | drop | no lookup by hash anywhere; the loader reads hash through data_id |
| `public.releases` | `idx_releases_identifiers` | gin | 824 MB | 0 | 4.0 GB | drop | no containment query; reattach reads a deeper path by primary key |
| `graph.track_by_artist` | `track_by_artist_reverse` | btree | 520 MB | 0 | 1.6 GB | drop | 0 scans; analytics reads whole relation; not a path relation |
| `public.releases` | `idx_releases_hash` | btree | 472 MB | 0 | 2.3 GB | drop | no lookup by hash anywhere |
| `public.artists` | `idx_artists_name` | hash | 340 MB | 0 | 442 MB | drop | no `data->>name` equality in any repo |
| `graph.credited_on` | `credited_on_role_category` | btree | 316 MB | 0 | 2.6 GB | drop | analytics plan is a Seq Scan, 81% of rows match |
| `public.masters` | `idx_masters_hash` | btree | 310 MB | 0 | 310 MB | drop | no lookup by hash anywhere |
| `public.releases` | `idx_releases_companies` | gin | 300 MB | 0 | 1.4 GB | drop | no reader |
| `public.labels` | `idx_labels_hash` | btree | 291 MB | 0 | 291 MB | drop | no lookup by hash anywhere |
| `public.releases` | `idx_releases_labels` | gin | 264 MB | 0 | 1.3 GB | drop | no reader |
| `public.releases` | `idx_releases_title` | hash | 139 MB | 0 | 685 MB | drop | no `data->>title` equality |
| `graph.person` | `person_name_trgm` | gin | 138 MB | 0 | 386 MB | drop | no reader; autocomplete is on Neo4j |
| `public.labels` | `idx_labels_name` | hash | 127 MB | 0 | 127 MB | drop | no `data->>name` equality in any repo |
| `public.masters` | `idx_masters_title` | hash | 124 MB | 0 | 124 MB | drop | no `data->>title` equality |
| `graph.credited_to` | `credited_to_source` | btree | 90 MB | 20 | 443 MB | drop | one distinct value; deletes go through the key |
| `graph.issued_on` | `issued_on_source` | btree | 42 MB | 20 | 208 MB | drop | one distinct value; deletes go through the key |
| `public.releases` | `idx_releases_year` | btree | 25 MB | 1 | 122 MB | drop | consumer casts `::int`, so it cannot match (finding 2) |
| `public.masters` | `idx_masters_year` | btree | 18 MB | 0 | 18 MB | drop | consumer casts `::int`, so it cannot match (finding 2) |
| `public.releases` | `idx_releases_genres` | gin | 12 MB | 0 | 60 MB | drop | consumer filter `IS NULL OR ?|` cannot use it (finding 2) |
| `public.releases` | `idx_releases_media_families` | gin | 8 MB | 0 | 41 MB | drop | consumer filter `IS NULL OR ?|` cannot use it (finding 2) |
| `musicbrainz.releases` | `idx_mb_releases_media_families` | gin | <1 MB | 0 | <1 MB | drop | no reader |
| `public.user_collections` | `idx_user_collections_media_families` | gin | <1 MB | 0 | <1 MB | drop | no filter on it; reads select by user |
| `insights.activity_summary` | `idx_activity_summary_date` | btree | <1 MB | 1 | <1 MB | drop | primary key scans backward |
| `insights.genre_trends` | `idx_genre_trends_genre` | btree | <1 MB | 0 | <1 MB | drop | left prefix of the primary key |
| `musicbrainz.artists` | `idx_mb_artists_name` | hash | <1 MB | 0 | <1 MB | drop | no reader |
| `musicbrainz.external_links` | `idx_mb_links_mbid` | btree | <1 MB | 0 | <1 MB | drop | left prefix of the unique key |
| `musicbrainz.external_links` | `idx_mb_links_service` | btree | <1 MB | 0 | <1 MB | drop | no filter on it |
| `musicbrainz.labels` | `idx_mb_labels_name` | hash | <1 MB | 0 | <1 MB | drop | no reader |
| `musicbrainz.relationships` | `idx_mb_rels_source` | btree | <1 MB | 0 | <1 MB | drop | left prefix of `relationships_natural_key` |
| `musicbrainz.relationships` | `idx_mb_rels_type` | btree | <1 MB | 0 | <1 MB | drop | no filter on it |
| `public.user_collections` | `idx_user_collections_user_id` | btree | <1 MB | 0 | <1 MB | drop | left prefix of the unique key |
| `public.user_wantlists` | `idx_user_wantlists_user_id` | btree | <1 MB | 0 | <1 MB | drop | left prefix of the unique key |
| `graph.company` | `company_company_id_hash` | hash | — | — | — | drop | no reader; `company_key` enforces the key |
| `graph.credited_on` | `credited_on_person_name_hash` | hash | — | — | 1.7 GB | drop | no person-led read; costly on repeated names (B1) |
| `graph.credited_to` | `credited_to_company_id_hash` | hash | — | — | — | drop | no company-led read |
| `graph.person` | `person_name_hash` | hash | — | — | — | drop | no reader; `person_key` enforces the key |
| `graph.track_credited_on` | `track_credited_on_person_name_hash` | hash | — | — | 3.2 GB | drop | no person-led read; costly on repeated names (B1) |
| `public.artists` | `idx_artists_updated_at` | btree | 62 MB | 1 | 81 MB | change | BRIN, after B3 |
| `public.releases` | `idx_releases_updated_at` | btree | 31 MB | 0 | 154 MB | change | BRIN, after B3; correlation 1.000 |
| `public.masters` | `idx_masters_updated_at` | btree | 20 MB | 1 | 20 MB | change | BRIN, after B3 |
| `public.labels` | `idx_labels_updated_at` | btree | 19 MB | 1 | 19 MB | change | BRIN, after B3 |
| `musicbrainz.external_links` | `idx_mb_links_updated_at` | btree | <1 MB | 0 | <1 MB | change | BRIN, after B3 |
| `musicbrainz.relationships` | `idx_mb_rels_updated_at` | btree | <1 MB | 0 | <1 MB | change | BRIN, after B3 |
| `graph.credited_on` | `credited_on_key` | btree | — | — | 8.4 GB | change | one combined hash over person and role (B1) |
| `graph.track_credited_on` | `track_credited_on_key` | btree | — | — | 17.3 GB | change | one combined hash over person and role (B1) |
| `graph.track_credited_on` | `track_credited_on_pkey` | btree | 2.4 GB | 21,814,492 | 10.5 GB | retired | v0.6.1 replaces it |
| `graph.track_credited_on` | `track_credited_on_reverse` | btree | 2.0 GB | 31,832 | 8.9 GB | retired | v0.6.1 replaces it |
| `graph.credited_on` | `credited_on_pkey` | btree | 1.4 GB | 18,106,360 | 4.0 GB | retired | v0.6.1 replaces it |
| `graph.credited_on` | `credited_on_reverse` | btree | 1.1 GB | 20,740 | 3.2 GB | retired | v0.6.1 replaces it |
| `graph.credited_to` | `credited_to_pkey` | btree | 948 MB | 9,920,813 | 4.6 GB | retired | v0.6.1 replaces it |
| `graph.credited_to` | `credited_to_reverse` | btree | 399 MB | 0 | 1.9 GB | retired | v0.6.1 replaces it |
| `graph.same_as` | `same_as_pkey` | btree | 151 MB | 24,534,834 | 422 MB | retired | v0.6.1 replaces it |
| `graph.person` | `person_pkey` | btree | 128 MB | 25,051,274 | 359 MB | retired | v0.6.1 replaces it |
| `graph.same_as` | `same_as_reverse` | btree | 83 MB | 0 | 233 MB | retired | v0.6.1 replaces it |
| `graph.company` | `company_pkey` | btree | 14 MB | 6,452,741 | 40 MB | retired | v0.6.1 replaces it |
| `public.provider_aliases` | `idx_provider_aliases_provider_entity_kind_external_id` | btree | 1.7 GB | 44,535,211 | 5.9 GB | keep | identity lookup and ON CONFLICT target |
| `public.provider_aliases` | `provider_aliases_pkey` | btree | 765 MB | 0 | 2.6 GB | keep | enforces a key |
| `public.provider_aliases` | `idx_provider_aliases_native_id` | btree | 668 MB | 34 | 2.3 GB | keep | ledger and merge reads |
| `graph.track_by_artist` | `track_by_artist_pkey` | btree | 639 MB | 7,930,086 | 1.9 GB | keep | enforces a key |
| `public.catalog_items` | `catalog_items_pkey` | btree | 510 MB | 37 | 1.2 GB | keep | enforces a key |
| `graph.in_style` | `in_style_pkey` | btree | 415 MB | 6,884,723 | 2.0 GB | keep | enforces a key |
| `graph.in_style` | `in_style_reverse` | btree | 394 MB | 298 | 1.9 GB | keep | style_stats |
| `graph.issued_on` | `issued_on_pkey` | btree | 309 MB | 3,998,635 | 1.5 GB | keep | enforces a key |
| `graph.in_genre` | `in_genre_pkey` | btree | 308 MB | 5,211,878 | 1.5 GB | keep | enforces a key |
| `public.artists` | `artists_pkey` | btree | 295 MB | 7,959,296 | 383 MB | keep | enforces a key |
| `graph.in_genre` | `in_genre_reverse` | btree | 273 MB | 86 | 1.3 GB | keep | genre_stats |
| `graph.issued_on` | `issued_on_reverse` | btree | 240 MB | 284,816 | 1.2 GB | keep | used during load, 284,816 scans |
| `public.artists` | `idx_artists_gm_item_id` | btree | 239 MB | 0 | 310 MB | keep | catalog_merge recompute |
| `graph.master_in_style` | `master_in_style_pkey` | btree | 234 MB | 4,137,906 | 234 MB | keep | enforces a key |
| `graph.by_artist` | `by_artist_pkey` | btree | 229 MB | 4,559,042 | 1.1 GB | keep | enforces a key |
| `graph.master_in_style` | `master_in_style_reverse` | btree | 224 MB | 0 | 224 MB | keep | reverse walk: counters, deletes, or the path functions |
| `graph.on_label` | `on_label_pkey` | btree | 222 MB | 4,868,596 | 1.1 GB | keep | enforces a key |
| `graph.master_in_genre` | `master_in_genre_pkey` | btree | 202 MB | 3,482,227 | 202 MB | keep | enforces a key |
| `graph.master_in_genre` | `master_in_genre_reverse` | btree | 189 MB | 71 | 189 MB | keep | reverse walk: counters, deletes, or the path functions |
| `graph.by_artist` | `by_artist_reverse` | btree | 186 MB | 1 | 918 MB | keep | reverse walk: counters, deletes, or the path functions |
| `graph.on_label` | `on_label_reverse` | btree | 186 MB | 1 | 915 MB | keep | label_stats |
| `graph.alias_of` | `alias_of_pkey` | btree | 182 MB | 4,345,915 | 237 MB | keep | enforces a key |
| `graph.alias_of` | `alias_of_reverse` | btree | 173 MB | 27,585 | 225 MB | keep | loader document delete, 27,585 scans |
| `public.artists` | `idx_artists_fts` | gin | 171 MB | 30 | 223 MB | keep | /api/search |
| `graph.master_by_artist` | `master_by_artist_pkey` | btree | 158 MB | 3,201,858 | 158 MB | keep | enforces a key |
| `public.releases` | `releases_pkey` | btree | 146 MB | 3,993,355 | 721 MB | keep | enforces a key |
| `graph.derived_from` | `derived_from_pkey` | btree | 139 MB | 2,745,204 | 687 MB | keep | enforces a key |
| `graph.master_by_artist` | `master_by_artist_reverse` | btree | 130 MB | 0 | 130 MB | keep | reverse walk: counters, deletes, or the path functions |
| `public.releases` | `idx_releases_gm_item_id` | btree | 120 MB | 0 | 591 MB | keep | catalog-api lookup_queries and catalog_merge |
| `graph.derived_from` | `derived_from_reverse` | btree | 107 MB | 6 | 528 MB | keep | reverse walk: counters, deletes, or the path functions |
| `graph.member_of` | `member_of_reverse` | btree | 98 MB | 0 | 128 MB | keep | reverse walk: counters, deletes, or the path functions |
| `graph.member_of` | `member_of_pkey` | btree | 95 MB | 4,131,450 | 124 MB | keep | enforces a key |
| `public.masters` | `masters_pkey` | btree | 95 MB | 2,619,082 | 95 MB | keep | enforces a key |
| `public.labels` | `labels_pkey` | btree | 87 MB | 2,440,071 | 87 MB | keep | enforces a key |
| `public.masters` | `idx_masters_gm_item_id` | btree | 79 MB | 0 | 79 MB | keep | catalog_merge recompute |
| `public.releases` | `idx_releases_fts` | gin | 76 MB | 60 | 374 MB | keep | /api/search; reachable, 60 scans |
| `public.labels` | `idx_labels_gm_item_id` | btree | 73 MB | 0 | 73 MB | keep | catalog_merge recompute |
| `public.masters` | `idx_masters_fts` | gin | 65 MB | 40 | 65 MB | keep | /api/search |
| `public.labels` | `idx_labels_fts` | gin | 61 MB | 30 | 61 MB | keep | /api/search |
| `public.releases` | `idx_releases_country` | btree | 25 MB | 0 | 124 MB | keep | reachable by the search filter (finding 2) |
| `graph.style` | `style_name_trgm` | gin | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `graph.style` | `style_pkey` | btree | <1 MB | 2,592,962 | <1 MB | keep | enforces a key |
| `graph.genre` | `genre_name_trgm` | gin | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `graph.genre` | `genre_pkey` | btree | <1 MB | 195,661 | <1 MB | keep | enforces a key |
| `graph.media_family` | `media_family_pkey` | btree | <1 MB | 47,487 | <1 MB | keep | enforces a key |
| `graph.medium` | `medium_pkey` | btree | <1 MB | 83,425 | <1 MB | keep | enforces a key |
| `insights.computation_log` | `computation_log_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `insights.computation_log` | `idx_computation_log_type_started` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `insights.data_completeness` | `data_completeness_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.admin_audit_log` | `admin_audit_log_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.admin_audit_log` | `idx_admin_audit_log_admin_id` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.admin_audit_log` | `idx_admin_audit_log_created_at` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.identity_ledger_density` | `idx_identity_ledger_density_key` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.identity_ledger_refresh_state` | `identity_ledger_refresh_state_pkey` | btree | <1 MB | 4 | <1 MB | keep | enforces a key |
| `public.loader_derived_refresh_cursor` | `loader_derived_refresh_cursor_pkey` | btree | <1 MB | 16,162 | <1 MB | keep | enforces a key |
| `public.users` | `users_email_key` | btree | <1 MB | 2 | <1 MB | keep | enforces a key |
| `public.users` | `users_pkey` | btree | <1 MB | 9 | <1 MB | keep | enforces a key |
| `activity.consent_grants` | `consent_grants_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `activity.consent_grants` | `idx_activity_consent_grants_user_purpose` | btree | <1 MB | 2 | <1 MB | keep | named reader or small table |
| `activity.erasures` | `erasures_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `activity.erasures` | `idx_activity_erasures_subject_id` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `activity.event_idempotency` | `event_idempotency_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `activity.event_idempotency` | `idx_activity_event_idempotency_occurred_at` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `activity.user_subjects` | `user_subjects_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `activity.user_subjects` | `user_subjects_subject_id_key` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `graph.artist_degree` | `artist_degree_degree` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `graph.artist_degree` | `artist_degree_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `graph.artist_genre` | `artist_genre_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `graph.artist_genre` | `artist_genre_reverse` | btree | <1 MB | 0 | <1 MB | keep | reverse walk: counters, deletes, or the path functions |
| `graph.artist_member_of` | `artist_member_of_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `graph.artist_member_of` | `artist_member_of_reverse` | btree | <1 MB | 0 | <1 MB | keep | reverse walk: counters, deletes, or the path functions |
| `graph.genre_stats` | `genre_stats_first_year` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `graph.genre_stats` | `genre_stats_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `graph.label_genre` | `label_genre_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `graph.label_genre` | `label_genre_reverse` | btree | <1 MB | 0 | <1 MB | keep | reverse walk: counters, deletes, or the path functions |
| `graph.label_stats` | `label_stats_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `graph.label_stats` | `label_stats_release_count` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `graph.release_degree_base` | `release_degree_base_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `graph.style_stats` | `style_stats_first_year` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `graph.style_stats` | `style_stats_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `graph.vertex_degree` | `vertex_degree_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `insights.activity_summary` | `activity_summary_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `insights.artist_centrality` | `artist_centrality_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `insights.community_counts` | `community_counts_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `insights.community_counts` | `idx_community_counts_fetched` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `insights.genre_atlas_sectors` | `genre_atlas_sectors_pkey` | btree | <1 MB | 2 | <1 MB | keep | enforces a key |
| `insights.genre_trends` | `genre_trends_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `insights.genre_year_counts` | `genre_year_counts_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `insights.label_longevity` | `label_longevity_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `insights.monthly_anniversaries` | `idx_anniversaries_month_year` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `insights.monthly_anniversaries` | `monthly_anniversaries_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `insights.release_rarity` | `idx_release_rarity_gem` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `insights.release_rarity` | `idx_release_rarity_score` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `insights.release_rarity` | `idx_release_rarity_tier` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `insights.release_rarity` | `release_rarity_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `insights.style_atlas_layout` | `style_atlas_layout_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `insights.style_cotags` | `style_cotags_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `insights.style_cotags` | `style_cotags_style_rank_key` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `insights.style_landmark_masters` | `style_landmark_masters_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `insights.style_landmark_masters` | `style_landmark_masters_style_rank_key` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `insights.style_parents` | `idx_style_parents_one_dominant` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `insights.style_parents` | `style_parents_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `insights.style_year_counts` | `style_year_counts_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `musicbrainz.artists` | `artists_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `musicbrainz.artists` | `idx_mb_artists_discogs_id` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `musicbrainz.artists` | `idx_mb_artists_gm_item_id` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `musicbrainz.external_links` | `external_links_mbid_entity_type_service_name_url_key` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `musicbrainz.external_links` | `external_links_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `musicbrainz.labels` | `idx_mb_labels_discogs_id` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `musicbrainz.labels` | `idx_mb_labels_gm_item_id` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `musicbrainz.labels` | `labels_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `musicbrainz.relationships` | `idx_mb_rels_endpoint_source` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `musicbrainz.relationships` | `idx_mb_rels_endpoint_target` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `musicbrainz.relationships` | `idx_mb_rels_target` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `musicbrainz.relationships` | `relationships_natural_key` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `musicbrainz.relationships` | `relationships_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `musicbrainz.release_groups` | `idx_mb_release_groups_discogs_id` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `musicbrainz.release_groups` | `idx_mb_release_groups_gm_item_id` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `musicbrainz.release_groups` | `release_groups_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `musicbrainz.releases` | `idx_mb_releases_discogs_id` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `musicbrainz.releases` | `idx_mb_releases_gm_item_id` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `musicbrainz.releases` | `idx_mb_releases_release_group_mbid` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `musicbrainz.releases` | `releases_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.app_config` | `app_config_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.app_tokens` | `app_tokens_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.app_tokens` | `idx_app_tokens_token_lookup` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.app_tokens` | `idx_app_tokens_user_active` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.artifacts` | `artifacts_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.artifacts` | `idx_artifacts_item_id` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.artist_embedding_releases` | `artist_embedding_releases_model_version_key` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.artist_embedding_releases` | `artist_embedding_releases_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.artist_embedding_releases` | `idx_artist_embedding_releases_is_current` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.artist_similar_artists` | `artist_similar_artists_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.catalog_item_moves` | `catalog_item_moves_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.catalog_item_moves` | `catalog_item_moves_supersession_row_key` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.catalog_item_moves` | `idx_catalog_item_moves_user_id` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.catalog_item_supersessions` | `catalog_item_supersessions_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.catalog_item_supersessions` | `idx_catalog_item_supersessions_superseded_id` | btree | <1 MB | 4 | <1 MB | keep | enforces a key |
| `public.catalog_item_supersessions` | `idx_catalog_item_supersessions_survivor_id` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.catalog_item_supersessions` | `idx_catalog_item_supersessions_via_id` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.collection_snapshots` | `collection_snapshots_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.collection_snapshots` | `idx_collection_snapshots_user_taken_at` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.extraction_history` | `extraction_history_pkey` | btree | <1 MB | 1 | <1 MB | keep | enforces a key |
| `public.extraction_history` | `idx_extraction_history_created_at` | btree | <1 MB | 3 | <1 MB | keep | named reader or small table |
| `public.extraction_history` | `idx_extraction_history_source_created_at` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.extraction_history` | `idx_extraction_history_status` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.identity_ledger_edition_events` | `idx_identity_ledger_edition_events_at` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.identity_ledger_edition_events` | `idx_identity_ledger_edition_events_key` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.identity_ledger_edition_events` | `idx_identity_ledger_edition_events_type_at` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.identity_ledger_edition_parents` | `idx_identity_ledger_edition_parents_key` | btree | <1 MB | 4 | <1 MB | keep | enforces a key |
| `public.identity_ledger_work_summary` | `idx_identity_ledger_work_summary_key` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.identity_ledger_work_summary` | `idx_identity_ledger_work_summary_page` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.identity_link_events` | `identity_link_events_idempotence_key` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.identity_link_events` | `identity_link_events_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.identity_link_events` | `idx_identity_link_events_entity_event_at` | btree | <1 MB | 20 | <1 MB | keep | named reader or small table |
| `public.identity_link_events` | `idx_identity_link_events_event_at` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.loader_derived_refresh_job` | `idx_loader_derived_refresh_job_due` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.loader_derived_refresh_job` | `idx_loader_derived_refresh_job_lease_expiry` | btree | <1 MB | 5,386 | <1 MB | keep | named reader or small table |
| `public.loader_derived_refresh_job` | `loader_derived_refresh_job_generation_key` | btree | <1 MB | 10,774 | <1 MB | keep | enforces a key |
| `public.loader_derived_refresh_job` | `loader_derived_refresh_job_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.loader_extraction_latch` | `idx_loader_extraction_latch_generation` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.loader_extraction_latch` | `idx_loader_extraction_latch_job_generation` | btree | <1 MB | 273 | <1 MB | keep | enforces a key |
| `public.loader_extraction_latch` | `loader_extraction_latch_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.oauth_tokens` | `oauth_tokens_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.oauth_tokens` | `oauth_tokens_user_id_provider_key` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.observations` | `idx_observations_artifact_kind` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.observations` | `idx_observations_user_id` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.observations` | `observations_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.owned_copies` | `idx_owned_copies_collection_row_id` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.owned_copies` | `idx_owned_copies_item_id` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.owned_copies` | `idx_owned_copies_user_id` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.owned_copies` | `owned_copies_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.sync_history` | `idx_sync_history_running` | btree | <1 MB | 1 | <1 MB | keep | named reader or small table |
| `public.sync_history` | `idx_sync_history_user_started` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.sync_history` | `sync_history_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.user_collections` | `idx_user_collections_gm_item_id` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.user_collections` | `idx_user_collections_owned_copy_id` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.user_collections` | `idx_user_collections_release_id` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.user_collections` | `user_collections_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.user_collections` | `user_collections_user_id_release_id_instance_id_key` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.user_wantlists` | `idx_user_wantlists_gm_item_id` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.user_wantlists` | `idx_user_wantlists_release_id` | btree | <1 MB | 0 | <1 MB | keep | named reader or small table |
| `public.user_wantlists` | `user_wantlists_pkey` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `public.user_wantlists` | `user_wantlists_user_id_release_id_key` | btree | <1 MB | 0 | <1 MB | keep | enforces a key |
| `activity.events` | `events_occurred_at_idempotency_key_key` | btree | — | — | — | keep | enforces a key |
| `activity.events` | `events_pkey` | btree | — | — | — | keep | enforces a key |
| `activity.events` | `idx_activity_events_subject_occurred_at` | btree | — | — | — | keep | named reader or small table |
| `activity.events` | `idx_activity_events_type_occurred_at` | btree | — | — | — | keep | named reader or small table |
| `activity.impressions` | `idx_activity_impressions_candidate_set_id` | btree | — | — | — | keep | named reader or small table |
| `activity.impressions` | `idx_activity_impressions_subject_occurred_at` | btree | — | — | — | keep | named reader or small table |
| `activity.impressions` | `impressions_pkey` | btree | — | — | — | keep | enforces a key |
| `graph.company` | `company_key` | btree | — | — | — | keep | enforces a key |
| `graph.credited_to` | `credited_to_key` | btree | — | — | — | keep | enforces a key |
| `graph.person` | `person_key` | btree | — | — | — | keep | enforces a key |
| `graph.same_as` | `same_as_key` | btree | — | — | — | keep | enforces a key |
| `graph.same_as` | `same_as_person_name_hash` | hash | — | — | — | keep | analytics person-to-artist join |
