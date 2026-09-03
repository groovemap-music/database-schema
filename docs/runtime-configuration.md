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
| `LOG_LEVEL` | runtime default | Structured-log level |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | OTLP/HTTP collector base URL, for example `http://otel-collector:4318`. Unset disables metrics export |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | falls back to `OTEL_EXPORTER_OTLP_ENDPOINT` | Metrics-only endpoint override |
| `OTEL_METRICS_EXPORTER` | `otlp` | Set to `none` to force metrics export off even with an endpoint configured |
| `OTEL_METRIC_EXPORT_INTERVAL` | SDK default | Push interval in milliseconds |
| `OTEL_SERVICE_NAME` | `schema-init` | Overrides the `service.name` resource attribute |
| `OTEL_RESOURCE_ATTRIBUTES` | empty | Extra resource attributes, for example `service.namespace=groovemap,deployment.environment.name=dev` |

Secrets also support the Docker secret-file convention. Set
`POSTGRES_USERNAME_FILE`, `POSTGRES_PASSWORD_FILE`, or `NEO4J_PASSWORD_FILE` to a mounted
file path instead of placing the corresponding value directly in the environment.

The image writes its file log to `/logs/database-schema.log`; mount `/logs` when the log
must persist beyond the one-shot container.

## Telemetry

The initializer records one `groovemap.schema_init.duration` histogram measurement (seconds)
around each store's initialization, tagged `store=postgresql|neo4j` and
`outcome=success|failure`, plus one overall measurement tagged `store=all` covering the whole
run. Metrics export over OTLP/HTTP; there is no Prometheus scrape endpoint. When
`OTEL_EXPORTER_OTLP_ENDPOINT` is unset (the default in development and in `just check`), the
initializer installs a no-op meter provider and behaves exactly as it does without the `otel`
extra: telemetry never fails startup, never blocks, and never changes the process exit code.
Telemetry is flushed and shut down before the process exits, including on failure, so the last
measurement is not dropped.
