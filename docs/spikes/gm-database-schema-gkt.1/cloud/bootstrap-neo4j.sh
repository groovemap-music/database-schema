#!/usr/bin/env bash
# Build the Neo4j side of the benchmark ON a cloud instance.
#
# Throwaway spike harness for gm-database-schema-gkt.1. Same contract as
# `bootstrap-postgres.sh`: root on a bare Ubuntu instance, harness already
# rsynced, catalog regenerated from the seed rather than uploaded.
set -euo pipefail

scale="${1:?usage: bootstrap-neo4j.sh <scale>}"
harness=/opt/bench/harness

# 32 GiB machine. Heap and page cache are scaled with the instance exactly as the
# PostgreSQL settings are, so neither engine is the one that got the memory.
NEO4J_HEAP=8G NEO4J_PAGECACHE=12G \
  "$harness/neo4j-build.sh" "$scale" /opt/bench/results "/opt/bench/csv-$scale"
rm -rf "/opt/bench/csv-$scale"
echo "neo4j ready at scale $scale"
