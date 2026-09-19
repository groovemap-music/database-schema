#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# gm-database-schema-gkt.1 — the spike driver
# ═══════════════════════════════════════════════════════════════════════════
#
# Throwaway spike harness. ./README.md documents the whole sequence; this is the
# entry point for all of it. Its shape is adapted from `investigations/run.sh` on
# the db-alternatives branch of SimplicityGuy/discogsography: flags select a
# mode, positionals are the local mode's arguments, local is the default and
# needs no flag, and cloud is opt-in.
#
#   ./run.sh                       measure locally at the large scale
#   ./run.sh local small           measure locally at the small scale
#   ./run.sh --build small         build the local engines at a scale, no measuring
#   ./run.sh --report <dir>        compare a results directory and render its tables
#   ./run.sh --cloud               provision IBM Cloud, run both scales, fetch, destroy
#   ./run.sh --cloud-keep          the same without the destroy, for inspection
#   ./run.sh --cloud-destroy       destroy and verify nothing survives
#   ./run.sh --clean               remove every local container and volume this created
#
# ENVIRONMENT
#   Local:  nothing. Docker is the only requirement.
#   Cloud:  ~/.config/groovemap/ibmcloud.env, holding IC_API_KEY, IC_REGION,
#           IC_RESOURCE_GROUP and GROOVEMAP_BENCH_SSH_KEY. That file is outside
#           the repository, it is read into the environment here, and no value
#           from it is ever echoed, written into infra/ or committed.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
RESULTS_ROOT="${GMGKT1_RESULTS:-${TMPDIR:-/tmp}/gmgkt1-results}"

PG_CONTAINER=gmgkt1-pg
PG_VOLUME=gmgkt1-pgdata
PG_PORT=55434
NEO4J_CONTAINER=gmgkt1-neo4j
NEO4J_VOLUME=gmgkt1-neo4j-data
NEO4J_PORT=57689
PG_IMAGE='postgres:19beta3-alpine@sha256:b1692e50613a21e61c424859f943b9e193ae73e5a8c68abd5382dfb235bf15fc'
DSN="postgresql://groovemap:spike-password@127.0.0.1:${PG_PORT}/groovemap"
PREFIX=gmgkt1

# ═══════════════════════════════════════════════════════════════════════════
# Local mode
# ═══════════════════════════════════════════════════════════════════════════

scale_name() { case "$1" in small) echo fixture ;; large) echo synthetic ;; *) echo "unknown scale $1" >&2; exit 1 ;; esac; }

# Free space is checked before anything large is written, because the failure
# mode otherwise is a half-loaded catalog and a machine with no room to clean it
# up. gm-database-schema-9c8.3 lost 3.4 GB to one unbounded statement.
require_disk() {
    local need_gb="$1" avail
    avail="$(df -g "$HOME" | awk 'NR==2 {print $4}')"
    if (( avail < need_gb )); then
        echo "only ${avail} GiB free; ${need_gb} GiB wanted. Stopping rather than filling the disk." >&2
        exit 1
    fi
}

build_postgres() {
    local scale="$1" generator_scale
    generator_scale="$(scale_name "$scale")"
    require_disk 12

    docker rm --force "$PG_CONTAINER" >/dev/null 2>&1 || true
    docker volume rm --force "$PG_VOLUME" >/dev/null 2>&1 || true
    docker volume create "$PG_VOLUME" >/dev/null
    # Every setting here is the one gm-database-schema-9c8.1 and -9c8.3 held
    # constant, so the three documents' numbers can be read against each other.
    docker run --detach --name "$PG_CONTAINER" \
        --publish "127.0.0.1:${PG_PORT}:5432" --volume "$PG_VOLUME:/var/lib/postgresql" \
        --env POSTGRES_USER=groovemap --env POSTGRES_PASSWORD=spike-password \
        --env POSTGRES_DB=postgres --shm-size=1g "$PG_IMAGE" \
        -c shared_buffers=1536MB -c effective_cache_size=4GB -c work_mem=128MB \
        -c maintenance_work_mem=512MB -c max_parallel_workers_per_gather=2 \
        -c max_parallel_workers=2 -c random_page_cost=1.1 -c track_io_timing=on -c jit=off >/dev/null
    for _ in $(seq 1 90); do docker exec "$PG_CONTAINER" pg_isready -U groovemap -q && break; sleep 2; done

    SCHEMA_PROPERTY_GRAPH=enabled uv run --project "$REPO" python -c "
import asyncio
from groovemap_schema import initializer
params = {'host': '127.0.0.1', 'port': ${PG_PORT}, 'dbname': 'groovemap',
          'user': 'groovemap', 'password': 'spike-password'}
initializer._ensure_postgres_database(params)
raise SystemExit(0 if asyncio.run(initializer._apply_postgres_schema(params)) else 1)
"
    PG_CONTAINER="$PG_CONTAINER" "$HERE/../gm-database-schema-9c8.1/load-postgres.sh" "$generator_scale"

    local sib="$HERE/../gm-database-schema-9c8.3"
    local psql=(docker exec -i -e PGPASSWORD=spike-password "$PG_CONTAINER" psql -U groovemap -d groovemap -v ON_ERROR_STOP=1 -q)
    "${psql[@]}" -f - < "$sib/stage.sql"
    python3 "$sib/artist_edges.py" --scale "$generator_scale" \
        | "${psql[@]}" -c '\copy path.artist_edge_stage (owner_id, element_id, block) FROM STDIN WITH (FORMAT csv)'
    "${psql[@]}" -f - < "$sib/augment.sql"
    PG_CONTAINER="$PG_CONTAINER" "$HERE/load.sh"
}

build_neo4j() {
    local scale="$1"
    require_disk 10
    "$HERE/neo4j-build.sh" "$(scale_name "$scale")" "$RESULTS_ROOT/$scale-local" "$HOME/.cache/gmgkt1-spike/csv-$(scale_name "$scale")"
}

calibrate_local() {
    local out="$1"
    mkdir -p "$(dirname "$out")"
    # Inside a container on the Docker VM, not on macOS: the VM is what runs the
    # queries, and it has a fraction of the host's cores.
    docker run --rm -i -w /tmp python:3.13-slim python - \
        --output /dev/stdout --label "Docker Desktop VM (local mode)" \
        < "$HERE/bench/calibration.py" > "$out" 2>/dev/null
}

measure_local() {
    local scale="$1" out="$RESULTS_ROOT/$scale-local"
    mkdir -p "$out"
    calibrate_local "$out/calibration-local.json"

    # Each engine is measured with the other STOPPED. On a 2 vCPU, 7.7 GiB Docker
    # VM the two of them together do not fit in the page cache, and a run with the
    # other engine resident measures the eviction rather than the search.
    docker stop "$NEO4J_CONTAINER" >/dev/null 2>&1 || true
    docker start "$PG_CONTAINER" >/dev/null 2>&1 || true
    for _ in $(seq 1 60); do docker exec "$PG_CONTAINER" pg_isready -U groovemap -q && break; sleep 2; done
    ( cd "$HERE" && uv run --project "$REPO" python -m bench.runner \
        --engine postgres --scale "$scale" --mode local --dsn "$DSN" \
        --outdir "$out" --calibration "$out/calibration-local.json" )

    docker stop "$PG_CONTAINER" >/dev/null 2>&1 || true
    docker start "$NEO4J_CONTAINER" >/dev/null 2>&1 || true
    for _ in $(seq 1 90); do docker exec "$NEO4J_CONTAINER" cypher-shell -u neo4j -p spike-password "RETURN 1" >/dev/null 2>&1 && break; sleep 2; done
    ( cd "$HERE" && uv run --project "$REPO" python -m bench.runner \
        --engine neo4j --scale "$scale" --mode local --neo4j-uri "bolt://127.0.0.1:${NEO4J_PORT}" \
        --outdir "$out" --calibration "$out/calibration-local.json" )

    report_dir "$out" "$scale" local
}

report_dir() {
    local out="$1" scale="$2" mode="$3"
    ( cd "$HERE" && uv run --project "$REPO" python -m bench.report \
        --postgres "$out/postgres-$scale-$mode.json" --neo4j "$out/neo4j-$scale-$mode.json" \
        --output "$out/report.md" >/dev/null )
    ( cd "$HERE" && uv run --project "$REPO" python -m bench.compare \
        --postgres "$out/postgres-$scale-$mode.json" --neo4j "$out/neo4j-$scale-$mode.json" \
        --output "$out/verdict.json" ) || true
    echo "report: $out/report.md"
}

run_local() {
    local scale="${1:-large}"
    build_postgres "$scale"
    build_neo4j "$scale"
    measure_local "$scale"
}

# ═══════════════════════════════════════════════════════════════════════════
# Cloud mode — IBM Cloud VPC through Terraform
# ═══════════════════════════════════════════════════════════════════════════

load_credentials() {
    local env_file="$HOME/.config/groovemap/ibmcloud.env"
    [[ -f "$env_file" ]] || { echo "missing $env_file" >&2; exit 1; }
    # shellcheck disable=SC1090
    set +u; . "$env_file"; set -u
    : "${IC_API_KEY:?}" "${IC_REGION:?}" "${IC_RESOURCE_GROUP:?}" "${GROOVEMAP_BENCH_SSH_KEY:?}"
    [[ -f "${GROOVEMAP_BENCH_SSH_KEY}.pub" ]] || { echo "no public half beside ${GROOVEMAP_BENCH_SSH_KEY}" >&2; exit 1; }
    # Terraform reads the key from the environment. It is never written to a
    # tfvars file, and infra/.gitignore refuses one.
    export TF_VAR_ibmcloud_api_key="$IC_API_KEY"
    export TF_VAR_region="$IC_REGION"
    export TF_VAR_resource_group_id="$IC_RESOURCE_GROUP"
    export TF_VAR_ssh_public_key="$(cat "${GROOVEMAP_BENCH_SSH_KEY}.pub")"
    export TF_VAR_prefix="$PREFIX"
}

egress_cidr() {
    local ip
    ip="$(curl -fsS --max-time 10 https://checkip.amazonaws.com | tr -d '[:space:]')"
    [[ -n "$ip" ]] || { echo "could not determine this machine's egress address" >&2; exit 1; }
    echo "$ip/32"
}

tf() { terraform -chdir="$HERE/infra" "$@"; }

ssh_to() {
    local host="$1"; shift
    ssh -i "$GROOVEMAP_BENCH_SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o LogLevel=ERROR -o ConnectTimeout=15 -o ServerAliveInterval=30 "${CLOUD_USER:-ubuntu}@$host" "$@"
}

cloud_provision() {
    load_credentials
    export TF_VAR_allowed_ssh_cidr="$(egress_cidr)"
    echo "restricting SSH to ${TF_VAR_allowed_ssh_cidr}"
    tf init -input=false >/dev/null
    tf apply -input=false -auto-approve
    tf output -json > "$RESULTS_ROOT/inventory.json"
}

cloud_engine_ip() { jq -r ".instances.value.$1" "$RESULTS_ROOT/inventory.json"; }

cloud_wait() {
    local host="$1"
    for _ in $(seq 1 60); do
        ssh_to "$host" true 2>/dev/null && return 0
        sleep 10
    done
    echo "instance $host never accepted SSH" >&2; return 1
}

# The dump the cloud bootstrap replays is regenerated whenever a local container
# with the product schema is up, so a cloud run cannot replay a stale copy of a
# schema the initializer has since changed.
refresh_product_schema() {
    local target="$HERE/cloud/product-schema.sql"
    docker exec "$PG_CONTAINER" pg_isready -U groovemap -q 2>/dev/null || {
        echo "no local PostgreSQL running; reusing the committed $target" >&2
        return 0
    }
    local head body
    head="$(sed -n '1,/^$/p' "$target")"
    body="$(docker exec "$PG_CONTAINER" pg_dump -U groovemap -d groovemap --schema-only \
              --no-owner --no-privileges --exclude-schema=graph_mat \
              --exclude-schema=path --exclude-schema=pf)" || return 0
    printf '%s%s\n' "$head" "$body" > "$target"
    echo "refreshed $target from the running container"
}

cloud_push() {
    local host="$1"
    # Idempotent, and also the repair path for an instance whose cloud-init ran
    # before this script learned that the login account is `ubuntu`.
    #
    # The wait is not belt-and-braces. `cloud-init status` reports done when the
    # runcmd list has been dispatched, and the `docker.io` postinst that creates
    # the `docker` group can still be running; a first attempt at this failed on
    # both instances with "group 'docker' does not exist" and took the whole run
    # down with it.
    ssh_to "$host" "
        for _ in \$(seq 1 60); do getent group docker >/dev/null && break; sleep 5; done
        getent group docker >/dev/null || { echo 'docker group never appeared' >&2; exit 1; }
        sudo -n mkdir -p /opt/bench/results
        sudo -n chown -R ubuntu:ubuntu /opt/bench
        sudo -n usermod -aG docker ubuntu
        mkdir -p /opt/bench/harness
        for _ in \$(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 5; done
        docker info >/dev/null 2>&1 || { echo 'docker not usable as ubuntu' >&2; exit 1; }
    "
    # The harness plus the two sibling spikes it reuses. Results and Terraform
    # state stay behind; nothing with a credential in it is ever pushed.
    rsync -az --delete -e "ssh -i $GROOVEMAP_BENCH_SSH_KEY -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR" \
        --exclude 'results/' --exclude 'infra/' --exclude '__pycache__/' \
        "$HERE/" "ubuntu@$host:/opt/bench/harness/"
    rsync -az -e "ssh -i $GROOVEMAP_BENCH_SSH_KEY -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR" \
        --exclude '__pycache__/' --exclude 'results/' \
        "$HERE/../gm-database-schema-9c8.1" "$HERE/../gm-database-schema-9c8.3" "ubuntu@$host:/opt/bench/"
}

cloud_run_engine() {
    local engine="$1" host="$2" profile="$3"
    cloud_wait "$host"
    cloud_push "$host"
    ssh_to "$host" "BENCH_LABEL='IBM Cloud VPC ${profile} (${engine})' bash /opt/bench/harness/cloud/bootstrap-common.sh"
    for scale in small large; do
        echo "── $engine / $scale on $host"
        ssh_to "$host" "bash /opt/bench/harness/cloud/bootstrap-${engine}.sh \$(case $scale in small) echo fixture;; large) echo synthetic;; esac)"
        if [[ "$engine" == postgres ]]; then
            ssh_to "$host" "cd /opt/bench/harness && PYTHONPATH=/opt/bench/harness /opt/bench/venv/bin/python -m bench.runner \
                --engine postgres --scale $scale --mode cloud --dsn '$DSN' \
                --outdir /opt/bench/results/$scale --calibration /opt/bench/results/calibration-cloud.json"
        else
            ssh_to "$host" "cd /opt/bench/harness && PYTHONPATH=/opt/bench/harness /opt/bench/venv/bin/python -m bench.runner \
                --engine neo4j --scale $scale --mode cloud --neo4j-uri bolt://127.0.0.1:${NEO4J_PORT} \
                --outdir /opt/bench/results/$scale --calibration /opt/bench/results/calibration-cloud.json"
        fi
    done
}

cloud_fetch() {
    local host="$1"
    for scale in small large; do
        mkdir -p "$RESULTS_ROOT/$scale-cloud"
        rsync -az -e "ssh -i $GROOVEMAP_BENCH_SSH_KEY -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR" \
            "ubuntu@$host:/opt/bench/results/$scale/" "$RESULTS_ROOT/$scale-cloud/" || true
    done
    rsync -az -e "ssh -i $GROOVEMAP_BENCH_SSH_KEY -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR" \
        "ubuntu@$host:/opt/bench/results/calibration-cloud.json" "$RESULTS_ROOT/" || true
}

run_cloud() {
    local keep="${1:-destroy}"
    mkdir -p "$RESULTS_ROOT"
    refresh_product_schema
    cloud_provision
    local profile; profile="$(jq -r '.profile.value' "$RESULTS_ROOT/inventory.json")"
    local pg_ip neo_ip; pg_ip="$(cloud_engine_ip postgres)"; neo_ip="$(cloud_engine_ip neo4j)"
    echo "postgres $pg_ip   neo4j $neo_ip   profile $profile"
    # The two engines are separate instances on an identical profile, so they
    # are built and measured CONCURRENTLY. Neither shares a CPU, a page cache or
    # a disk with the other, which is the thing the local mode cannot offer and
    # has to work around by stopping one engine to measure the other.
    cloud_run_engine postgres "$pg_ip" "$profile" > "$RESULTS_ROOT/cloud-postgres.log" 2>&1 &
    local pg_job=$!
    cloud_run_engine neo4j "$neo_ip" "$profile" > "$RESULTS_ROOT/cloud-neo4j.log" 2>&1 &
    local neo_job=$!
    local failed=0
    wait "$pg_job" || failed=1
    wait "$neo_job" || failed=1
    (( failed == 0 )) || echo "one or both engines reported a failure; see $RESULTS_ROOT/cloud-*.log" >&2
    cloud_fetch "$pg_ip"
    cloud_fetch "$neo_ip"
    for scale in small large; do
        [[ -f "$RESULTS_ROOT/$scale-cloud/postgres-$scale-cloud.json" ]] && report_dir "$RESULTS_ROOT/$scale-cloud" "$scale" cloud
    done
    [[ "$keep" == keep ]] || cloud_destroy
}

cloud_destroy() {
    load_credentials
    export TF_VAR_allowed_ssh_cidr="${TF_VAR_allowed_ssh_cidr:-0.0.0.0/32}"
    tf destroy -input=false -auto-approve
    # A clean state file is evidence about the state file. This asks the account.
    echo "── verifying nothing named ${PREFIX}- survives"
    ibmcloud is instances --output json 2>/dev/null | jq -r --arg p "$PREFIX-" '[.[]|select(.name|startswith($p))|.name]|if length==0 then "no instances" else "STILL RUNNING: \(.)" end'
    ibmcloud is floating-ips --output json 2>/dev/null | jq -r --arg p "$PREFIX-" '[.[]|select(.name|startswith($p))|.name]|if length==0 then "no floating ips" else "STILL ALLOCATED: \(.)" end'
    ibmcloud is vpcs --output json 2>/dev/null | jq -r --arg p "$PREFIX-" '[.[]|select(.name|startswith($p))|.name]|if length==0 then "no vpcs" else "STILL PRESENT: \(.)" end'
    ibmcloud is volumes --output json 2>/dev/null | jq -r --arg p "$PREFIX-" '[.[]|select(.name|startswith($p))|.name]|if length==0 then "no volumes" else "STILL PRESENT: \(.)" end'
}

run_clean() {
    docker rm --force "$PG_CONTAINER" "$NEO4J_CONTAINER" >/dev/null 2>&1 || true
    docker volume rm --force "$PG_VOLUME" "$NEO4J_VOLUME" gmgkt1-csv >/dev/null 2>&1 || true
    rm -rf "$HOME/.cache/gmgkt1-spike"
    # Terraform's local state after a destroy is a dead artefact that still holds
    # resource CRNs, and this repository's `secret-scan` gate scans the working
    # directory rather than only tracked files — so leaving it behind turns
    # `just check` red for the next person even though .gitignore keeps it out of
    # every commit. It is removed only when it describes nothing; a state that
    # still lists resources is left alone and reported.
    if [[ -f "$HERE/infra/terraform.tfstate" ]]; then
        local live
        live="$(terraform -chdir="$HERE/infra" state list 2>/dev/null | wc -l | tr -d ' ')"
        if [[ "$live" == "0" ]]; then
            rm -f "$HERE/infra/terraform.tfstate" "$HERE/infra/terraform.tfstate.backup"
            rm -rf "$HERE/infra/.terraform" "$HERE/infra/.terraform.lock.hcl"
            echo "reclaimed the emptied terraform state"
        else
            echo "infra/ state still lists $live resources; run --cloud-destroy first" >&2
        fi
    fi
    echo "removed only what this spike created; nothing else was pruned"
}

# ═══════════════════════════════════════════════════════════════════════════

case "${1:-}" in
    --help|-h)       sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
    --build)         build_postgres "${2:-large}"; build_neo4j "${2:-large}"; exit 0 ;;
    --report)        report_dir "${2:?usage: --report <dir>}" "${3:-large}" "${4:-local}"; exit 0 ;;
    --cloud)         run_cloud destroy; exit 0 ;;
    --cloud-keep)    run_cloud keep; exit 0 ;;
    --cloud-destroy) cloud_destroy; exit 0 ;;
    --clean)         run_clean; exit 0 ;;
    local)           shift; run_local "${1:-large}"; exit 0 ;;
    "")              run_local large; exit 0 ;;
    *)               echo "unknown mode: $1" >&2; sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 1 ;;
esac
