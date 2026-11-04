-- 5M credits, ~2.6 per release (51.0M/19.6M), dump order, power-law names, real-vocabulary roles.
DROP SCHEMA IF EXISTS bench CASCADE; CREATE SCHEMA bench;
CREATE UNLOGGED TABLE bench.roles AS
SELECT row_number() OVER () - 1 AS i, r AS role FROM unnest(ARRAY[
 'Producer','Written-By','Mastered By','Mixed By','Engineer','Composed By','Lyrics By','Vocals','Guitar','Bass','Drums','Keyboards',
 'Design','Photography By','Artwork','Liner Notes','Lacquer Cut By','Recorded By','Arranged By','Music By','Featuring','Remix','Co-producer',
 'Executive Producer','Backing Vocals','Saxophone','Trumpet','Piano','Violin','Conductor','Orchestra','A&R','Management','Coordinator',
 'Cover','Layout','Words By','Performer','Programmed By','Edited By','Written-By, Producer','Producer, Mixed By','Bass Guitar','Lead Vocals','Sleeve Notes']) AS r;
CREATE UNLOGGED TABLE bench.credit_src AS
SELECT 'Credit Person ' || floor(661775 * power(random(), 3))::int AS person_name,
       (1000 + g / 3 * 1)::text AS release_id,   -- placeholder, replaced below
       g
FROM generate_series(1, 5000000) g;
UPDATE bench.credit_src SET release_id = (10 + floor(g / 2.6))::int::text;
ALTER TABLE bench.credit_src ADD COLUMN role text;
UPDATE bench.credit_src c SET role = r.role FROM bench.roles r WHERE r.i = floor(45 * power(((c.g * 2654435761::bigint) % 4294967296)::float8 / 4294967296, 2.0))::int;
VACUUM (ANALYZE) bench.credit_src;
SELECT count(*), count(DISTINCT person_name), count(DISTINCT release_id), max(n) FROM bench.credit_src, LATERAL (SELECT 0) x, (SELECT max(c) n FROM (SELECT count(*) c FROM bench.credit_src GROUP BY person_name) z) m GROUP BY m.n;
SELECT pg_size_pretty(pg_total_relation_size('bench.credit_src'));
