-- Throwaway DDL for the gm-database-schema-9c8.1 spike. Not product schema.
--
-- Backend (b): the SAME property graph, declared over materialized edge
-- relations instead of over the JSONB views. Everything lives in its own
-- `graph_mat` schema so the shipped `graph` schema is untouched and the two
-- declarations can be measured side by side in one database with one cache.
--
-- These are plain tables populated by INSERT ... SELECT from the shipped views,
-- not MATERIALIZED VIEWs. That is deliberate: the question the spike has to
-- answer is whether the LOADER should write edge rows, and a loader writes
-- tables. A materialized view would also answer "are the rows already computed",
-- but it would tie the refresh story to REFRESH MATERIALIZED VIEW, which takes
-- an exclusive lock and recomputes the whole relation — the wrong shape for a
-- catalog that is updated release by release. Where a matview WOULD suffice the
-- read timings are identical, since both are heap plus index; the recommendation
-- in the spike document turns on write behaviour, not on these numbers.
--
-- Key columns are `text` throughout. PostgreSQL 19 beta 3 cannot resolve an
-- equality operator for a `character varying` property-graph vertex key, which
-- is why the shipped Discogs vertex views carry appended `*_key` text columns;
-- declaring these tables as `text` from the start avoids needing the same
-- workaround twice.

DROP PROPERTY GRAPH IF EXISTS graph_mat.catalog;
DROP SCHEMA IF EXISTS graph_mat CASCADE;
CREATE SCHEMA graph_mat;

-- ---------------------------------------------------------------------------
-- Vertices
-- ---------------------------------------------------------------------------

CREATE TABLE graph_mat.artist (
    artist_id  text PRIMARY KEY,
    name       text,
    gm_item_id uuid,
    hash       text,
    updated_at timestamptz
);

CREATE TABLE graph_mat.label (
    label_id   text PRIMARY KEY,
    name       text,
    gm_item_id uuid,
    hash       text,
    updated_at timestamptz
);

CREATE TABLE graph_mat.master (
    master_id  text PRIMARY KEY,
    title      text,
    year       text,
    genres     text[],
    styles     text[],
    gm_item_id uuid,
    hash       text,
    updated_at timestamptz
);

CREATE TABLE graph_mat.release (
    release_id     text PRIMARY KEY,
    title          text,
    year           text,
    country        text,
    genres         text[],
    styles         text[],
    media_families text[],
    gm_item_id     uuid,
    hash           text,
    updated_at     timestamptz
);

-- `graph.genre` and `graph.style` are SELECT DISTINCT over a full unnest of
-- every release AND every master. At a million releases that is the single most
-- expensive vertex view in the schema, and it yields about fifteen rows.
CREATE TABLE graph_mat.genre (name text PRIMARY KEY);
CREATE TABLE graph_mat.style (name text PRIMARY KEY);

CREATE TABLE graph_mat.mb_artist (
    mbid              uuid PRIMARY KEY,
    name              text,
    sort_name         text,
    type              text,
    gender            text,
    begin_date        text,
    end_date          text,
    ended             boolean,
    area              text,
    begin_area        text,
    end_area          text,
    disambiguation    text,
    discogs_artist_id bigint,
    updated_at        timestamptz
);

-- ---------------------------------------------------------------------------
-- Edges
-- ---------------------------------------------------------------------------
--
-- Every edge relation gets BOTH directions indexed. The primary key serves the
-- forward traversal and a second index serves the reverse, because the ported
-- Cypher walks these edges backwards as often as forwards: the collaborator
-- pattern enters `by_artist` from the artist end, and the label-DNA pattern
-- enters `on_label` from the label end. An edge table indexed in one direction
-- only is a sequential scan in the other.

CREATE TABLE graph_mat.by_artist (
    release_id text NOT NULL,
    artist_id  text NOT NULL,
    PRIMARY KEY (release_id, artist_id)
);
CREATE INDEX by_artist_reverse ON graph_mat.by_artist (artist_id, release_id);

CREATE TABLE graph_mat.on_label (
    release_id text NOT NULL,
    label_id   text NOT NULL,
    PRIMARY KEY (release_id, label_id)
);
CREATE INDEX on_label_reverse ON graph_mat.on_label (label_id, release_id);

CREATE TABLE graph_mat.in_genre (
    release_id text NOT NULL,
    genre_name text NOT NULL,
    PRIMARY KEY (release_id, genre_name)
);
CREATE INDEX in_genre_reverse ON graph_mat.in_genre (genre_name, release_id);

CREATE TABLE graph_mat.in_style (
    release_id text NOT NULL,
    style_name text NOT NULL,
    PRIMARY KEY (release_id, style_name)
);
CREATE INDEX in_style_reverse ON graph_mat.in_style (style_name, release_id);

CREATE TABLE graph_mat.derived_from (
    release_id text PRIMARY KEY,
    master_id  text NOT NULL
);
CREATE INDEX derived_from_reverse ON graph_mat.derived_from (master_id, release_id);

CREATE TABLE graph_mat.master_by_artist (
    master_id text NOT NULL,
    artist_id text NOT NULL,
    PRIMARY KEY (master_id, artist_id)
);
CREATE INDEX master_by_artist_reverse ON graph_mat.master_by_artist (artist_id, master_id);

CREATE TABLE graph_mat.master_in_genre (
    master_id  text NOT NULL,
    genre_name text NOT NULL,
    PRIMARY KEY (master_id, genre_name)
);
CREATE INDEX master_in_genre_reverse ON graph_mat.master_in_genre (genre_name, master_id);

CREATE TABLE graph_mat.master_in_style (
    master_id  text NOT NULL,
    style_name text NOT NULL,
    PRIMARY KEY (master_id, style_name)
);
CREATE INDEX master_in_style_reverse ON graph_mat.master_in_style (style_name, master_id);

-- The MusicBrainz edge already reads a real table rather than a JSONB unnest, so
-- materializing it copies rows rather than precomputing them. It is included so
-- backend (b) is the whole graph and not a subset, and so the third read family
-- is measured against the same declaration as the other two. The endpoint
-- indexes are the part that matters.
CREATE TABLE graph_mat.mb_rel_artist_artist (
    relationship_id   bigint PRIMARY KEY,
    source_mbid       uuid NOT NULL,
    target_mbid       uuid NOT NULL,
    relationship_type text,
    begin_date        text,
    end_date          text,
    ended             boolean,
    attributes        jsonb
);
CREATE INDEX mb_rel_source ON graph_mat.mb_rel_artist_artist (source_mbid);
CREATE INDEX mb_rel_target ON graph_mat.mb_rel_artist_artist (target_mbid);

-- ---------------------------------------------------------------------------
-- Population, straight from the shipped views
-- ---------------------------------------------------------------------------
--
-- Reading the views here is the point: it proves the materialized relations hold
-- exactly the rows the declared graph holds, so a difference in the measured
-- timings is a difference in access path and not in cardinality.

INSERT INTO graph_mat.artist SELECT artist_id, name, gm_item_id, hash, updated_at FROM graph.artist;
INSERT INTO graph_mat.label SELECT label_id, name, gm_item_id, hash, updated_at FROM graph.label;
INSERT INTO graph_mat.master SELECT master_id, title, year, genres, styles, gm_item_id, hash, updated_at FROM graph.master;
INSERT INTO graph_mat.release
    SELECT release_id, title, year, country, genres, styles, media_families, gm_item_id, hash, updated_at FROM graph.release;
INSERT INTO graph_mat.genre SELECT name FROM graph.genre;
INSERT INTO graph_mat.style SELECT name FROM graph.style;
INSERT INTO graph_mat.mb_artist
    SELECT mbid, name, sort_name, type, gender, begin_date, end_date, ended, area, begin_area, end_area,
           disambiguation, discogs_artist_id, updated_at
    FROM graph.mb_artist;

INSERT INTO graph_mat.by_artist SELECT release_id, artist_id FROM graph.by_artist;
INSERT INTO graph_mat.on_label SELECT release_id, label_id FROM graph.on_label;
INSERT INTO graph_mat.in_genre SELECT release_id, genre_name FROM graph.in_genre;
INSERT INTO graph_mat.in_style SELECT release_id, style_name FROM graph.in_style;
INSERT INTO graph_mat.derived_from SELECT release_id, master_id FROM graph.derived_from;
INSERT INTO graph_mat.master_by_artist SELECT master_id, artist_id FROM graph.master_by_artist;
INSERT INTO graph_mat.master_in_genre SELECT master_id, genre_name FROM graph.master_in_genre;
INSERT INTO graph_mat.master_in_style SELECT master_id, style_name FROM graph.master_in_style;
INSERT INTO graph_mat.mb_rel_artist_artist
    SELECT relationship_id, source_mbid, target_mbid, relationship_type, begin_date, end_date, ended, attributes
    FROM graph.mb_rel_artist_artist;

-- The Cypher for the third read family reaches the relationship from a Discogs
-- artist id, so that is the column the lookup lands on.
CREATE INDEX mb_artist_discogs ON graph_mat.mb_artist (discogs_artist_id);

ANALYZE graph_mat.artist;
ANALYZE graph_mat.label;
ANALYZE graph_mat.master;
ANALYZE graph_mat.release;
ANALYZE graph_mat.genre;
ANALYZE graph_mat.style;
ANALYZE graph_mat.mb_artist;
ANALYZE graph_mat.by_artist;
ANALYZE graph_mat.on_label;
ANALYZE graph_mat.in_genre;
ANALYZE graph_mat.in_style;
ANALYZE graph_mat.derived_from;
ANALYZE graph_mat.master_by_artist;
ANALYZE graph_mat.master_in_genre;
ANALYZE graph_mat.master_in_style;
ANALYZE graph_mat.mb_rel_artist_artist;

-- ---------------------------------------------------------------------------
-- The same graph, declared over the materialized relations
-- ---------------------------------------------------------------------------
--
-- Label names match the shipped `graph.catalog` exactly, so one GRAPH_TABLE
-- query text runs against either declaration with only the graph name changed.

CREATE PROPERTY GRAPH graph_mat.catalog
    VERTEX TABLES (
        graph_mat.artist AS artist KEY (artist_id)
            LABEL artist PROPERTIES ALL COLUMNS,
        graph_mat.label AS label KEY (label_id)
            LABEL label PROPERTIES ALL COLUMNS,
        graph_mat.master AS master KEY (master_id)
            LABEL master PROPERTIES ALL COLUMNS,
        graph_mat.release AS release KEY (release_id)
            LABEL release PROPERTIES ALL COLUMNS,
        graph_mat.genre AS genre KEY (name)
            LABEL genre PROPERTIES ALL COLUMNS,
        graph_mat.style AS style KEY (name)
            LABEL style PROPERTIES ALL COLUMNS,
        graph_mat.mb_artist AS mb_artist KEY (mbid)
            LABEL mb_artist PROPERTIES ALL COLUMNS
    )
    EDGE TABLES (
        graph_mat.by_artist AS by_artist KEY (release_id, artist_id)
            SOURCE KEY (release_id) REFERENCES release (release_id)
            DESTINATION KEY (artist_id) REFERENCES artist (artist_id)
            LABEL by_artist PROPERTIES ALL COLUMNS,
        graph_mat.on_label AS on_label KEY (release_id, label_id)
            SOURCE KEY (release_id) REFERENCES release (release_id)
            DESTINATION KEY (label_id) REFERENCES label (label_id)
            LABEL on_label PROPERTIES ALL COLUMNS,
        graph_mat.in_genre AS in_genre KEY (release_id, genre_name)
            SOURCE KEY (release_id) REFERENCES release (release_id)
            DESTINATION KEY (genre_name) REFERENCES genre (name)
            LABEL in_genre PROPERTIES ALL COLUMNS,
        graph_mat.in_style AS in_style KEY (release_id, style_name)
            SOURCE KEY (release_id) REFERENCES release (release_id)
            DESTINATION KEY (style_name) REFERENCES style (name)
            LABEL in_style PROPERTIES ALL COLUMNS,
        graph_mat.derived_from AS derived_from KEY (release_id)
            SOURCE KEY (release_id) REFERENCES release (release_id)
            DESTINATION KEY (master_id) REFERENCES master (master_id)
            LABEL derived_from PROPERTIES ALL COLUMNS,
        graph_mat.master_by_artist AS master_by_artist KEY (master_id, artist_id)
            SOURCE KEY (master_id) REFERENCES master (master_id)
            DESTINATION KEY (artist_id) REFERENCES artist (artist_id)
            LABEL master_by_artist PROPERTIES ALL COLUMNS,
        graph_mat.master_in_genre AS master_in_genre KEY (master_id, genre_name)
            SOURCE KEY (master_id) REFERENCES master (master_id)
            DESTINATION KEY (genre_name) REFERENCES genre (name)
            LABEL master_in_genre PROPERTIES ALL COLUMNS,
        graph_mat.master_in_style AS master_in_style KEY (master_id, style_name)
            SOURCE KEY (master_id) REFERENCES master (master_id)
            DESTINATION KEY (style_name) REFERENCES style (name)
            LABEL master_in_style PROPERTIES ALL COLUMNS,
        graph_mat.mb_rel_artist_artist AS mb_rel_artist_artist KEY (relationship_id)
            SOURCE KEY (source_mbid) REFERENCES mb_artist (mbid)
            DESTINATION KEY (target_mbid) REFERENCES mb_artist (mbid)
            LABEL mb_rel_artist_artist PROPERTIES ALL COLUMNS
    );
