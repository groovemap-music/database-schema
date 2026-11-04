#!/usr/bin/env bash
# Entity-table load and hash-gated touch, against the shipped releases index set and reduced sets.
set -u
S=$(dirname "$0"); OUT=$S/entity.tsv
psqlc(){ docker exec -i gm-wclt-pg psql -U groovemap -d groovemap -v ON_ERROR_STOP=1 -Atq "$@"; }
now(){ python3 -c 'import time;print(time.time())'; }
DDL_T="DROP TABLE IF EXISTS bench.r; CREATE TABLE bench.r (LIKE public.releases INCLUDING DEFAULTS INCLUDING CONSTRAINTS INCLUDING GENERATED); ALTER TABLE bench.r ADD PRIMARY KEY (data_id)"
I_HASH="CREATE INDEX r_hash ON bench.r (hash)"
I_UPD="CREATE INDEX r_upd ON bench.r (updated_at)"
I_BRIN="CREATE INDEX r_upd ON bench.r USING brin (updated_at)"
I_GM="CREATE INDEX r_gm ON bench.r (gm_item_id)"
I_TITLE="CREATE INDEX r_title ON bench.r USING hash ((data->>'title'))"
I_YEAR="CREATE INDEX r_year ON bench.r ((data->>'year'))"
I_COUNTRY="CREATE INDEX r_country ON bench.r ((data->>'country'))"
I_GENRES="CREATE INDEX r_genres ON bench.r USING GIN ((data->'genres'))"
I_LABELS="CREATE INDEX r_labels ON bench.r USING GIN ((data->'labels'))"
I_IDENT="CREATE INDEX r_ident ON bench.r USING GIN ((data->'identifiers'))"
I_COMP="CREATE INDEX r_comp ON bench.r USING GIN ((data->'companies'))"
I_MEDIA="CREATE INDEX r_media ON bench.r USING GIN ((media->'families'))"
I_FTS="CREATE INDEX r_fts ON bench.r USING GIN (to_tsvector('english', COALESCE(data->>'title', '')))"
ALL="$I_HASH; $I_UPD; $I_GM; $I_TITLE; $I_YEAR; $I_COUNTRY; $I_GENRES; $I_LABELS; $I_IDENT; $I_COMP; $I_MEDIA; $I_FTS"
KEEP="$I_UPD; $I_GM; $I_COUNTRY; $I_FTS"
KEEPB="$I_BRIN; $I_GM; $I_COUNTRY; $I_FTS"
step(){ # name proc
  local n=$1 p=$2 l0 l1 t0 t1
  psqlc -c "CHECKPOINT"
  l0=$(psqlc -c "select pg_current_wal_lsn()")
  t0=$(now); psqlc -c "CALL $p"; t1=$(now)
  l1=$(psqlc -c "select pg_current_wal_lsn()")
  psqlc -F' ' -c "select '$n', round(($t1-$t0)::numeric,1), pg_wal_lsn_diff('$l1','$l0')::bigint/1048576, pg_table_size('bench.r')/1048576, pg_indexes_size('bench.r')/1048576, (select n_tup_upd from pg_stat_user_tables where relname='r'), (select n_tup_hot_upd from pg_stat_user_tables where relname='r'), pg_stat_get_xact_tuples_updated('bench.r'::regclass)" | tee -a "$OUT"
}
for v in "$@"; do
  case $v in
   ship) D="$ALL";; keep) D="$KEEP";; keepb) D="$KEEPB";; pk) D="";;
  esac
  psqlc -c "$DDL_T"
  [ -n "$D" ] && psqlc -c "$D"
  step "${v}_load" "bench.load_docs('bench.r')"
  sleep 2
  step "${v}_touch" "bench.touch_docs('bench.r')"
done
