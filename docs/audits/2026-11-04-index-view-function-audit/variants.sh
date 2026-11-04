#!/usr/bin/env bash
# usage: variants.sh <out.tsv> [variant...]
set -u
OUT=$1; shift
psqlc(){ docker exec -i gm-wclt-pg psql -U groovemap -d groovemap -v ON_ERROR_STOP=1 -Atq "$@"; }
now(){ python3 -c 'import time;print(time.time())'; }
create(){ psqlc -c "DROP TABLE IF EXISTS bench.c; CREATE TABLE bench.c (person_name text NOT NULL, release_id text NOT NULL, role text NOT NULL, role_category text GENERATED ALWAYS AS (${FN:-bench.role_cat_pl}(role)) STORED);"; }
K="CREATE UNIQUE INDEX c_key ON bench.c (release_id, graph.text_key(person_name), graph.text_key(role))"
H="CREATE INDEX c_hash ON bench.c USING hash (person_name)"
R="CREATE INDEX c_role ON bench.c (role_category, release_id)"
PK060="ALTER TABLE bench.c ADD PRIMARY KEY (person_name, release_id, role)"
REV060="CREATE INDEX c_rev ON bench.c (release_id, person_name)"
R060="CREATE INDEX c_role ON bench.c (role_category, person_name)"
run(){ # name, pre-index DDL, post-index DDL
  local name=$1 pre=$2 post=$3
  create
  [ -n "$pre" ] && psqlc -c "$pre"
  psqlc -c "CHECKPOINT"
  local l0 l1 l2 t0 t1 t2
  l0=$(psqlc -c "select pg_current_wal_lsn()")
  t0=$(now); psqlc -c "CALL bench.load('bench.c', 260, ${MAXG:-5000000})"; t1=$(now)
  l1=$(psqlc -c "select pg_current_wal_lsn()")
  [ -n "$post" ] && psqlc -c "$post"
  t2=$(now)
  l2=$(psqlc -c "select pg_current_wal_lsn()")
  local sizes
  sizes=$(psqlc -F' ' -c "select pg_table_size('bench.c')/1048576, pg_indexes_size('bench.c')/1048576, (select count(*) from bench.c), pg_wal_lsn_diff('$l1','$l0')::bigint/1048576, pg_wal_lsn_diff('$l2','$l1')::bigint/1048576")
  local per; per=$(psqlc -F'=' -c "select string_agg(c.relname||'='||pg_relation_size(c.oid)/1048576, ',') from pg_index i join pg_class c on c.oid=i.indexrelid where i.indrelid='bench.c'::regclass")
  python3 - "$name" "$t0" "$t1" "$t2" "$sizes" "$per" >> "$OUT" <<'PY'
import sys
n,t0,t1,t2,s,p=sys.argv[1:7]; h,i,r,w1,w2=s.split()
print(f"{n}\tload_s={float(t1)-float(t0):.1f}\tpost_s={float(t2)-float(t1):.1f}\ttotal_s={float(t2)-float(t0):.1f}\theap_mb={h}\tidx_mb={i}\trows={r}\twal_load_mb={w1}\twal_post_mb={w2}\t{p}")
PY
  tail -1 "$OUT"
}
for v in "$@"; do case $v in
 heap)   run heap   "" "";;
 prop)   run prop   "CREATE UNIQUE INDEX c_key ON bench.c (release_id, graph.credit_key(person_name, role)); $R" "";;
 propk)  run propk  "CREATE UNIQUE INDEX c_key ON bench.c (release_id, graph.credit_key(person_name, role))" "";;
 key1)   run key1   "CREATE UNIQUE INDEX c_key ON bench.c (release_id, graph.text_key(length(person_name) || chr(1) || person_name || chr(1) || role))" "";;
 ship1)  run ship1  "CREATE UNIQUE INDEX c_key ON bench.c (release_id, graph.text_key(length(person_name) || chr(1) || person_name || chr(1) || role)); $H; $R" "";;
 key)    run key    "$K" "";;
 keyh)   run keyh   "$K; $H" "";;
 keyr)   run keyr   "$K; $R" "";;
 ship)   run ship   "$K; $H; $R" "";;
 v060)   run v060   "$PK060; $REV060; $R060" "";;
 defer2) run defer2 "$K" "$H; $R";;
 defer3) run defer3 "" "$K; $H; $R";;
esac; done
