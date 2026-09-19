#!/usr/bin/env bash
# Build the PostgreSQL side of the benchmark ON a cloud instance.
#
# Throwaway spike harness for gm-database-schema-gkt.1. Runs as root on a bare
# Ubuntu VPC instance that cloud-init has given Docker, after `../run.sh --cloud`
# has rsynced this directory to /opt/bench/harness.
#
# The catalog is REGENERATED here rather than uploaded. gm-database-schema-9c8.1's
# generator is a pure function of its seed, so the instance reproduces the same
# bytes the laptop did in about the time it would take to upload them, and the
# upload is a schema dump of a few hundred kilobytes instead of six gigabytes.
set -euo pipefail

scale="${1:?usage: bootstrap-postgres.sh <scale>}"
root=/opt/bench
harness="$root/harness"
export PGPASSWORD=spike-password

# 32 GiB machine: a quarter to shared_buffers, three quarters assumed cached.
# Every other setting is the one both sibling spikes held constant, so the only
# difference between the laptop numbers and these is the machine.
docker rm --force gmgkt1-pg >/dev/null 2>&1 || true
docker volume rm --force gmgkt1-pgdata >/dev/null 2>&1 || true
docker volume create gmgkt1-pgdata >/dev/null
docker run --detach --name gmgkt1-pg \
  --publish 127.0.0.1:55434:5432 --volume gmgkt1-pgdata:/var/lib/postgresql \
  --env POSTGRES_USER=groovemap --env POSTGRES_PASSWORD=spike-password \
  --env POSTGRES_DB=postgres --shm-size=2g \
  postgres:19beta3-alpine@sha256:b1692e50613a21e61c424859f943b9e193ae73e5a8c68abd5382dfb235bf15fc \
  -c shared_buffers=8GB -c effective_cache_size=24GB -c work_mem=256MB \
  -c maintenance_work_mem=2GB -c max_parallel_workers_per_gather=4 \
  -c max_parallel_workers=8 -c random_page_cost=1.1 -c track_io_timing=on -c jit=off >/dev/null

for _ in $(seq 1 90); do docker exec gmgkt1-pg pg_isready -U groovemap -q && break; sleep 2; done

psql_run() {
    docker exec -i -e PGPASSWORD="$PGPASSWORD" gmgkt1-pg \
        psql -U groovemap -d "${PGDB:-groovemap}" -v ON_ERROR_STOP=1 -q "$@"
}

# The product schema arrives as a dump taken from a database the packaged
# initializer built, rather than by running the initializer here. The initializer
# needs the whole `groovemap_schema` distribution and its dependency tree; the
# dump is the same DDL, and using it means the cloud instance carries no Python
# package the measurement does not need.
echo "creating database and applying the product schema"
PGDB=postgres psql_run -c "SELECT 1" >/dev/null
PGDB=postgres psql_run -c "DROP DATABASE IF EXISTS groovemap" >/dev/null
PGDB=postgres psql_run -c "CREATE DATABASE groovemap" >/dev/null
psql_run -f - < "$harness/cloud/product-schema.sql" >/dev/null

echo "loading catalog at scale $scale"
PG_CONTAINER=gmgkt1-pg "$harness/../gm-database-schema-9c8.1/load-postgres.sh" "$scale"

echo "adding the artist-to-artist edges"
sib="$harness/../gm-database-schema-9c8.3"
psql_run -f - < "$sib/stage.sql"
python3 "$sib/artist_edges.py" --scale "$scale" \
  | docker exec -i -e PGPASSWORD="$PGPASSWORD" gmgkt1-pg \
      psql -U groovemap -d groovemap -v ON_ERROR_STOP=1 -q \
      -c '\copy path.artist_edge_stage (owner_id, element_id, block) FROM STDIN WITH (FORMAT csv)'
psql_run -f - < "$sib/augment.sql"

echo "building the pf schema"
PG_CONTAINER=gmgkt1-pg "$harness/load.sh"
echo "postgres ready at scale $scale"
