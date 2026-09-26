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
| `NEO4J_HOST` | `localhost` | Neo4j hostname or full connection URI |
| `NEO4J_PORT` | `7687` | Bolt port when the host is not a full URI |
| `NEO4J_USERNAME` | `neo4j` | Neo4j user |
| `NEO4J_PASSWORD` | `groovemap` | Neo4j password |
| `NEO4J_TLS_ENABLED` | `false` | Enable encrypted Bolt transport |
| `NEO4J_TLS_VERIFY` | `true` | Verify the Neo4j server certificate when TLS is enabled |
| `SCHEMA_PROPERTY_GRAPH` | `disabled` | `enabled` declares the `graph.catalog` property graph. Requires PostgreSQL 19 or later; skipped with a logged reason on an older server |
| `LOG_LEVEL` | runtime default | Structured-log level |
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
must persist beyond the one-shot container.

## The property graph switch

`SCHEMA_PROPERTY_GRAPH` is off unless it is set to `enabled` — `true`, `1`, `yes`, `on`, and
`enable` are accepted as well, and everything else, including `disabled` and an empty value,
leaves it off. With it on, the initializer declares `graph.catalog` over the `graph` schema's
views after the rest of the schema has been applied. See
[the property graph](architecture.md#property-graph) for the statement itself.

Two things must both be true before the server is asked anything about the graph: the switch
is on, and the server reports `server_version_num` of at least `190000`. Either one failing logs
a single line naming which one and is not an error — the switch on a PostgreSQL 18 server is a
supported configuration, not a misconfiguration, which is what lets one deployment manifest
cover both engines during a version migration.

With both true, a missing `graph.catalog` is created, and an existing one this schema declared
is re-declared in one transaction whenever its definition fingerprint or its elements no longer
match the current declaration, so a new declaration rolls out on the next run. A current graph
is left alone, and so is a table, a view, or a property graph carrying a comment this schema did
not write; each logs one line. A statement that fails is counted like any other failed schema
statement and makes the run exit nonzero, leaving the previous graph in place. See
[the property graph](architecture.md#when-it-is-applied) for the fingerprint and the locking.

Turning the switch off does not remove an existing graph: this initializer only ever replaces
its own graph, and only with the switch on.

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
