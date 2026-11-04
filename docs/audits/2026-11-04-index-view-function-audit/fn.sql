SET jit = off;
\timing on
CREATE TEMP TABLE r AS SELECT role FROM bench.credit_src WHERE g <= 100000;
\echo baseline (length only)
SELECT sum(length(role)) FROM r;
\echo shipped credit_role_category, 100k calls
SELECT count(*) FILTER (WHERE graph.credit_role_category(role) = 'other') FROM r;
\echo shipped body computing normalized once per call: plpgsql twin
SELECT replace(replace(replace(pg_get_functiondef('graph.credit_role_category(text)'::regprocedure),
  'graph.credit_role_category','bench.role_cat_pl'),'LANGUAGE sql','LANGUAGE plpgsql'),
  'FROM (SELECT btrim(lower(raw_role)) AS role) AS normalized','') AS d \gset
SELECT regexp_replace(regexp_replace(:'d', 'AS \$function\$\s+SELECT CASE', $r$AS $function$ DECLARE normalized_role text := btrim(lower(raw_role)); BEGIN RETURN CASE$r$), 'END\s+\$function\$', $r$END; END $function$$r$) AS d2 \gset
SELECT replace(:'d2', 'normalized.role', 'normalized_role') AS d3 \gset
:d3 ;
\echo plpgsql twin, 100k calls
SELECT count(*) FILTER (WHERE bench.role_cat_pl(role) = 'other') FROM r;
\echo equality over the role sample
SELECT count(*) FROM (SELECT DISTINCT role FROM r) x WHERE bench.role_cat_pl(role) IS DISTINCT FROM graph.credit_role_category(role);
