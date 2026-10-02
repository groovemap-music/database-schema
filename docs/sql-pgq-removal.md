# SQL/PGQ removal and recovery

PostgreSQL 19 removed SQL/PGQ before release, so this repository no longer
declares or probes `graph.catalog`. The pre-cleanup tree is commit
`fecb0a43814a1e5047a5db423e7bb924986dedf6`.

The implementation originated in these commits and was extended later:

- `f2db254ff04fdecc4d4943bbd5f95363ec83fb41` added the conditional
  `CREATE PROPERTY GRAPH` declaration and `SCHEMA_PROPERTY_GRAPH` switch.
- `8e564c3df7881bd841a96f3363bccf072e3d2df2` and
  `83c91b817f58df1009eba3806118886846fac5c3` added unit and real engine tests.
- `f8a7d312f449b6ab7fb1f6533e39e7033ce701cf` added catalog inspection,
  fingerprinting, and replacement of stale declarations.
- `4381a3cc9ab44aa6d1b81bc110b183bd68f9cff1` and
  `14dddf5b14aaf00ab92899fac40e07690bf9b6da` extended the declaration for
  MusicBrainz native identifiers and track relations.

Recover the complete former implementation without an archive branch with:

```sh
git show fecb0a43814a1e5047a5db423e7bb924986dedf6:src/groovemap_schema/postgres.py
git show fecb0a43814a1e5047a5db423e7bb924986dedf6:tests/test_postgres_schema.py
git show fecb0a43814a1e5047a5db423e7bb924986dedf6:tests/test_graph_schema.py
git show fecb0a43814a1e5047a5db423e7bb924986dedf6:tests/integration/test_real_schema_idempotence.py
git diff fecb0a43814a1e5047a5db423e7bb924986dedf6^ fecb0a43814a1e5047a5db423e7bb924986dedf6 -- docs contracts
```

## Retained contracts

The cleanup retains every ordinary PostgreSQL relation in the `graph` schema,
including loader written vertex and edge tables, compatibility views, counter
tables, grants, `graph.bootstrap_fill`, `graph.refresh_artist_member_of`,
`graph.refresh_vertex_degree`, `graph.find_shortest_path`, and
`graph.explore_traversal`. Discogs and MusicBrainz loaders continue to write the
same tables. The analytics engine and catalog API can continue to use ordinary
SQL over those relations. Neo4j schema statements and parity fixtures remain
unchanged and Neo4j remains the authoritative graph query backend.

Only the SQL/PGQ declaration generator, environment switch, PostgreSQL 19
catalog probes, `GRAPH_TABLE` queries, and their dedicated tests are removed.
The pgvector and other PostgreSQL 19 integration coverage is independent and
remains supported.

Rollback is a source rollback: revert the removal commit or restore the paths
above from `fecb0a4`. No database rollback is required because the removed path
was additive and disabled by default. Existing experimental `graph.catalog`
objects are left untouched; operators may drop them explicitly on the
experimental PostgreSQL build that created them.
