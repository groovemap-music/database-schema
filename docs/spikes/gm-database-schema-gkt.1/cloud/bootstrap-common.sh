#!/usr/bin/env bash
# Everything both cloud instances need before either bootstrap runs.
#
# Throwaway spike harness for gm-database-schema-gkt.1.
set -euo pipefail

root=/opt/bench
mkdir -p "$root/results"

# cloud-init may still be installing. Wait for it rather than racing it.
for _ in $(seq 1 120); do
    [[ -f "$root/.cloud-init-done" ]] && command -v docker >/dev/null && break
    sleep 5
done
command -v docker >/dev/null || { echo "docker never arrived" >&2; exit 1; }

# The two drivers the runner needs, and nothing else. A virtualenv rather than
# --break-system-packages, so the instance's own python3 is left as it was and
# the calibration figures are not measuring a mutated interpreter.
if [[ ! -x "$root/venv/bin/python" ]]; then
    python3 -m venv "$root/venv"
    "$root/venv/bin/pip" install --quiet --upgrade pip
    "$root/venv/bin/pip" install --quiet 'psycopg[binary]>=3.3' 'neo4j>=6'
fi

"$root/venv/bin/python" "$root/harness/bench/calibration.py" \
    --output "$root/results/calibration-cloud.json" \
    --label "${BENCH_LABEL:-IBM Cloud VPC instance}"
echo "common bootstrap complete"
