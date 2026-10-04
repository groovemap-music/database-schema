# Schema successor source and live compatibility

## Question

Can the normal `database-schema` release path produce a supported immutable successor for the MusicBrainz consumers from current `main`, preserving SQL/PGQ removal, Neo4j graph authority, and the accepted compact similar-artist contract, and can that complete initializer be shown compatible with the current live PostgreSQL and Neo4j schemas?

## Method

This research used only repository history and source plus the maintained, metadata-only receipts in `work/beadhive-inventory-2026-10-02/dispatch`. It did not connect to either live store. The source inventory was generated from the pinned offline environment by enumerating PostgreSQL `_schema_statements()`, its conditional vector/role catalogs, and Neo4j `SCHEMA_STATEMENTS`. Git ancestry, trees, tags, workflow references, and release scripts were read locally. The live comparison is limited to objects actually captured by `update75-musicbrainz-live-store-compatibility-research.json`; absence of evidence is recorded as unverified rather than compatible.

The compared source points are:

| Source point | Commit | Tree | Result |
|---|---|---|---|
| Published `v0.4.1` | `4f3efde3b6c9cb72e8ed9bce55635f611a8b3e44` | `c7036abdf8591cace33fa6b47149011d5d336b12` | Ancestor of `9a50949`; contains the removed SQL/PGQ activation path and cannot be reused. |
| Required consumer floor | `9a50949b1810f3e61adae2f89b86acec2e20c4a3` | `8a0a2442e9621bf7e171de6c730efe9292dfd71c` | Removes SQL/PGQ while retaining ordinary relational graph objects. No published tag contains it. |
| Current `main` | `4e9720d838c7da8a6bde139c64a69d781c0f67f0` | `a56fc2dd24ebf34e442ea3410f74c779aec87d0a` | Descends from `9a50949`; the only source change after that floor is the accepted compact similar-artist storage change merged through `b0208cc`. |

## Evidence

### Complete normal initializer inventory

Current `main` always attempts 327 PostgreSQL statements and all 38 Neo4j statements. PostgreSQL autocommit is enabled; individual statement failures are counted while later statements continue. The initializer runs both stores concurrently and exits zero only when both stores report no failures. It has no store-only or object-only mode.

The 327 unconditional PostgreSQL statements are completely partitioned by the source catalogs below; the counts sum to 327.

| Ordered catalog | Count | Objects and operations covered | Live comparison |
|---|---:|---|---|
| Discogs entity generator | 20 | Four entity tables; hash, `updated_at`, and `gm_item_id` indexes/columns | No complete live comparison in the admitted receipts. |
| Specific indexes | 17 | Discogs expression, GIN, FTS, media column and media-family index | No complete live comparison. |
| User/application catalog | 70 | Users/auth, collections/wantlists, native identity, copies/observations/snapshots, sync/admin/loader coordination and `resolve_catalog_item` | No complete live comparison. |
| Insights catalog | 22 | `insights` schema, eight tables, indexes, and additive rarity/media columns | No complete live comparison. |
| Activity catalog | 18 | `activity` schema, subjects/consent/events/impressions/partitions/erasures, indexes, partition function and append-only triggers | No complete live comparison. |
| MusicBrainz table/migration catalog | 19 | Schema; six tables; relationship-key widening; `media`, `updated_at`, `gm_item_id`, and bigint widening migrations | The six tables, entity PKs and both natural keys are proven present. Ten required additions are proven missing, listed below. Other exact definitions were not exhaustively compared. |
| MusicBrainz indexes | 20 | Discogs-id, media-family, name, relationship endpoint/type, external-link, `updated_at`, and `gm_item_id` indexes | Indexes associated with the missing columns are necessarily missing; the receipt does not compare every other index definition. |
| Similar-artist catalog | 6 | `artist_embedding_releases`, `artist_similar_artists`, unique-current index, K-guard function and two triggers | Completely unverified live. This is a blocking compatibility gap. |
| Ordinary relational graph catalog | 135 | 1 schema, 1 extension, 8 functions, 27 guarded view/type migrations, 31 tables, 30 indexes, and 37 views | `media_family`, `medium`, `issued_on`, `medium_label`, `mb_relationship_type`, and `bootstrap_fill` are proven missing. The remaining exact definitions are not fully compared. |

After those 327 statements, the same normal run probes server capabilities. It may additionally attempt three vector statements (`vector`, `artist_embeddings`, and its model-version index), three pipeline-role statements, one embedding-table grant, and two similarity-table/sequence grants. These are guarded by vector availability, superuser, and `CREATEROLE`; a closed guard is an intentional skip. The live receipt says PostgreSQL 18.6 has no vector extension, while the connecting role is superuser with `CREATEROLE`, so a successor run is expected to skip vector/table/index and the vector-dependent grant but attempt the role plus ordinary graph and similarity grants. This expected branch is source-and-metadata reasoning, not a live execution result.

The 38 Neo4j statements are 11 uniqueness constraints, 21 range indexes, and 6 full-text indexes. Their exact names are: `artist_id`, `label_id`, `master_id`, `release_id`, `genre_name`, `style_name`, `medium_id`, `media_family_name`, `user_id`, `person_name`, `company_id`; `artist_gm_id`, `label_gm_id`, `master_gm_id`, `release_gm_id`, `artist_sha256`, `label_sha256`, `master_sha256`, `release_sha256`, `artist_name`, `label_name`, `release_year_index`, `master_year_index`, `release_media_families_index`, `release_country`, `genre_first_year_index`, `style_first_year_index`, `person_credit_count`, `artist_mbid`, `label_mbid`, `release_mbid`, `master_mbid`; and `artist_name_fulltext`, `release_title_fulltext`, `label_name_fulltext`, `genre_name_fulltext`, `style_name_fulltext`, `person_name_fulltext`. All use `IF NOT EXISTS`. Live metadata proves the four legacy `id` constraints/indexes and four MBID indexes needed by the MusicBrainz graph consumer are online. `Medium` and `MediaFamily` do not yet exist. The receipt does not compare the other 28 statements by name, type, property and options, so full Neo4j compatibility is unproven.

### Proven MusicBrainz gaps

The live PostgreSQL receipt proves these normal-source objects absent: UUID `gm_item_id` on `musicbrainz.artists`, `labels`, `releases`, and `release_groups`; JSONB `musicbrainz.releases.media`; `updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()` on `musicbrainz.relationships` and `external_links`; tables `graph.media_family`, `graph.medium`, and `graph.issued_on`; and functions `graph.medium_label`, `graph.mb_relationship_type`, and `graph.bootstrap_fill`. These are enough to block the SQL consumer and prove that live has not received `9a50949`.

Neo4j remains the graph-query authority. The successor adds only constraints/indexes there; the ordinary PostgreSQL `graph` relations remain persistence/projection contracts. Current source contains no property-graph declaration, `GRAPH_TABLE` activation, or feature switch. The guarded `CASCADE` clauses in view-to-table migrations exist only to clean dependencies left by an older SQL/PGQ application.

### Compact similar-artist compatibility

Current `main` correctly retains the later accepted contract: `artist_embedding_releases` uses an identity `release_id`, a unique `model_version`, lineage/count/current fields, and a partial unique current index; `artist_similar_artists` stores one row per `(release_id, artist_id)` with aligned `TEXT[]` ids and `REAL[]` scores, cascades from the release, and enforces release K through a function and two triggers.

That change is not an in-place migration. Both table statements use `CREATE TABLE IF NOT EXISTS`; if a database already has the earlier rank-row shape, they succeed without changing it and later current-shape indexes/functions/triggers can fail or bind incorrectly. The source comment explicitly requires scratch databases with the unreleased old shape to reset both tables. Maintained planning evidence says the compact replacement was chosen because nothing was in production, but the admitted live metadata receipt never queried these two tables. Planning text is not live catalog proof. Therefore current `main` can be the source of a successor only after a fresh catalog check proves both tables absent or exactly current-shape. Any old/incompatible shape requires a separately reviewed migration or other owner-approved repair; this spike does not authorize one.

### Supported release, check, audit, image, and hosted paths

The repository has a normal supported path without source repair:

- `just check` covers format, lint, types, unit coverage, contracts, repository policy, install, licenses, secret scan and non-mutating bump preview. Required disposable PostgreSQL 18 plus Neo4j integration is a separate `just test-integration`; PostgreSQL 19 beta is advisory.
- `just audit` runs the dependency audit. `just release-dry-run` reruns `check`, builds wheel/sdist, SHA-256 sums, CycloneDX SBOM and third-party notices, then validates the artifacts.
- `just image` refuses modified or unverifiable source, builds the runtime image, probes its entrypoint, and validates OCI source/revision/version/license metadata.
- CI and release delegate to immutable automation commit `833cb464507678c38ab78bd4718ce697399463e9`. Only an approved `v*` tag enters the hosted publication workflow; it grants package, OIDC and attestation permissions and publishes the image with prepared runtime wheel. Branch and scheduled CI do not publish.

Existing validation receipts show current `main`'s normal repository gate completed successfully and the non-mutating bump preview proposes `v0.5.0`. They do not establish an approved tag, hosted run, immutable image digest, provenance/SBOM receipt, or live runtime for that successor. The deployed legacy `schema-init:latest` revision `2d680d4c...` and the previously approved repository image `v0.1.2` are not forward-migration candidates.

## Verdict: NO-GO

Current `main` is the only defensible successor source: it descends from the required consumer pin, preserves SQL/PGQ removal and Neo4j authority, and retains the approved compact similar-artist design. Published `v0.4.1` cannot be reused. However, a complete live compatibility determination is not possible from the admitted evidence. The receipts compare only the MusicBrainz blocker subset and consumer-relevant Neo4j surface, not all 327 PostgreSQL and 38 Neo4j statements. They also do not prove the live shape or absence of the two similarity tables, where `IF NOT EXISTS` cannot repair an old schema. No immutable successor release or image exists.

## Recommendation

The database-schema owner should keep `4e9720d` (or a normally reviewed descendant that preserves its tree contracts) as the successor source and must not branch from `v0.4.1`, revert the compact storage change, or restore SQL/PGQ. Before publication, an authorized operator should capture a fresh metadata-only, statement-by-statement live comparison for all unconditional PostgreSQL and Neo4j objects, plus the conditional capability/role branch. It must explicitly prove both similarity tables absent or exactly current-shape. If either has the rank-row shape, the owner must design and separately approve an explicit migration/repair before proceeding.

After that inventory is independently reviewed, the owner can use the existing normal version, check, audit, integration, release-dry-run, image and hosted `v*` workflow to produce a new immutable successor (the current preview is `v0.5.0`). Publication, disposable two-run validation, live schema application, and post-apply inventory remain separate gated work. Do not deploy or start the newly published SQL and graph consumers before those schema prerequisites complete, and do not apply a schema initializer live through this research. The healthy legacy consumers currently running their old images remain outside this spike; this recommendation neither requires nor authorizes stopping them.
