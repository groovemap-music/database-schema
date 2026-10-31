# Runtime configuration

The `database-schema` image is a one-shot initializer. It exits successfully only after
the PostgreSQL database exists and both the PostgreSQL and Neo4j schemas have been
applied. The deployment stack waits for that successful exit before starting dependent
services.

| Variable | Default | Purpose |
| --- | --- | --- |
| `POSTGRES_HOST` | `localhost` | PostgreSQL hostname, optionally with an embedded port |
| `POSTGRES_PORT` | `5432` | PostgreSQL port when it is not embedded in the host |
| `POSTGRES_DATABASE` | `groovemap` | Database to create and initialize |
| `POSTGRES_USERNAME` | `groovemap` | PostgreSQL user |
| `POSTGRES_PASSWORD` | `groovemap` | PostgreSQL password |
| `POSTGRES_PREPROVISIONED` | `false` | `true` selects pre-provisioned mode: verify an operator-provisioned database instead of creating one (see [Pre-provisioned PostgreSQL](#pre-provisioned-postgresql)) |
| `NEO4J_HOST` | `localhost` | Neo4j hostname or full connection URI |
| `NEO4J_PORT` | `7687` | Bolt port when the host is not a full URI |
| `NEO4J_USERNAME` | `neo4j` | Neo4j user |
| `NEO4J_PASSWORD` | `groovemap` | Neo4j password |
| `NEO4J_TLS_ENABLED` | `false` | Enable encrypted Bolt transport |
| `NEO4J_TLS_VERIFY` | `true` | Verify the Neo4j server certificate when TLS is enabled |
| `LOG_LEVEL` | `INFO` | Structured-log level |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | OTLP/HTTP collector base URL, for example `http://otel-collector:4318`. Unset disables both metrics and trace export |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | falls back to `OTEL_EXPORTER_OTLP_ENDPOINT` | Metrics-only endpoint override |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | falls back to `OTEL_EXPORTER_OTLP_ENDPOINT` | Traces-only endpoint override |
| `OTEL_METRICS_EXPORTER` | `otlp` | Set to `none` to force metrics export off even with an endpoint configured |
| `OTEL_TRACES_EXPORTER` | `otlp` | Set to `none` to force trace export off while metrics keep flowing |
| `OTEL_TRACES_SAMPLER` | `parentbased_traceidratio` | Sampler the SDK applies to root spans |
| `OTEL_TRACES_SAMPLER_ARG` | `1.0` | Sampling ratio; deployment turns this down in production |
| `OTEL_METRIC_EXPORT_INTERVAL` | SDK default | Push interval in milliseconds |
| `OTEL_SDK_DISABLED` | `false` | `true` makes the SDK a no-op even with an endpoint configured |
| `OTEL_SERVICE_NAME` | `schema-init` | Overrides the `service.name` resource attribute |
| `OTEL_RESOURCE_ATTRIBUTES` | empty | Extra resource attributes, for example `service.namespace=groovemap,deployment.environment.name=dev` |

Secrets also support the Docker secret-file convention. Set
`POSTGRES_USERNAME_FILE`, `POSTGRES_PASSWORD_FILE`, or `NEO4J_PASSWORD_FILE` to a mounted
file path instead of placing the corresponding value directly in the environment.

The image writes its file log to `/logs/database-schema.log`; mount `/logs` when the log
must persist beyond the one-shot container. Outside the container `/logs` is not required:
if the log file cannot be opened, the runtime warns once and logs to the console only. Set
`LOG_FILE` to write the file elsewhere, or to an empty value to disable the file log.

## Pre-provisioned PostgreSQL

By default (dedicated mode) the initializer connects to the `postgres` maintenance database and
runs `CREATE DATABASE` when the target is missing, then `CREATE EXTENSION pg_trgm`, `vector`
(when installable), and the `embedding_pipeline` role where the connecting role may. That needs
a privileged login and is wrong for a shared server.

Set `POSTGRES_PREPROVISIONED=true` (also accepts `1`, `yes`, `on`) when an administrator has
provisioned the database, role and extensions. In this mode the initializer:

- never connects to `postgres` and never issues `CREATE DATABASE`, `CREATE ROLE`, or `CREATE EXTENSION`;
- verifies, before any DDL, that the target database is reachable, that the runtime role can create
  schemas in it and objects in `public`, and that `pg_trgm` is installed. Any failure exits
  nonzero and logs the exact SQL the administrator must run;
- reports whether `vector` is installed. Absent is **not** fatal: `public.artist_embeddings` is
  skipped, as in dedicated mode;
- reports whether the `embedding_pipeline` role exists. Absent is **not** fatal: the embedding
  feature degrades (its grants are skipped) and the log names the SQL that enables it. When the
  role exists, the initializer applies its grants, which only need ownership of the objects;
- then applies the same schema DDL as dedicated mode, inside that one database only.

The runtime role needs no `SUPERUSER`, `CREATEROLE`, or `CREATEDB`. Dedicated mode is unchanged.

### Least-privilege provisioning SQL

Run once as a PostgreSQL administrator. Replace the names and password.

```sql
-- 1. The runtime role: a plain login, no cluster-wide privileges.
CREATE ROLE groovemap LOGIN PASSWORD '<password>' NOSUPERUSER NOCREATEDB NOCREATEROLE;

-- 2. The database, owned by that role. Ownership is what lets it create schemas and objects
--    (on PostgreSQL 15+ it also owns `public` through pg_database_owner).
CREATE DATABASE groovemap OWNER groovemap;

-- 3. Extensions, connected to the new database (\c groovemap). pg_trgm is required.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
--    Optional, superuser only (pgvector is not a trusted extension); needed for artist embeddings.
CREATE EXTENSION IF NOT EXISTS vector;

-- 4. Optional: the embedding pipeline's role. The initializer grants it read access to
--    `graph` and read/write on the embedding tables once it exists.
CREATE ROLE embedding_pipeline NOLOGIN;
--    Let the pipeline's login role use it (replace pipeline_login with the pipeline's own login).
GRANT embedding_pipeline TO pipeline_login;
```

`embedding_pipeline` is cluster-wide, so on a server shared with another GrooveMap database it is
created once. To keep the owner but a different login for the runtime, use
`GRANT CREATE ON DATABASE groovemap TO groovemap;` and `GRANT CREATE ON SCHEMA public TO groovemap;`
instead of step 2's `OWNER`.

### Required PostgreSQL version

PostgreSQL **18** or newer. The schema uses the built-in `uuidv7()` introduced in 18; the required
integration tier runs PostgreSQL 18 and the stack pins 18. PostgreSQL 19 is exercised by an
advisory tier only.

### Connection budget

Plan `max_connections` (and any `ALTER ROLE ... CONNECTION LIMIT`) from the pool ceilings of the
stack's services. Each connection past the pool minimum is opened on demand, so size for the
maximum:

| Service | Ceiling | Setting |
| --- | --- | --- |
| `tableinator` (discogs-sql-loader) | 12 | `POSTGRES_POOL_MAX_SIZE` |
| `brainztableinator` (musicbrainz-sql-loader) | 12 | `POSTGRES_POOL_MAX_SIZE` |
| `catalog-api` | 8, plus 4 for its projection pool | `POSTGRES_POOL_MAX_SIZE` |
| `analytics-engine` insights | 4 | `POSTGRES_POOL_MAX_SIZE` |
| `schema-init` (this image) | 5, only while it runs | fixed |
| one-shot jobs (embedding pipeline 1, ledger backfill 2, reattach 2) | 1-2 each | fixed |

The always-on services sum to 40 connections (44 with the projection pool); with one-shots and
`schema-init` running, peak is under 60. Provide at least 60 usable connections for GrooveMap,
on top of `superuser_reserved_connections` and whatever other applications on the shared server
need, or lower the pool maximums. Per-replica ceilings multiply with replica count.

## The bootstrap fill

The initializer never writes a graph row — applying the schema declares
`graph.bootstrap_fill()` and stops there, so a fresh database's `graph` relations start empty.
An operator who needs those relations populated once, in an environment that already holds the
`artists`/`labels`/`masters`/`releases`/`musicbrainz` documents but has not yet run a loader,
calls the function directly against the target database:

```sql
SELECT * FROM graph.bootstrap_fill();
```

**It is not authoritative.** `discogs-sql-loader` and `musicbrainz-sql-loader` own every
relation it touches and supersede whatever it wrote the first time either one runs; where the
fill and a loader disagree, the loader is right. Nothing in this image calls it, and no
environment variable enables it — it exists only for an operator to invoke by hand before the
loaders have run, so a `catalog-api` read rewrite is not blocked on the dual-write. See
[the bootstrap fill](architecture.md#the-bootstrap-fill) for the fill order, the emptying and
refilling behavior, and its one known divergence from the loaders.

## Image and operational behavior

The published image is `ghcr.io/groovemap-music/database-schema`. It runs as numeric user and
group `1000:1000`, declares `/logs` as its volume, and launches `database-schema` directly as
its entrypoint. It exposes no port and defines no Docker health check: completion status is the
only health signal. A zero exit means database creation (when needed) and both schema families
succeeded; any connection, connectivity, or statement failure produces a nonzero exit.

Image builds accept an exact 40-character lowercase Git commit as `VCS_REF` and refuse modified
tracked source. The resulting OCI labels record that revision, the package version, the source
repository, and the MIT license. Release automation publishes only from an approved `v*` tag;
building locally with `just image` never publishes or applies a live schema.

## Telemetry

Metrics and traces are two independent signals over one shared OTLP/HTTP endpoint. There is no
Prometheus scrape endpoint. Either can be switched off without touching the other, and when
`OTEL_EXPORTER_OTLP_ENDPOINT` is unset -- the default in development and in `just check` -- both
fall back to no-op providers and the initializer behaves exactly as it does without the `otel`
extra: telemetry never fails startup, never blocks, and never changes the process exit code.

Because this is a one-shot job, everything recorded during the run would be lost at exit
without an explicit flush. Telemetry is shut down in a `finally` block that covers the success
path and every failure path, including a run that never reaches schema application, and that
shutdown force-flushes the tracer provider and then the meter provider.

### Metrics

| Metric | Kind | Attributes | Source |
| --- | --- | --- | --- |
| `groovemap.schema_init.duration` | histogram, seconds | `store` (`postgresql`, `neo4j`, `all`), `outcome` (`success`, `failure`) | this service, once per store plus one `store=all` measurement covering the whole run |
| `db.client.operation.duration` | histogram, seconds | `db.system.name`, `db.operation.name`, `error.type` on failure | the runtime's PostgreSQL pool and Neo4j driver |
| `groovemap.pipeline.reconnects` | counter | `system` | the runtime's resilient connection wrappers |
| `groovemap.pipeline.circuit_breaker.state` | observable gauge | `system` | the runtime's circuit breakers |
| `groovemap.runtime.event_loop.lag` | histogram, seconds | none | the event-loop monitor, sampled once a second |
| `process.*` and `cpython.gc.*` | observable counters and gauges | see the runtime contract | the runtime's process view, installed by `setup_telemetry` |

The process view covers CPU time and utilization, resident and virtual memory, thread count,
open file descriptors, context switches, and garbage collection. It is process-scoped only: no
`system.*` host metric is reported, because the host is scraped once by node-exporter.

The event-loop monitor runs because this initializer does its work on an asyncio loop. It is
started from that running loop right after telemetry is configured and cancelled during
shutdown, so the lag histogram covers exactly the schema application.

### Spans

| Span | Kind | Attributes |
| --- | --- | --- |
| `schema_init postgresql`, `schema_init neo4j` | `INTERNAL` | `store`, `outcome`, `error.type` on a failure that raised |
| `{db.operation.name} {db.system.name}`, for example `execute postgresql` | `CLIENT` | `db.system.name`, `db.operation.name`, `error.type` on failure |

Each store's `schema_init` span is a trace root, and the database spans the runtime's pool and
driver open on their own nest underneath it. The two stores initialize concurrently and each
one is therefore its own trace, which is what lets an operator read a single store's
initialization without untangling the other. There is no `schema_init all` span: the overall
run is a metric only, because wrapping the stores in a parent span would stop each store's span
from being a root.

A store that did not initialize carries `outcome=failure` and span status `ERROR`, plus
`error.type` -- the exception's class name -- when the failure raised rather than being a count
of rejected statements. Nothing else is attached: no statement, message, stack trace, or
identifier. The SDK's automatic exception recording is switched off for that reason, since it
would otherwise add a span event holding the message and the traceback and copy the message
into the span's status description.

Call counts and durations per span name are derived by the collector's span-metrics connector.
This service never emits them itself.
