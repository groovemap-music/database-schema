# Unread index drops: evidence and decisions (gm-database-schema-zd7u)

Proposal B2 of [the 2026-11-04 audit](2026-11-04-index-view-function-audit.md) asked for the unread indexes to
be dropped after a week of read traffic. This is that re-read, with the sibling repositories searched for the
indexed columns and predicates, and the decisions.

## What the counters can and cannot say

Read-only, on lux, 2026-11-15 05:41 UTC, PostgreSQL 18.6:

- `pg_stat_database.stats_reset` for `groovemap` is `NULL`, so the counters cover everything since the statistics
  were created, loads and real reads together. `last_idx_scan` is `NULL` for every index with 0 scans.
- 276 indexes exist. **84 have never been scanned and are neither unique nor a primary key: 25.09 GiB.**
  The audit's 36 were 6.4 GB on a partial load. The 84 include indexes it did not list, among them the 5.0 GiB
  HNSW index built since, and the same indexes at their full-load size.
- The scan counts that are large (`idx_provider_aliases_provider_entity_kind_external_id` 306M, `relationships_key` 135M,
  `issued_on_reverse` 116M) are the loaders' own lookups, so they are mostly load. Even `provider_aliases_pkey` and
  `release_degree_base_pkey` show 0 scans, and the reads that are not load are few. **0 scans alone does not clear an
  index.** Each drop below also has a named absence of readers in every consumer repository, and no rarely-run path (the
  monthly embeddings job, the nightly similar-artists job, the identity-ledger rebuild, loader purge and seen-list paths,
  the graph enrichers, and `catalog-api`'s rarely-hit routes) that could use it.
- Searched, as `rg` over the working trees of `analytics-engine`, `catalog-api`, `discogs-graph-enricher`,
  `discogs-sql-loader`, `musicbrainz-sql-loader`, `mcp-server`, `graph-explorer`, `operations-toolkit`,
  `deployment` and `python-libraries` (tests, `design` and `planning-archive` excluded): the column and the predicate
  shape of each candidate, not only the index name. A search cannot see SQL composed at run time, which is why the
  rollback below is cheap.

## Dropped: 16 indexes, 14.64 GiB on lux

"B2" means the audit's list already had it. "new" means it did not.

| Index | Table | Size on lux | Scans | In the audit | Why it is safe |
| --- | --- | ---: | ---: | --- | --- |
| `idx_releases_identifiers` | `public.releases` | 2.81 GiB | 0 | B2 | No containment query. `catalog-api/api/reattach.py` tests `data->'identifiers'->'aliases' @>` on a row the primary key found, and exact barcode or catalogue-number lookup resolves through `provider_aliases`. ADR 0011 described the index as serving analytical containment queries; none exists. |
| `track_by_artist_reverse` | `graph.track_by_artist` | 2.57 GiB | 0 | B2 | Nothing walks `track_by_artist` from the artist end: analytics-engine reads the whole relation, and the loaders delete by `release_id`, which leads the primary key. Not a path relation. |
| `idx_releases_hash` | `public.releases` | 2.28 GiB | 0 | B2 | Same as `idx_artists_hash`. |
| `idx_release_rarity_gem` | `insights.release_rarity` | 1.46 GiB | 0 | new | `catalog-api` never orders or filters by `hidden_gem_score` (the tier route selects the column and orders by `rarity_score`). Audit listed it as keep because it was small then; at full size it is 1.5 GB. |
| `idx_artists_hash` | `public.artists` | 1.19 GiB | 0 | B2 | No `WHERE hash =` in any repository. The Discogs loader reads `hash` beside `data_id` (`WHERE data_id = ANY(...)`) and compares it in `record_persistence.py` against the row the primary key found. |
| `idx_releases_companies` | `public.releases` | 1.09 GiB | 0 | B2 | Same as `idx_releases_identifiers`; `catalog-api` reads `data->'companies'` only by `data_id`. |
| `idx_releases_labels` | `public.releases` | 1.04 GiB | 0 | B2 | No containment query. `discogs-graph-enricher/scripts/stub-name-export.sql` unnests `data->'labels'` over the whole table, which no index serves. |
| `idx_releases_title` | `public.releases` | 0.60 GiB | 0 | B2 | Same as `idx_masters_title`, through `release_search`. |
| `idx_artists_name` | `public.artists` | 0.38 GiB | 0 | B2 | No `data->>'name' =` in any repository. `catalog-api` reads names through the full-text index and `lookup_queries` selects them. |
| `idx_mb_links_mbid` | `musicbrainz.external_links` | 0.34 GiB | 0 | B2 | Left prefix of the unique key `external_links_mbid_entity_type_service_name_url_key`; `get_artist_external_links` filters on `mbid` and the key serves it. |
| `idx_masters_hash` | `public.masters` | 0.30 GiB | 0 | B2 | Same as `idx_artists_hash`. |
| `idx_labels_hash` | `public.labels` | 0.28 GiB | 0 | B2 | Same as `idx_artists_hash`. |
| `idx_mb_artists_name` | `musicbrainz.artists` | 0.11 GiB | 0 | B2 | No `name =` lookup on `musicbrainz.artists` in any repository. |
| `idx_masters_title` | `public.masters` | 94 MiB | 0 | B2 | No `data->>'title' =` in any repository; titles are searched through `master_search` / `idx_masters_fts`. |
| `idx_labels_name` | `public.labels` | 81 MiB | 0 | B2 | Same as `idx_artists_name`. |
| `idx_mb_labels_name` | `musicbrainz.labels` | 12 MiB | 0 | B2 | Same as `idx_mb_artists_name`. |

Rollback is the index's original `CREATE INDEX` (in `tests/integration/test_unread_index_drops.py`, `_PREVIOUS_RELEASE`)
followed by a rebuild. The GIN and hash indexes on `releases` are the slow rebuilds (19.6M rows, not timed here); use
`CREATE INDEX CONCURRENTLY` if a reader ever returns.

## Kept, with the reason (68 indexes with 0 scans)

An index stays when a rarely-run path could use it, when the design names a reader that has not arrived, or when the
saving is too small to be worth the risk. The ones that need a decision are first.

| Index | Table | Size on lux | Why it stays |
| --- | --- | ---: | --- |
| `idx_artist_embeddings_discogs_2026_11_e7dd9162b4ef_hnsw` | `public.artist_embeddings` | 4.86 GiB | Built this month for the similar-artists job. Its reader is the nightly job and the monthly embeddings run, neither of which had a chance to read it. Not evidence of non-use. |
| `credited_to_company_id_hash` | `graph.credited_to` | 1.31 GiB | Candidate, not dropped: the free-text key design (`_FREE_TEXT_KEYS`) declares it as the lookup for the Neo4j-to-PostgreSQL read move. No reader exists yet, but dropping it means reworking the free-text key migration. Follow-up bead, 1.3 GB. **Retired by gm-database-schema-ntr5**, see below. |
| `by_artist_reverse` | `graph.by_artist` | 1.04 GiB | Reverse walk used by counters, deletes and the path functions (audit finding 2). Rare, not absent. |
| `idx_releases_gm_item_id` | `public.releases` | 0.58 GiB | `catalog-api/api/catalog_merge.py` `_RECOMPUTE` runs `UPDATE ... WHERE cached.gm_item_id = ANY(...)` on all four entity tables when two catalog items merge or split, and its comment says "each column is indexed, so a recompute is index probes". A rare, operator-driven path that would sequentially scan 19.6M rows without it. |
| `idx_release_rarity_artist_ids` | `insights.release_rarity` | 0.37 GiB | `docs/architecture.md` documents the `artist_ids @>` route, and the deploy fails without the index. The route has not been called yet. |
| `person_name_trgm` | `graph.person` | 0.33 GiB | Same: person autocomplete is on Neo4j today and the index is the one a PostgreSQL read would need. 0.3 GB. **Retired by gm-database-schema-ntr5**, see below. |
| `idx_artists_gm_item_id` | `public.artists` | 0.30 GiB | Same as `idx_releases_gm_item_id`. |
| `person_name_hash` | `graph.person` | 0.29 GiB | Same as `credited_to_company_id_hash`. 0.3 GB. **Retired by gm-database-schema-ntr5**, see below. |
| `artist_genre_reverse` | `graph.artist_genre` | 0.26 GiB | Same: reverse walk for counters and the path functions. |
| `master_in_style_reverse` | `graph.master_in_style` | 0.22 GiB | Same. |
| `master_by_artist_reverse` | `graph.master_by_artist` | 0.13 GiB | Same. |
| `label_genre_reverse` | `graph.label_genre` | 0.12 GiB | Same. |
| `idx_releases_country` | `public.releases` | 0.12 GiB | The country filter in `search_queries.py` uses it (audit finding 2). |
| `artist_member_of_reverse` | `graph.artist_member_of` | 0.11 GiB | Same. Also the MusicBrainz member-of union walk. |
| `idx_masters_gm_item_id` | `public.masters` | 78 MiB | Same as `idx_releases_gm_item_id`. |
| `idx_labels_gm_item_id` | `public.labels` | 73 MiB | Same as `idx_releases_gm_item_id`. |
| `artist_degree_degree` | `graph.artist_degree` | 56 MiB | Leaderboard order index, read by the counters. 56 MB. |
| `idx_releases_genres` | `public.releases` | 50 MiB | `search_queries.py` still filters `releases` by genre. The audit found the `IS NULL OR ?|` shape cannot use it, which is a query to fix, not a reason to drop; 49 MB. **Retired by gm-database-schema-ntr5**, see below. |
| `company_company_id_hash` | `graph.company` | 49 MiB | Same as `credited_to_company_id_hash`. 48 MB. **Retired by gm-database-schema-ntr5**, see below. |
| `idx_releases_media_families` | `public.releases` | 34 MiB | Same as `idx_releases_genres`. 33 MB. **Retired by gm-database-schema-ntr5**, see below. |
| `idx_masters_year` | `public.masters` | 18 MiB | Same family: the year filter casts `::int`, so the index cannot match; fix the query first. 17 MB. `idx_releases_year` stays because it has 2 scans. **Retired by gm-database-schema-ntr5**, see below. |

The other 47 are under 100 MB each, 89 MiB together, on empty or nearly empty tables
(the `activity.*` partitions, `user_*`, `owned_copies`, `observations`, `catalog_item_*`, `app_tokens`, and the small
graph and insights tables). There is no read traffic on those tables to judge by and nothing to save.

## Unread because the query cannot use it

The brief asks to report these instead of dropping them. Both are unread indexes whose *query* is the problem:

- `catalog-api`'s year filter casts `(data->>'year')::int`, which does not match `idx_releases_year` or
  `idx_masters_year` (`(data->>'year')`). `idx_releases_year` has 2 scans, from another shape. The filter is a
  full scan of `releases` until it is rewritten to compare the text expression, or until it moves to `release_search`.
- The genre and media filters use `(c IS NULL OR c ?| ...)`, which has no GIN path for the `IS NULL` arm.

These cost 17 MB to 126 MB, so the indexes stay and the queries are the follow-up.

**Follow-up, 2026-11-16 (gm-catalog-api-q6fy, retired by gm-database-schema-ntr5).** The queries were looked at
again, and the indexes are the thing to go. `catalog-api`'s release branches now filter year, genre, media and
country on `public.release_search` (database-schema v0.12.0) and its master branches filter year on
`public.master_search`, both after the `title_tsv` bitmap match, so no search query touches `public.releases` or
`public.masters` by these expressions at all, and an index there could never apply. EXPLAIN (ANALYZE, BUFFERS) on
lux of the ranked branch for a common word (`love`, about 497k matching releases) with year, genre and media-family
filters reads the same buffers as without them: the filters cost CPU on the rows the title match already visits,
which no index on the base tables avoids. No other repository filters `public.releases` or `public.masters` by
year, genre or media: `deployment/scripts/cleanup-implausible-years.sh` tests `data->>'year' ~ '^[0-9]+$'` and casts
to `int`, which a btree on the text expression cannot serve. Lux counters on 2026-11-16 (lifetime, `stats_reset`
NULL):

| Index | Scans | Last scan | Size on lux |
| --- | ---: | --- | ---: |
| `idx_releases_year` | 2 | 2026-11-08 19:05 UTC | 121 MiB (126.5 MB) |
| `idx_releases_genres` | 0 | - | 50 MiB (52.0 MB) |
| `idx_releases_media_families` | 0 | - | 34 MiB (35.5 MB) |
| `idx_masters_year` | 0 | - | 18 MiB (18.4 MB) |

The two scans of `idx_releases_year` match no query in any repository and are consistent with a one-off
diagnostic query. All four are retired, 232 MB (222 MiB) in all. `idx_releases_country` stays (audit finding 2).

## Candidates left for a follow-up

`credited_to_company_id_hash`, `person_name_hash`, `person_name_trgm` and `company_company_id_hash` (about 2.0 GiB) have no
reader but belong to the free-text key design (`_FREE_TEXT_KEYS`), whose lookup indexes exist for the planned
PostgreSQL read of person and company. Dropping them means reworking that migration's `NOT NULL` restoration, and is
a decision for whoever owns the move off Neo4j.

**Resolved 2026-11-16: retired by gm-database-schema-ntr5.** The PostgreSQL read they were for went away when
`catalog-api` removed its PostgreSQL graph backend in October 2026 (`catalog-api/docs/pgq-removal.md`;
`docs/architecture.md`, "SQL/PGQ removal"), so Neo4j is the only graph query engine and the move off it is not
planned. Readers were checked first, as for the sixteen above:

- No repository has a query with `=` on `graph.person.name`, `graph.company.company_id` or
  `graph.credited_to.company_id`, or a trigram, `LIKE` or similarity match on `graph.person.name`.
- `discogs-sql-loader` deletes `credited_to` by `release_id = ANY(...) AND source = ...`, which leads
  `credited_to_key`, and never deletes `person` or `company` rows (shared vocabulary). Its reconcile sweep covers
  only `member_of` and `same_as`. Every writer uses a target-less `ON CONFLICT DO NOTHING`, which probes
  `<relation>_key`.
- The schema's own functions read these relations by full scan only: the `release_degree_base` counter the loaders
  refresh counts `credited_to.release_id`, `graph.bootstrap_fill` truncates and refills, and the path and explore
  functions do not traverse `credited_to`, `person` or `company`.

Lux, read-only, 2026-11-16 04:11 UTC (lifetime counters, `stats_reset` NULL):

| Index | Scans | Size on lux | Decision |
| --- | ---: | ---: | --- |
| `graph.credited_to_company_id_hash` | 0 | 1,340 MiB (1,405 MB) | Retired |
| `graph.person_name_trgm` | 0 | 341 MiB (357 MB) | Retired |
| `graph.person_name_hash` | 0 | 292 MiB (306 MB) | Retired |
| `graph.company_company_id_hash` | 0 | 49 MiB (51 MB) | Retired |
| `graph.same_as_person_name_hash` | 30,933,638 (last 2026-11-10) | 292 MiB | Kept: `analytics-engine`'s embedding pipeline joins `credited_on` and `track_credited_on` to `same_as` on `person_name` |
| `graph.person_key`, `graph.company_key`, `graph.credited_to_key` | 102.9M, 24.3M, 35.4M | 601, 94, 4,724 MiB | Unchanged: the unique keys every writer's `ON CONFLICT` uses |

The four retired lookups free 2.12 GB (1.97 GiB). The free-text key migration now declares, per relation, whether it
has a lookup index and whether its column needs `NOT NULL` restored, so a database upgraded from the primary-key
shape gets `person.name`, `company.company_id` and `credited_to.company_id` back as `NOT NULL` without building the
index. On lux all three are already `NOT NULL`. Rollback is the original `CREATE INDEX` in
`tests/integration/test_unread_index_drops.py` (`_PREVIOUS_RELEASE`) and a rebuild, `CONCURRENTLY` on lux.

## The change

Schema-init (`src/groovemap_schema/postgres.py`) keeps each retired index's statement name and position, which
`tests/test_identity_schema.py` pins as expand-only, and replaces the SQL with `DROP INDEX IF EXISTS`. A fresh database
never builds the index; an upgraded one loses it on the next init; a rerun is a no-op that takes no table lock. It is
not `CONCURRENTLY`: that cannot run inside a transaction block and a plain drop only unlinks files, so the table lock is
held for milliseconds and is bounded by the init's `lock_timeout`. Run schema-init with the loaders stopped, as for every
index change. The persistence contract lists no index in this set, so it does not change.
