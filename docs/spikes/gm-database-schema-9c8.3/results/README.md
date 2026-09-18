# Captured evidence

The measurements behind
[`../../gm-database-schema-9c8.3-variable-length-paths.md`](../../gm-database-schema-9c8.3-variable-length-paths.md),
kept rather than discarded. gm-database-schema-9c8.1's reviewer could not audit
that spike's Neo4j access counts because its `PROFILE` capture had been thrown
away; every plan and profile this spike quotes is reproduced here in full.

| File | What it is |
| --- | --- |
| `environment.txt` | Host, Docker, PostgreSQL version, image digest and server settings, captured from the running container. |
| `timings.tsv` | Every PostgreSQL run: `variant`, `case`, `depth_cap`, `run`, `ms`. `over-temp-limit` is p1 exceeding `temp_file_limit`. |
| `timings-neo4j.tsv` | Every Neo4j run, same columns, after the `PROFILE` line and the warm-up are dropped. |
| `answers.csv` | The distance, visited count and per-level frontier sizes each search returned. This is the cross-check: all three PostgreSQL searches agree on every case. |
| `explain-p1.txt` | `EXPLAIN (ANALYZE, BUFFERS)` of the naive recursive CTE at caps 1 to 4. Cap 3 and above carry the error instead of a plan, which is the result. |
| `explain-levels.txt` | `auto_explain` with `log_nested_statements`, capturing the per-level expansion inside the PL/pgSQL searches. `EXPLAIN` of the function call itself would show a Function Scan and nothing else. |
| `neo4j-shortest-path-*.txt` | Per case: the query, its `PROFILE` with operator-level DB hits, and the timed runs. |
| `neo4j-explore-traversal-*.txt` | The same for `*1..n`. |
| `neo4j-stats-synthetic.txt` | Node and relationship counts by label and type, which is how the two engines' edge sets were checked against each other. |
| `neo4j-import-synthetic.txt`, `neo4j-generate-synthetic.txt` | Bulk import and generator output. |

Timings are from one sitting on the machine `environment.txt` describes, with
each engine measured while the other was removed.
