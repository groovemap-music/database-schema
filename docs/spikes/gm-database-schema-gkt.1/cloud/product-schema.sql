-- GENERATED. Do not edit, and do not treat as a second source of truth.
--
-- gm-database-schema-gkt.1 — the product schema as a replayable dump, for the
-- cloud mode only. Throwaway spike artefact.
--
-- The local mode applies the product schema the way the integration suite does,
-- by calling `groovemap_schema.initializer` against a running container. A bare
-- IBM Cloud instance has no Python environment, and building one there means
-- resolving this project's whole dependency tree — including a git dependency —
-- on a machine whose only job is to run a benchmark for twenty minutes.
--
-- So the cloud bootstrap replays this instead. It is `pg_dump --schema-only`
-- of a database the packaged initializer built, with the three spike schemas
-- excluded, which is the same DDL by construction:
--
--   SCHEMA_PROPERTY_GRAPH=enabled <apply groovemap_schema.initializer>
--   pg_dump -U groovemap -d groovemap --schema-only --no-owner --no-privileges \
--     --exclude-schema=graph_mat --exclude-schema=path --exclude-schema=pf
--
-- `../run.sh` regenerates it whenever a local container with the product schema
-- is running, so a cloud run started after a local one cannot replay a stale
-- copy. If this file ever disagrees with the initializer, the initializer is
-- right and this should be regenerated, not patched.

--
-- PostgreSQL database dump
--

\restrict UI55ijiWSnDroKMcs7JYrBLkFbaTDw2rbCW1LiGbSvoybdL1y6rmklMlhZTSSHs

-- Dumped from database version 19beta3
-- Dumped by pg_dump version 19beta3

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: activity; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA activity;


--
-- Name: graph; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA graph;


--
-- Name: insights; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA insights;


--
-- Name: musicbrainz; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA musicbrainz;


--
-- Name: hstore; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS hstore WITH SCHEMA public;


--
-- Name: EXTENSION hstore; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION hstore IS 'data type for storing sets of (key, value) pairs';


--
-- Name: ensure_month_partition(text, date); Type: FUNCTION; Schema: activity; Owner: -
--

CREATE FUNCTION activity.ensure_month_partition(table_name text, month date) RETURNS text
    LANGUAGE plpgsql
    AS $$
        DECLARE
            month_start    DATE := date_trunc('month', month)::DATE;
            month_end      DATE := (date_trunc('month', month) + INTERVAL '1 month')::DATE;
            partition_name TEXT := format('%s_y%sm%s', table_name, to_char(month_start, 'YYYY'), to_char(month_start, 'MM'));
        BEGIN
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS activity.%I PARTITION OF activity.%I FOR VALUES FROM (%L) TO (%L)',
                partition_name,
                table_name,
                month_start,
                month_end
            );
            RETURN partition_name;
        END
        $$;


--
-- Name: reject_mutation(); Type: FUNCTION; Schema: activity; Owner: -
--

CREATE FUNCTION activity.reject_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            IF current_setting('groovemap.erasure', true) = 'on' THEN
                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION
                '%.% is append-only: % is rejected unless groovemap.erasure is on',
                TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'restrict_violation';
        END
        $$;


--
-- Name: credit_role_category(text); Type: FUNCTION; Schema: graph; Owner: -
--

CREATE FUNCTION graph.credit_role_category(raw_role text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    AS $$
        SELECT CASE
        WHEN normalized.role = 'assistant engineer' THEN 'engineering'
        WHEN normalized.role = 'executive producer' THEN 'production'
        WHEN normalized.role = 'executive-producer' THEN 'production'
        WHEN normalized.role = 'mastering engineer' THEN 'mastering'
        WHEN normalized.role = 'recording engineer' THEN 'engineering'
        WHEN normalized.role = 'session musician' THEN 'session'
        WHEN normalized.role = 'mixing engineer' THEN 'engineering'
        WHEN normalized.role = 'audio engineer' THEN 'engineering'
        WHEN normalized.role = 'backing vocals' THEN 'session'
        WHEN normalized.role = 'co-produced by' THEN 'production'
        WHEN normalized.role = 'lacquer cut by' THEN 'mastering'
        WHEN normalized.role = 'photography by' THEN 'design'
        WHEN normalized.role = 'sound engineer' THEN 'engineering'
        WHEN normalized.role = 'art direction' THEN 'design'
        WHEN normalized.role = 'remastered by' THEN 'mastering'
        WHEN normalized.role = 'illustration' THEN 'design'
        WHEN normalized.role = 'co-producer' THEN 'production'
        WHEN normalized.role = 'designed by' THEN 'design'
        WHEN normalized.role = 'lead vocals' THEN 'session'
        WHEN normalized.role = 'liner notes' THEN 'design'
        WHEN normalized.role = 'mastered at' THEN 'mastering'
        WHEN normalized.role = 'mastered by' THEN 'mastering'
        WHEN normalized.role = 'photography' THEN 'design'
        WHEN normalized.role = 'produced by' THEN 'production'
        WHEN normalized.role = 'recorded by' THEN 'engineering'
        WHEN normalized.role = 'synthesizer' THEN 'session'
        WHEN normalized.role = 'artwork by' THEN 'design'
        WHEN normalized.role = 'managed by' THEN 'management'
        WHEN normalized.role = 'management' THEN 'management'
        WHEN normalized.role = 'percussion' THEN 'session'
        WHEN normalized.role = 'remixed by' THEN 'engineering'
        WHEN normalized.role = 'accordion' THEN 'session'
        WHEN normalized.role = 'booked by' THEN 'management'
        WHEN normalized.role = 'featuring' THEN 'session'
        WHEN normalized.role = 'harmonica' THEN 'session'
        WHEN normalized.role = 'keyboards' THEN 'session'
        WHEN normalized.role = 'saxophone' THEN 'session'
        WHEN normalized.role = 'clarinet' THEN 'session'
        WHEN normalized.role = 'engineer' THEN 'engineering'
        WHEN normalized.role = 'mandolin' THEN 'session'
        WHEN normalized.role = 'mixed at' THEN 'engineering'
        WHEN normalized.role = 'mixed by' THEN 'engineering'
        WHEN normalized.role = 'producer' THEN 'production'
        WHEN normalized.role = 'trombone' THEN 'session'
        WHEN normalized.role = 'artwork' THEN 'design'
        WHEN normalized.role = 'booking' THEN 'management'
        WHEN normalized.role = 'strings' THEN 'session'
        WHEN normalized.role = 'trumpet' THEN 'session'
        WHEN normalized.role = 'bongos' THEN 'session'
        WHEN normalized.role = 'congas' THEN 'session'
        WHEN normalized.role = 'cut by' THEN 'mastering'
        WHEN normalized.role = 'design' THEN 'design'
        WHEN normalized.role = 'guitar' THEN 'session'
        WHEN normalized.role = 'layout' THEN 'design'
        WHEN normalized.role = 'violin' THEN 'session'
        WHEN normalized.role = 'vocals' THEN 'session'
        WHEN normalized.role = 'a & r' THEN 'management'
        WHEN normalized.role = 'banjo' THEN 'session'
        WHEN normalized.role = 'cello' THEN 'session'
        WHEN normalized.role = 'cover' THEN 'design'
        WHEN normalized.role = 'drums' THEN 'session'
        WHEN normalized.role = 'flute' THEN 'session'
        WHEN normalized.role = 'horns' THEN 'session'
        WHEN normalized.role = 'organ' THEN 'session'
        WHEN normalized.role = 'piano' THEN 'session'
        WHEN normalized.role = 'remix' THEN 'engineering'
        WHEN normalized.role = 'sitar' THEN 'session'
        WHEN normalized.role = 'tabla' THEN 'session'
        WHEN normalized.role = 'viola' THEN 'session'
        WHEN normalized.role = 'bass' THEN 'session'
        WHEN normalized.role = 'harp' THEN 'session'
        WHEN normalized.role = 'oboe' THEN 'session'
        WHEN normalized.role = 'tuba' THEN 'session'
        WHEN normalized.role = 'a&r' THEN 'management'
        WHEN strpos(normalized.role, 'assistant engineer') > 0 THEN 'engineering'
        WHEN strpos(normalized.role, 'executive producer') > 0 THEN 'production'
        WHEN strpos(normalized.role, 'executive-producer') > 0 THEN 'production'
        WHEN strpos(normalized.role, 'mastering engineer') > 0 THEN 'mastering'
        WHEN strpos(normalized.role, 'recording engineer') > 0 THEN 'engineering'
        WHEN strpos(normalized.role, 'session musician') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'mixing engineer') > 0 THEN 'engineering'
        WHEN strpos(normalized.role, 'audio engineer') > 0 THEN 'engineering'
        WHEN strpos(normalized.role, 'backing vocals') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'co-produced by') > 0 THEN 'production'
        WHEN strpos(normalized.role, 'lacquer cut by') > 0 THEN 'mastering'
        WHEN strpos(normalized.role, 'photography by') > 0 THEN 'design'
        WHEN strpos(normalized.role, 'sound engineer') > 0 THEN 'engineering'
        WHEN strpos(normalized.role, 'art direction') > 0 THEN 'design'
        WHEN strpos(normalized.role, 'remastered by') > 0 THEN 'mastering'
        WHEN strpos(normalized.role, 'illustration') > 0 THEN 'design'
        WHEN strpos(normalized.role, 'co-producer') > 0 THEN 'production'
        WHEN strpos(normalized.role, 'designed by') > 0 THEN 'design'
        WHEN strpos(normalized.role, 'lead vocals') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'liner notes') > 0 THEN 'design'
        WHEN strpos(normalized.role, 'mastered at') > 0 THEN 'mastering'
        WHEN strpos(normalized.role, 'mastered by') > 0 THEN 'mastering'
        WHEN strpos(normalized.role, 'photography') > 0 THEN 'design'
        WHEN strpos(normalized.role, 'produced by') > 0 THEN 'production'
        WHEN strpos(normalized.role, 'recorded by') > 0 THEN 'engineering'
        WHEN strpos(normalized.role, 'synthesizer') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'artwork by') > 0 THEN 'design'
        WHEN strpos(normalized.role, 'managed by') > 0 THEN 'management'
        WHEN strpos(normalized.role, 'management') > 0 THEN 'management'
        WHEN strpos(normalized.role, 'percussion') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'remixed by') > 0 THEN 'engineering'
        WHEN strpos(normalized.role, 'accordion') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'booked by') > 0 THEN 'management'
        WHEN strpos(normalized.role, 'featuring') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'harmonica') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'keyboards') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'saxophone') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'clarinet') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'engineer') > 0 THEN 'engineering'
        WHEN strpos(normalized.role, 'mandolin') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'mixed at') > 0 THEN 'engineering'
        WHEN strpos(normalized.role, 'mixed by') > 0 THEN 'engineering'
        WHEN strpos(normalized.role, 'producer') > 0 THEN 'production'
        WHEN strpos(normalized.role, 'trombone') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'artwork') > 0 THEN 'design'
        WHEN strpos(normalized.role, 'booking') > 0 THEN 'management'
        WHEN strpos(normalized.role, 'strings') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'trumpet') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'bongos') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'congas') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'cut by') > 0 THEN 'mastering'
        WHEN strpos(normalized.role, 'design') > 0 THEN 'design'
        WHEN strpos(normalized.role, 'guitar') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'layout') > 0 THEN 'design'
        WHEN strpos(normalized.role, 'violin') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'vocals') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'a & r') > 0 THEN 'management'
        WHEN strpos(normalized.role, 'banjo') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'cello') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'cover') > 0 THEN 'design'
        WHEN strpos(normalized.role, 'drums') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'flute') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'horns') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'organ') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'piano') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'remix') > 0 THEN 'engineering'
        WHEN strpos(normalized.role, 'sitar') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'tabla') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'viola') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'bass') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'harp') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'oboe') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'tuba') > 0 THEN 'session'
        WHEN strpos(normalized.role, 'a&r') > 0 THEN 'management'
            ELSE 'other'
        END
        FROM (SELECT btrim(lower(raw_role)) AS role) AS normalized
        $$;


--
-- Name: medium_label(text); Type: FUNCTION; Schema: graph; Owner: -
--

CREATE FUNCTION graph.medium_label(medium_id text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    AS $$
        SELECT CASE medium_id
            WHEN 'digital_download_card' THEN 'Download card'
            WHEN 'digital_file' THEN 'Digital file'
            WHEN 'digital_floppy_disk' THEN 'Floppy disk'
            WHEN 'digital_memory_card' THEN 'Memory card'
            WHEN 'digital_unspecified' THEN 'Digital'
            WHEN 'digital_usb' THEN 'USB flash drive'
            WHEN 'grooved_acetate' THEN 'Acetate'
            WHEN 'grooved_cylinder' THEN 'Cylinder'
            WHEN 'grooved_edison_disc' THEN 'Edison Diamond Disc'
            WHEN 'grooved_flexi_disc' THEN 'Flexi disc'
            WHEN 'grooved_lathe_cut' THEN 'Lathe cut'
            WHEN 'grooved_other_unspecified' THEN 'Other grooved medium'
            WHEN 'grooved_pathe_disc' THEN 'Pathé disc'
            WHEN 'grooved_piano_roll' THEN 'Piano roll'
            WHEN 'optical_cd' THEN 'CD'
            WHEN 'optical_cdr' THEN 'CD-R'
            WHEN 'optical_dualdisc' THEN 'DualDisc'
            WHEN 'optical_dvd_audio' THEN 'DVD-Audio'
            WHEN 'optical_minidisc' THEN 'MiniDisc'
            WHEN 'optical_sacd' THEN 'SACD'
            WHEN 'optical_umd' THEN 'UMD'
            WHEN 'optical_unspecified' THEN 'Optical disc'
            WHEN 'other_unspecified' THEN 'Other'
            WHEN 'shellac_10' THEN '10" shellac'
            WHEN 'shellac_12' THEN '12" shellac'
            WHEN 'shellac_7' THEN '7" shellac'
            WHEN 'shellac_unspecified' THEN 'Shellac'
            WHEN 'tape_4_track' THEN '4-track cartridge'
            WHEN 'tape_8_track' THEN '8-track cartridge'
            WHEN 'tape_cassette' THEN 'Cassette'
            WHEN 'tape_dat' THEN 'DAT'
            WHEN 'tape_dcc' THEN 'DCC'
            WHEN 'tape_elcaset' THEN 'Elcaset'
            WHEN 'tape_microcassette' THEN 'Microcassette'
            WHEN 'tape_playtape' THEN 'PlayTape'
            WHEN 'tape_reel_to_reel' THEN 'Reel-to-reel'
            WHEN 'tape_unspecified' THEN 'Tape'
            WHEN 'video_betamax' THEN 'Betamax'
            WHEN 'video_blu_ray' THEN 'Blu-ray'
            WHEN 'video_cdv' THEN 'CD Video'
            WHEN 'video_ced' THEN 'Capacitance Electronic Disc'
            WHEN 'video_dvd' THEN 'DVD-Video'
            WHEN 'video_dvdr' THEN 'DVD-R'
            WHEN 'video_film_reel' THEN 'Film reel'
            WHEN 'video_hd_dvd' THEN 'HD DVD'
            WHEN 'video_laserdisc' THEN 'Laserdisc'
            WHEN 'video_svcd' THEN 'Super Video CD'
            WHEN 'video_unspecified' THEN 'Video'
            WHEN 'video_vcd' THEN 'Video CD'
            WHEN 'video_vhd' THEN 'VHD'
            WHEN 'video_vhs' THEN 'VHS'
            WHEN 'vinyl_10' THEN '10" vinyl'
            WHEN 'vinyl_12' THEN '12" vinyl'
            WHEN 'vinyl_7' THEN '7" vinyl'
            WHEN 'vinyl_unspecified' THEN 'Vinyl'
            ELSE medium_id
        END
        $$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: consent_grants; Type: TABLE; Schema: activity; Owner: -
--

CREATE TABLE activity.consent_grants (
    id uuid DEFAULT uuidv7() NOT NULL,
    user_id uuid NOT NULL,
    purpose text NOT NULL,
    granted_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_at timestamp with time zone,
    CONSTRAINT consent_grants_purpose_check CHECK ((purpose = ANY (ARRAY['product_analytics'::text, 'model_training'::text])))
);


--
-- Name: erasures; Type: TABLE; Schema: activity; Owner: -
--

CREATE TABLE activity.erasures (
    id uuid DEFAULT uuidv7() NOT NULL,
    subject_id uuid NOT NULL,
    requested_at timestamp with time zone NOT NULL,
    completed_at timestamp with time zone,
    events_deleted bigint,
    impressions_deleted bigint,
    model_versions_before text[],
    notes jsonb
);


--
-- Name: events; Type: TABLE; Schema: activity; Owner: -
--

CREATE TABLE activity.events (
    event_id uuid DEFAULT uuidv7() NOT NULL,
    event_type text NOT NULL,
    schema_version smallint NOT NULL,
    subject_id uuid NOT NULL,
    session_id uuid,
    occurred_at timestamp with time zone NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    producer text NOT NULL,
    consent_purposes text[] NOT NULL,
    model_version text,
    feature_version text,
    idempotency_key text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL
)
PARTITION BY RANGE (occurred_at);


--
-- Name: events_default; Type: TABLE; Schema: activity; Owner: -
--

CREATE TABLE activity.events_default (
    event_id uuid DEFAULT uuidv7() CONSTRAINT events_event_id_not_null NOT NULL,
    event_type text CONSTRAINT events_event_type_not_null NOT NULL,
    schema_version smallint CONSTRAINT events_schema_version_not_null NOT NULL,
    subject_id uuid CONSTRAINT events_subject_id_not_null NOT NULL,
    session_id uuid,
    occurred_at timestamp with time zone CONSTRAINT events_occurred_at_not_null NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() CONSTRAINT events_recorded_at_not_null NOT NULL,
    producer text CONSTRAINT events_producer_not_null NOT NULL,
    consent_purposes text[] CONSTRAINT events_consent_purposes_not_null NOT NULL,
    model_version text,
    feature_version text,
    idempotency_key text CONSTRAINT events_idempotency_key_not_null NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb CONSTRAINT events_payload_not_null NOT NULL
);


--
-- Name: impressions; Type: TABLE; Schema: activity; Owner: -
--

CREATE TABLE activity.impressions (
    impression_id uuid DEFAULT uuidv7() NOT NULL,
    subject_id uuid NOT NULL,
    surface text NOT NULL,
    policy_id text NOT NULL,
    candidate_set_id uuid NOT NULL,
    "position" integer NOT NULL,
    item_id uuid NOT NULL,
    score real,
    propensity real,
    request_id uuid,
    occurred_at timestamp with time zone NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    consent_purposes text[] NOT NULL
)
PARTITION BY RANGE (occurred_at);


--
-- Name: impressions_default; Type: TABLE; Schema: activity; Owner: -
--

CREATE TABLE activity.impressions_default (
    impression_id uuid DEFAULT uuidv7() CONSTRAINT impressions_impression_id_not_null NOT NULL,
    subject_id uuid CONSTRAINT impressions_subject_id_not_null NOT NULL,
    surface text CONSTRAINT impressions_surface_not_null NOT NULL,
    policy_id text CONSTRAINT impressions_policy_id_not_null NOT NULL,
    candidate_set_id uuid CONSTRAINT impressions_candidate_set_id_not_null NOT NULL,
    "position" integer CONSTRAINT impressions_position_not_null NOT NULL,
    item_id uuid CONSTRAINT impressions_item_id_not_null NOT NULL,
    score real,
    propensity real,
    request_id uuid,
    occurred_at timestamp with time zone CONSTRAINT impressions_occurred_at_not_null NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() CONSTRAINT impressions_recorded_at_not_null NOT NULL,
    consent_purposes text[] CONSTRAINT impressions_consent_purposes_not_null NOT NULL
);


--
-- Name: user_subjects; Type: TABLE; Schema: activity; Owner: -
--

CREATE TABLE activity.user_subjects (
    user_id uuid NOT NULL,
    subject_id uuid DEFAULT uuidv7() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: artists; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.artists (
    data_id character varying NOT NULL,
    hash character varying NOT NULL,
    data jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    gm_item_id uuid
);


--
-- Name: alias_of; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.alias_of AS
 SELECT DISTINCT btrim((element.value ->> 'id'::text)) AS alias_artist_id,
    artists.data_id AS artist_id
   FROM (public.artists artists
     CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN (jsonb_typeof((artists.data -> 'aliases'::text)) = 'array'::text) THEN (artists.data -> 'aliases'::text)
            ELSE '[]'::jsonb
        END) element(value))
  WHERE ((NULLIF(btrim((element.value ->> 'id'::text)), ''::text) IS NOT NULL) AND (btrim((element.value ->> 'id'::text)) <> '0'::text));


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    email character varying(255) NOT NULL,
    hashed_password character varying(255) NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    is_admin boolean DEFAULT false NOT NULL,
    password_changed_at timestamp with time zone,
    totp_secret character varying,
    totp_enabled boolean DEFAULT false NOT NULL,
    totp_recovery_codes jsonb,
    totp_failed_attempts integer DEFAULT 0 NOT NULL,
    totp_locked_until timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: app_user; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.app_user AS
 SELECT id AS user_id,
    is_active,
    is_admin,
    created_at,
    updated_at
   FROM public.users users;


--
-- Name: artist; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.artist AS
 SELECT data_id AS artist_id,
    (data ->> 'name'::text) AS name,
    gm_item_id,
    hash,
    updated_at,
    (data_id)::text AS artist_key
   FROM public.artists artists;


--
-- Name: releases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.releases (
    data_id character varying NOT NULL,
    hash character varying NOT NULL,
    data jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    gm_item_id uuid,
    media jsonb
);


--
-- Name: by_artist; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.by_artist AS
 SELECT DISTINCT entity.data_id AS release_id,
    btrim((element.value ->> 'id'::text)) AS artist_id
   FROM (public.releases entity
     CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN (jsonb_typeof((entity.data -> 'artists'::text)) = 'array'::text) THEN (entity.data -> 'artists'::text)
            ELSE '[]'::jsonb
        END) element(value))
  WHERE ((NULLIF(btrim((element.value ->> 'id'::text)), ''::text) IS NOT NULL) AND (btrim((element.value ->> 'id'::text)) <> '0'::text));


--
-- Name: catalog_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.catalog_items (
    id uuid DEFAULT uuidv7() NOT NULL,
    kind text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT catalog_items_kind_check CHECK ((kind = ANY (ARRAY['release'::text, 'master'::text, 'artist'::text, 'label'::text])))
);


--
-- Name: catalog_item; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.catalog_item AS
 SELECT id AS item_id,
    kind,
    created_at
   FROM public.catalog_items catalog_items;


--
-- Name: user_collections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_collections (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    release_id bigint NOT NULL,
    instance_id bigint,
    folder_id integer,
    title character varying(500),
    artist character varying(500),
    year integer,
    formats jsonb,
    media jsonb,
    label character varying(255),
    condition character varying(100),
    rating smallint,
    notes text,
    date_added timestamp with time zone,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    gm_item_id uuid,
    owned_copy_id uuid
);


--
-- Name: collected; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.collected AS
 SELECT collection.id AS collection_id,
    collection.user_id,
    releases.data_id AS release_id,
    collection.instance_id,
    collection.folder_id,
    collection.condition,
    collection.rating,
    collection.date_added
   FROM (public.user_collections collection
     JOIN public.releases releases ON (((releases.data_id)::text = (collection.release_id)::text)));


--
-- Name: company; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.company AS
 SELECT DISTINCT ON (company_id) company_id,
    COALESCE(company_name, company_id) AS name,
        CASE
            WHEN (company_id ~ '^[0-9]+$'::text) THEN company_id
            ELSE NULL::text
        END AS discogs_label_id
   FROM ( SELECT releases.data_id AS release_id,
            item.ordinality AS entry_position,
                CASE
                    WHEN (btrim((item.value ->> 'discogs_id'::text)) ~ '^0*[1-9][0-9]*$'::text) THEN btrim((item.value ->> 'discogs_id'::text))
                    WHEN (NULLIF(btrim((item.value ->> 'name'::text)), ''::text) IS NOT NULL) THEN ('name:'::text || lower(regexp_replace(btrim((item.value ->> 'name'::text)), '\s+'::text, ' '::text, 'g'::text)))
                    ELSE NULL::text
                END AS company_id,
            NULLIF(btrim((item.value ->> 'name'::text)), ''::text) AS company_name,
            NULLIF(btrim((item.value ->> 'role'::text)), ''::text) AS role,
            COALESCE(NULLIF(btrim((item.value ->> 'role_category'::text)), ''::text), 'other'::text) AS role_category
           FROM (public.releases releases
             CROSS JOIN LATERAL jsonb_array_elements(
                CASE
                    WHEN (jsonb_typeof(((releases.data -> 'companies'::text) -> 'items'::text)) = 'array'::text) THEN ((releases.data -> 'companies'::text) -> 'items'::text)
                    ELSE '[]'::jsonb
                END) WITH ORDINALITY item(value, ordinality))) credit
  WHERE ((company_id IS NOT NULL) AND (role IS NOT NULL))
  ORDER BY company_id, COALESCE(company_name, company_id);


--
-- Name: credited_on; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.credited_on AS
 SELECT DISTINCT person_name,
    release_id,
    role,
    graph.credit_role_category(role) AS role_category
   FROM ( SELECT releases.data_id AS release_id,
            (credit_1.value ->> 'name'::text) AS person_name,
            (credit_1.value ->> 'role'::text) AS role,
            btrim((credit_1.value ->> 'id'::text)) AS artist_id
           FROM (public.releases releases
             CROSS JOIN LATERAL jsonb_array_elements(
                CASE
                    WHEN (jsonb_typeof((releases.data -> 'extraartists'::text)) = 'array'::text) THEN (releases.data -> 'extraartists'::text)
                    ELSE '[]'::jsonb
                END) credit_1(value))
          WHERE ((NULLIF((credit_1.value ->> 'name'::text), ''::text) IS NOT NULL) AND (NULLIF((credit_1.value ->> 'role'::text), ''::text) IS NOT NULL))) credit;


--
-- Name: credited_to; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.credited_to AS
 SELECT DISTINCT ON (release_id, company_id, role) release_id,
    company_id,
    role,
    role_category,
    'discogs'::text AS source
   FROM ( SELECT releases.data_id AS release_id,
            item.ordinality AS entry_position,
                CASE
                    WHEN (btrim((item.value ->> 'discogs_id'::text)) ~ '^0*[1-9][0-9]*$'::text) THEN btrim((item.value ->> 'discogs_id'::text))
                    WHEN (NULLIF(btrim((item.value ->> 'name'::text)), ''::text) IS NOT NULL) THEN ('name:'::text || lower(regexp_replace(btrim((item.value ->> 'name'::text)), '\s+'::text, ' '::text, 'g'::text)))
                    ELSE NULL::text
                END AS company_id,
            NULLIF(btrim((item.value ->> 'name'::text)), ''::text) AS company_name,
            NULLIF(btrim((item.value ->> 'role'::text)), ''::text) AS role,
            COALESCE(NULLIF(btrim((item.value ->> 'role_category'::text)), ''::text), 'other'::text) AS role_category
           FROM (public.releases releases
             CROSS JOIN LATERAL jsonb_array_elements(
                CASE
                    WHEN (jsonb_typeof(((releases.data -> 'companies'::text) -> 'items'::text)) = 'array'::text) THEN ((releases.data -> 'companies'::text) -> 'items'::text)
                    ELSE '[]'::jsonb
                END) WITH ORDINALITY item(value, ordinality))) credit
  WHERE ((company_id IS NOT NULL) AND (role IS NOT NULL))
  ORDER BY release_id, company_id, role, entry_position;


--
-- Name: derived_from; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.derived_from AS
 SELECT data_id AS release_id,
    btrim((data ->> 'master_id'::text)) AS master_id
   FROM public.releases releases
  WHERE ((NULLIF(btrim((data ->> 'master_id'::text)), ''::text) IS NOT NULL) AND (btrim((data ->> 'master_id'::text)) <> '0'::text));


--
-- Name: masters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.masters (
    data_id character varying NOT NULL,
    hash character varying NOT NULL,
    data jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    gm_item_id uuid
);


--
-- Name: genre; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.genre AS
 SELECT DISTINCT genre.value AS name
   FROM (( SELECT releases.data AS document
           FROM public.releases releases
        UNION ALL
         SELECT masters.data AS document
           FROM public.masters masters) source
     CROSS JOIN LATERAL jsonb_array_elements_text(
        CASE
            WHEN (jsonb_typeof((source.document -> 'genres'::text)) = 'array'::text) THEN (source.document -> 'genres'::text)
            ELSE '[]'::jsonb
        END) genre(value))
  WHERE (NULLIF(genre.value, ''::text) IS NOT NULL);


--
-- Name: releases; Type: TABLE; Schema: musicbrainz; Owner: -
--

CREATE TABLE musicbrainz.releases (
    mbid uuid NOT NULL,
    name text NOT NULL,
    barcode text,
    status text,
    release_group_mbid uuid,
    discogs_release_id bigint,
    data jsonb,
    media jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    gm_item_id uuid
);


--
-- Name: in_family; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.in_family AS
 SELECT DISTINCT medium_id,
    family_name
   FROM ( SELECT releases.data_id AS release_id,
            'discogs'::text AS provider,
            (item.value ->> 'medium'::text) AS medium_id,
            (item.value ->> 'family'::text) AS family_name,
                CASE
                    WHEN ((jsonb_typeof((item.value -> 'qty'::text)) = 'number'::text) AND ((item.value ->> 'qty'::text) ~ '^[0-9]{1,9}$'::text) AND (((item.value ->> 'qty'::text))::bigint >= 1)) THEN ((item.value ->> 'qty'::text))::bigint
                    ELSE (1)::bigint
                END AS qty
           FROM (public.releases releases
             CROSS JOIN LATERAL jsonb_array_elements(
                CASE
                    WHEN (jsonb_typeof((releases.media -> 'items'::text)) = 'array'::text) THEN (releases.media -> 'items'::text)
                    ELSE '[]'::jsonb
                END) item(value))
          WHERE ((NULLIF((item.value ->> 'medium'::text), ''::text) IS NOT NULL) AND (NULLIF((item.value ->> 'family'::text), ''::text) IS NOT NULL))
        UNION ALL
         SELECT releases.data_id AS release_id,
            'musicbrainz'::text AS provider,
            (item.value ->> 'medium'::text) AS medium_id,
            (item.value ->> 'family'::text) AS family_name,
                CASE
                    WHEN ((jsonb_typeof((item.value -> 'qty'::text)) = 'number'::text) AND ((item.value ->> 'qty'::text) ~ '^[0-9]{1,9}$'::text) AND (((item.value ->> 'qty'::text))::bigint >= 1)) THEN ((item.value ->> 'qty'::text))::bigint
                    ELSE (1)::bigint
                END AS qty
           FROM ((musicbrainz.releases mb_release
             JOIN public.releases releases ON (((releases.data_id)::text = (mb_release.discogs_release_id)::text)))
             CROSS JOIN LATERAL jsonb_array_elements(
                CASE
                    WHEN (jsonb_typeof((mb_release.media -> 'items'::text)) = 'array'::text) THEN (mb_release.media -> 'items'::text)
                    ELSE '[]'::jsonb
                END) item(value))
          WHERE ((NULLIF((item.value ->> 'medium'::text), ''::text) IS NOT NULL) AND (NULLIF((item.value ->> 'family'::text), ''::text) IS NOT NULL))) media;


--
-- Name: in_genre; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.in_genre AS
 SELECT DISTINCT entity.data_id AS release_id,
    tag.value AS genre_name
   FROM (public.releases entity
     CROSS JOIN LATERAL jsonb_array_elements_text(
        CASE
            WHEN (jsonb_typeof((entity.data -> 'genres'::text)) = 'array'::text) THEN (entity.data -> 'genres'::text)
            ELSE '[]'::jsonb
        END) tag(value))
  WHERE (NULLIF(tag.value, ''::text) IS NOT NULL);


--
-- Name: in_style; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.in_style AS
 SELECT DISTINCT entity.data_id AS release_id,
    tag.value AS style_name
   FROM (public.releases entity
     CROSS JOIN LATERAL jsonb_array_elements_text(
        CASE
            WHEN (jsonb_typeof((entity.data -> 'styles'::text)) = 'array'::text) THEN (entity.data -> 'styles'::text)
            ELSE '[]'::jsonb
        END) tag(value))
  WHERE (NULLIF(tag.value, ''::text) IS NOT NULL);


--
-- Name: issued_on; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.issued_on AS
 SELECT release_id,
    medium_id,
    provider AS source,
    (sum(qty))::bigint AS qty
   FROM ( SELECT releases.data_id AS release_id,
            'discogs'::text AS provider,
            (item.value ->> 'medium'::text) AS medium_id,
            (item.value ->> 'family'::text) AS family_name,
                CASE
                    WHEN ((jsonb_typeof((item.value -> 'qty'::text)) = 'number'::text) AND ((item.value ->> 'qty'::text) ~ '^[0-9]{1,9}$'::text) AND (((item.value ->> 'qty'::text))::bigint >= 1)) THEN ((item.value ->> 'qty'::text))::bigint
                    ELSE (1)::bigint
                END AS qty
           FROM (public.releases releases
             CROSS JOIN LATERAL jsonb_array_elements(
                CASE
                    WHEN (jsonb_typeof((releases.media -> 'items'::text)) = 'array'::text) THEN (releases.media -> 'items'::text)
                    ELSE '[]'::jsonb
                END) item(value))
          WHERE ((NULLIF((item.value ->> 'medium'::text), ''::text) IS NOT NULL) AND (NULLIF((item.value ->> 'family'::text), ''::text) IS NOT NULL))
        UNION ALL
         SELECT releases.data_id AS release_id,
            'musicbrainz'::text AS provider,
            (item.value ->> 'medium'::text) AS medium_id,
            (item.value ->> 'family'::text) AS family_name,
                CASE
                    WHEN ((jsonb_typeof((item.value -> 'qty'::text)) = 'number'::text) AND ((item.value ->> 'qty'::text) ~ '^[0-9]{1,9}$'::text) AND (((item.value ->> 'qty'::text))::bigint >= 1)) THEN ((item.value ->> 'qty'::text))::bigint
                    ELSE (1)::bigint
                END AS qty
           FROM ((musicbrainz.releases mb_release
             JOIN public.releases releases ON (((releases.data_id)::text = (mb_release.discogs_release_id)::text)))
             CROSS JOIN LATERAL jsonb_array_elements(
                CASE
                    WHEN (jsonb_typeof((mb_release.media -> 'items'::text)) = 'array'::text) THEN (mb_release.media -> 'items'::text)
                    ELSE '[]'::jsonb
                END) item(value))
          WHERE ((NULLIF((item.value ->> 'medium'::text), ''::text) IS NOT NULL) AND (NULLIF((item.value ->> 'family'::text), ''::text) IS NOT NULL))) media
  GROUP BY release_id, medium_id, provider;


--
-- Name: labels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.labels (
    data_id character varying NOT NULL,
    hash character varying NOT NULL,
    data jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    gm_item_id uuid
);


--
-- Name: label; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.label AS
 SELECT data_id AS label_id,
    (data ->> 'name'::text) AS name,
    gm_item_id,
    hash,
    updated_at,
    (data_id)::text AS label_key
   FROM public.labels labels;


--
-- Name: master; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.master AS
 SELECT data_id AS master_id,
    (data ->> 'title'::text) AS title,
    (data ->> 'year'::text) AS year,
    ARRAY( SELECT jsonb_array_elements_text((masters.data -> 'genres'::text)) AS jsonb_array_elements_text
          WHERE (jsonb_typeof((masters.data -> 'genres'::text)) = 'array'::text)) AS genres,
    ARRAY( SELECT jsonb_array_elements_text((masters.data -> 'styles'::text)) AS jsonb_array_elements_text
          WHERE (jsonb_typeof((masters.data -> 'styles'::text)) = 'array'::text)) AS styles,
    gm_item_id,
    hash,
    updated_at,
    (data_id)::text AS master_key
   FROM public.masters masters;


--
-- Name: master_by_artist; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.master_by_artist AS
 SELECT DISTINCT entity.data_id AS master_id,
    btrim((element.value ->> 'id'::text)) AS artist_id
   FROM (public.masters entity
     CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN (jsonb_typeof((entity.data -> 'artists'::text)) = 'array'::text) THEN (entity.data -> 'artists'::text)
            ELSE '[]'::jsonb
        END) element(value))
  WHERE ((NULLIF(btrim((element.value ->> 'id'::text)), ''::text) IS NOT NULL) AND (btrim((element.value ->> 'id'::text)) <> '0'::text));


--
-- Name: master_in_genre; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.master_in_genre AS
 SELECT DISTINCT entity.data_id AS master_id,
    tag.value AS genre_name
   FROM (public.masters entity
     CROSS JOIN LATERAL jsonb_array_elements_text(
        CASE
            WHEN (jsonb_typeof((entity.data -> 'genres'::text)) = 'array'::text) THEN (entity.data -> 'genres'::text)
            ELSE '[]'::jsonb
        END) tag(value))
  WHERE (NULLIF(tag.value, ''::text) IS NOT NULL);


--
-- Name: master_in_style; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.master_in_style AS
 SELECT DISTINCT entity.data_id AS master_id,
    tag.value AS style_name
   FROM (public.masters entity
     CROSS JOIN LATERAL jsonb_array_elements_text(
        CASE
            WHEN (jsonb_typeof((entity.data -> 'styles'::text)) = 'array'::text) THEN (entity.data -> 'styles'::text)
            ELSE '[]'::jsonb
        END) tag(value))
  WHERE (NULLIF(tag.value, ''::text) IS NOT NULL);


--
-- Name: artists; Type: TABLE; Schema: musicbrainz; Owner: -
--

CREATE TABLE musicbrainz.artists (
    mbid uuid NOT NULL,
    name text NOT NULL,
    sort_name text,
    type text,
    gender text,
    begin_date text,
    end_date text,
    ended boolean DEFAULT false,
    area text,
    begin_area text,
    end_area text,
    disambiguation text,
    discogs_artist_id bigint,
    aliases jsonb,
    tags jsonb,
    data jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    gm_item_id uuid
);


--
-- Name: mb_artist; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.mb_artist AS
 SELECT mbid,
    name,
    sort_name,
    type,
    gender,
    begin_date,
    end_date,
    ended,
    area,
    begin_area,
    end_area,
    disambiguation,
    discogs_artist_id,
    updated_at
   FROM musicbrainz.artists artists;


--
-- Name: labels; Type: TABLE; Schema: musicbrainz; Owner: -
--

CREATE TABLE musicbrainz.labels (
    mbid uuid NOT NULL,
    name text NOT NULL,
    type text,
    label_code integer,
    begin_date text,
    end_date text,
    ended boolean DEFAULT false,
    area text,
    disambiguation text,
    discogs_label_id bigint,
    data jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    gm_item_id uuid
);


--
-- Name: mb_label; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.mb_label AS
 SELECT mbid,
    name,
    type,
    label_code,
    begin_date,
    end_date,
    ended,
    area,
    disambiguation,
    discogs_label_id,
    updated_at
   FROM musicbrainz.labels labels;


--
-- Name: relationships; Type: TABLE; Schema: musicbrainz; Owner: -
--

CREATE TABLE musicbrainz.relationships (
    id bigint NOT NULL,
    source_mbid uuid NOT NULL,
    target_mbid uuid NOT NULL,
    source_entity_type text NOT NULL,
    target_entity_type text NOT NULL,
    relationship_type text NOT NULL,
    begin_date text,
    end_date text,
    ended boolean DEFAULT false,
    attributes jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: mb_rel_artist_artist; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.mb_rel_artist_artist AS
 SELECT relationship.id AS relationship_id,
    relationship.source_mbid,
    relationship.target_mbid,
    relationship.relationship_type,
    relationship.begin_date,
    relationship.end_date,
    relationship.ended,
    relationship.attributes
   FROM ((musicbrainz.relationships relationship
     JOIN musicbrainz.artists source_entity ON ((source_entity.mbid = relationship.source_mbid)))
     JOIN musicbrainz.artists target_entity ON ((target_entity.mbid = relationship.target_mbid)))
  WHERE ((relationship.source_entity_type = 'artist'::text) AND (relationship.target_entity_type = 'artist'::text));


--
-- Name: mb_rel_artist_label; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.mb_rel_artist_label AS
 SELECT relationship.id AS relationship_id,
    relationship.source_mbid,
    relationship.target_mbid,
    relationship.relationship_type,
    relationship.begin_date,
    relationship.end_date,
    relationship.ended,
    relationship.attributes
   FROM ((musicbrainz.relationships relationship
     JOIN musicbrainz.artists source_entity ON ((source_entity.mbid = relationship.source_mbid)))
     JOIN musicbrainz.labels target_entity ON ((target_entity.mbid = relationship.target_mbid)))
  WHERE ((relationship.source_entity_type = 'artist'::text) AND (relationship.target_entity_type = 'label'::text));


--
-- Name: mb_rel_artist_release; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.mb_rel_artist_release AS
 SELECT relationship.id AS relationship_id,
    relationship.source_mbid,
    relationship.target_mbid,
    relationship.relationship_type,
    relationship.begin_date,
    relationship.end_date,
    relationship.ended,
    relationship.attributes
   FROM ((musicbrainz.relationships relationship
     JOIN musicbrainz.artists source_entity ON ((source_entity.mbid = relationship.source_mbid)))
     JOIN musicbrainz.releases target_entity ON ((target_entity.mbid = relationship.target_mbid)))
  WHERE ((relationship.source_entity_type = 'artist'::text) AND (relationship.target_entity_type = 'release'::text));


--
-- Name: release_groups; Type: TABLE; Schema: musicbrainz; Owner: -
--

CREATE TABLE musicbrainz.release_groups (
    mbid uuid NOT NULL,
    name text NOT NULL,
    type text,
    secondary_types jsonb,
    first_release_date text,
    disambiguation text,
    discogs_master_id bigint,
    data jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    gm_item_id uuid
);


--
-- Name: mb_rel_artist_release_group; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.mb_rel_artist_release_group AS
 SELECT relationship.id AS relationship_id,
    relationship.source_mbid,
    relationship.target_mbid,
    relationship.relationship_type,
    relationship.begin_date,
    relationship.end_date,
    relationship.ended,
    relationship.attributes
   FROM ((musicbrainz.relationships relationship
     JOIN musicbrainz.artists source_entity ON ((source_entity.mbid = relationship.source_mbid)))
     JOIN musicbrainz.release_groups target_entity ON ((target_entity.mbid = relationship.target_mbid)))
  WHERE ((relationship.source_entity_type = 'artist'::text) AND (relationship.target_entity_type = 'release-group'::text));


--
-- Name: mb_rel_label_artist; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.mb_rel_label_artist AS
 SELECT relationship.id AS relationship_id,
    relationship.source_mbid,
    relationship.target_mbid,
    relationship.relationship_type,
    relationship.begin_date,
    relationship.end_date,
    relationship.ended,
    relationship.attributes
   FROM ((musicbrainz.relationships relationship
     JOIN musicbrainz.labels source_entity ON ((source_entity.mbid = relationship.source_mbid)))
     JOIN musicbrainz.artists target_entity ON ((target_entity.mbid = relationship.target_mbid)))
  WHERE ((relationship.source_entity_type = 'label'::text) AND (relationship.target_entity_type = 'artist'::text));


--
-- Name: mb_rel_label_label; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.mb_rel_label_label AS
 SELECT relationship.id AS relationship_id,
    relationship.source_mbid,
    relationship.target_mbid,
    relationship.relationship_type,
    relationship.begin_date,
    relationship.end_date,
    relationship.ended,
    relationship.attributes
   FROM ((musicbrainz.relationships relationship
     JOIN musicbrainz.labels source_entity ON ((source_entity.mbid = relationship.source_mbid)))
     JOIN musicbrainz.labels target_entity ON ((target_entity.mbid = relationship.target_mbid)))
  WHERE ((relationship.source_entity_type = 'label'::text) AND (relationship.target_entity_type = 'label'::text));


--
-- Name: mb_rel_label_release; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.mb_rel_label_release AS
 SELECT relationship.id AS relationship_id,
    relationship.source_mbid,
    relationship.target_mbid,
    relationship.relationship_type,
    relationship.begin_date,
    relationship.end_date,
    relationship.ended,
    relationship.attributes
   FROM ((musicbrainz.relationships relationship
     JOIN musicbrainz.labels source_entity ON ((source_entity.mbid = relationship.source_mbid)))
     JOIN musicbrainz.releases target_entity ON ((target_entity.mbid = relationship.target_mbid)))
  WHERE ((relationship.source_entity_type = 'label'::text) AND (relationship.target_entity_type = 'release'::text));


--
-- Name: mb_rel_label_release_group; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.mb_rel_label_release_group AS
 SELECT relationship.id AS relationship_id,
    relationship.source_mbid,
    relationship.target_mbid,
    relationship.relationship_type,
    relationship.begin_date,
    relationship.end_date,
    relationship.ended,
    relationship.attributes
   FROM ((musicbrainz.relationships relationship
     JOIN musicbrainz.labels source_entity ON ((source_entity.mbid = relationship.source_mbid)))
     JOIN musicbrainz.release_groups target_entity ON ((target_entity.mbid = relationship.target_mbid)))
  WHERE ((relationship.source_entity_type = 'label'::text) AND (relationship.target_entity_type = 'release-group'::text));


--
-- Name: mb_rel_release_artist; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.mb_rel_release_artist AS
 SELECT relationship.id AS relationship_id,
    relationship.source_mbid,
    relationship.target_mbid,
    relationship.relationship_type,
    relationship.begin_date,
    relationship.end_date,
    relationship.ended,
    relationship.attributes
   FROM ((musicbrainz.relationships relationship
     JOIN musicbrainz.releases source_entity ON ((source_entity.mbid = relationship.source_mbid)))
     JOIN musicbrainz.artists target_entity ON ((target_entity.mbid = relationship.target_mbid)))
  WHERE ((relationship.source_entity_type = 'release'::text) AND (relationship.target_entity_type = 'artist'::text));


--
-- Name: mb_rel_release_group_artist; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.mb_rel_release_group_artist AS
 SELECT relationship.id AS relationship_id,
    relationship.source_mbid,
    relationship.target_mbid,
    relationship.relationship_type,
    relationship.begin_date,
    relationship.end_date,
    relationship.ended,
    relationship.attributes
   FROM ((musicbrainz.relationships relationship
     JOIN musicbrainz.release_groups source_entity ON ((source_entity.mbid = relationship.source_mbid)))
     JOIN musicbrainz.artists target_entity ON ((target_entity.mbid = relationship.target_mbid)))
  WHERE ((relationship.source_entity_type = 'release-group'::text) AND (relationship.target_entity_type = 'artist'::text));


--
-- Name: mb_rel_release_group_label; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.mb_rel_release_group_label AS
 SELECT relationship.id AS relationship_id,
    relationship.source_mbid,
    relationship.target_mbid,
    relationship.relationship_type,
    relationship.begin_date,
    relationship.end_date,
    relationship.ended,
    relationship.attributes
   FROM ((musicbrainz.relationships relationship
     JOIN musicbrainz.release_groups source_entity ON ((source_entity.mbid = relationship.source_mbid)))
     JOIN musicbrainz.labels target_entity ON ((target_entity.mbid = relationship.target_mbid)))
  WHERE ((relationship.source_entity_type = 'release-group'::text) AND (relationship.target_entity_type = 'label'::text));


--
-- Name: mb_rel_release_group_release; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.mb_rel_release_group_release AS
 SELECT relationship.id AS relationship_id,
    relationship.source_mbid,
    relationship.target_mbid,
    relationship.relationship_type,
    relationship.begin_date,
    relationship.end_date,
    relationship.ended,
    relationship.attributes
   FROM ((musicbrainz.relationships relationship
     JOIN musicbrainz.release_groups source_entity ON ((source_entity.mbid = relationship.source_mbid)))
     JOIN musicbrainz.releases target_entity ON ((target_entity.mbid = relationship.target_mbid)))
  WHERE ((relationship.source_entity_type = 'release-group'::text) AND (relationship.target_entity_type = 'release'::text));


--
-- Name: mb_rel_release_group_release_group; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.mb_rel_release_group_release_group AS
 SELECT relationship.id AS relationship_id,
    relationship.source_mbid,
    relationship.target_mbid,
    relationship.relationship_type,
    relationship.begin_date,
    relationship.end_date,
    relationship.ended,
    relationship.attributes
   FROM ((musicbrainz.relationships relationship
     JOIN musicbrainz.release_groups source_entity ON ((source_entity.mbid = relationship.source_mbid)))
     JOIN musicbrainz.release_groups target_entity ON ((target_entity.mbid = relationship.target_mbid)))
  WHERE ((relationship.source_entity_type = 'release-group'::text) AND (relationship.target_entity_type = 'release-group'::text));


--
-- Name: mb_rel_release_label; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.mb_rel_release_label AS
 SELECT relationship.id AS relationship_id,
    relationship.source_mbid,
    relationship.target_mbid,
    relationship.relationship_type,
    relationship.begin_date,
    relationship.end_date,
    relationship.ended,
    relationship.attributes
   FROM ((musicbrainz.relationships relationship
     JOIN musicbrainz.releases source_entity ON ((source_entity.mbid = relationship.source_mbid)))
     JOIN musicbrainz.labels target_entity ON ((target_entity.mbid = relationship.target_mbid)))
  WHERE ((relationship.source_entity_type = 'release'::text) AND (relationship.target_entity_type = 'label'::text));


--
-- Name: mb_rel_release_release; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.mb_rel_release_release AS
 SELECT relationship.id AS relationship_id,
    relationship.source_mbid,
    relationship.target_mbid,
    relationship.relationship_type,
    relationship.begin_date,
    relationship.end_date,
    relationship.ended,
    relationship.attributes
   FROM ((musicbrainz.relationships relationship
     JOIN musicbrainz.releases source_entity ON ((source_entity.mbid = relationship.source_mbid)))
     JOIN musicbrainz.releases target_entity ON ((target_entity.mbid = relationship.target_mbid)))
  WHERE ((relationship.source_entity_type = 'release'::text) AND (relationship.target_entity_type = 'release'::text));


--
-- Name: mb_rel_release_release_group; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.mb_rel_release_release_group AS
 SELECT relationship.id AS relationship_id,
    relationship.source_mbid,
    relationship.target_mbid,
    relationship.relationship_type,
    relationship.begin_date,
    relationship.end_date,
    relationship.ended,
    relationship.attributes
   FROM ((musicbrainz.relationships relationship
     JOIN musicbrainz.releases source_entity ON ((source_entity.mbid = relationship.source_mbid)))
     JOIN musicbrainz.release_groups target_entity ON ((target_entity.mbid = relationship.target_mbid)))
  WHERE ((relationship.source_entity_type = 'release'::text) AND (relationship.target_entity_type = 'release-group'::text));


--
-- Name: mb_release; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.mb_release AS
 SELECT mbid,
    name,
    barcode,
    status,
    release_group_mbid,
    discogs_release_id,
    ARRAY( SELECT jsonb_array_elements_text((releases.media -> 'families'::text)) AS jsonb_array_elements_text
          WHERE (jsonb_typeof((releases.media -> 'families'::text)) = 'array'::text)) AS media_families,
    updated_at
   FROM musicbrainz.releases releases;


--
-- Name: mb_release_group; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.mb_release_group AS
 SELECT mbid,
    name,
    type,
    secondary_types,
    first_release_date,
    disambiguation,
    discogs_master_id,
    updated_at
   FROM musicbrainz.release_groups release_groups;


--
-- Name: media_family; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.media_family AS
 SELECT DISTINCT family_name AS name
   FROM ( SELECT releases.data_id AS release_id,
            'discogs'::text AS provider,
            (item.value ->> 'medium'::text) AS medium_id,
            (item.value ->> 'family'::text) AS family_name,
                CASE
                    WHEN ((jsonb_typeof((item.value -> 'qty'::text)) = 'number'::text) AND ((item.value ->> 'qty'::text) ~ '^[0-9]{1,9}$'::text) AND (((item.value ->> 'qty'::text))::bigint >= 1)) THEN ((item.value ->> 'qty'::text))::bigint
                    ELSE (1)::bigint
                END AS qty
           FROM (public.releases releases
             CROSS JOIN LATERAL jsonb_array_elements(
                CASE
                    WHEN (jsonb_typeof((releases.media -> 'items'::text)) = 'array'::text) THEN (releases.media -> 'items'::text)
                    ELSE '[]'::jsonb
                END) item(value))
          WHERE ((NULLIF((item.value ->> 'medium'::text), ''::text) IS NOT NULL) AND (NULLIF((item.value ->> 'family'::text), ''::text) IS NOT NULL))
        UNION ALL
         SELECT releases.data_id AS release_id,
            'musicbrainz'::text AS provider,
            (item.value ->> 'medium'::text) AS medium_id,
            (item.value ->> 'family'::text) AS family_name,
                CASE
                    WHEN ((jsonb_typeof((item.value -> 'qty'::text)) = 'number'::text) AND ((item.value ->> 'qty'::text) ~ '^[0-9]{1,9}$'::text) AND (((item.value ->> 'qty'::text))::bigint >= 1)) THEN ((item.value ->> 'qty'::text))::bigint
                    ELSE (1)::bigint
                END AS qty
           FROM ((musicbrainz.releases mb_release
             JOIN public.releases releases ON (((releases.data_id)::text = (mb_release.discogs_release_id)::text)))
             CROSS JOIN LATERAL jsonb_array_elements(
                CASE
                    WHEN (jsonb_typeof((mb_release.media -> 'items'::text)) = 'array'::text) THEN (mb_release.media -> 'items'::text)
                    ELSE '[]'::jsonb
                END) item(value))
          WHERE ((NULLIF((item.value ->> 'medium'::text), ''::text) IS NOT NULL) AND (NULLIF((item.value ->> 'family'::text), ''::text) IS NOT NULL))) media;


--
-- Name: medium; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.medium AS
 SELECT DISTINCT ON (medium_id) medium_id,
    family_name AS family,
    graph.medium_label(medium_id) AS label
   FROM ( SELECT releases.data_id AS release_id,
            'discogs'::text AS provider,
            (item.value ->> 'medium'::text) AS medium_id,
            (item.value ->> 'family'::text) AS family_name,
                CASE
                    WHEN ((jsonb_typeof((item.value -> 'qty'::text)) = 'number'::text) AND ((item.value ->> 'qty'::text) ~ '^[0-9]{1,9}$'::text) AND (((item.value ->> 'qty'::text))::bigint >= 1)) THEN ((item.value ->> 'qty'::text))::bigint
                    ELSE (1)::bigint
                END AS qty
           FROM (public.releases releases
             CROSS JOIN LATERAL jsonb_array_elements(
                CASE
                    WHEN (jsonb_typeof((releases.media -> 'items'::text)) = 'array'::text) THEN (releases.media -> 'items'::text)
                    ELSE '[]'::jsonb
                END) item(value))
          WHERE ((NULLIF((item.value ->> 'medium'::text), ''::text) IS NOT NULL) AND (NULLIF((item.value ->> 'family'::text), ''::text) IS NOT NULL))
        UNION ALL
         SELECT releases.data_id AS release_id,
            'musicbrainz'::text AS provider,
            (item.value ->> 'medium'::text) AS medium_id,
            (item.value ->> 'family'::text) AS family_name,
                CASE
                    WHEN ((jsonb_typeof((item.value -> 'qty'::text)) = 'number'::text) AND ((item.value ->> 'qty'::text) ~ '^[0-9]{1,9}$'::text) AND (((item.value ->> 'qty'::text))::bigint >= 1)) THEN ((item.value ->> 'qty'::text))::bigint
                    ELSE (1)::bigint
                END AS qty
           FROM ((musicbrainz.releases mb_release
             JOIN public.releases releases ON (((releases.data_id)::text = (mb_release.discogs_release_id)::text)))
             CROSS JOIN LATERAL jsonb_array_elements(
                CASE
                    WHEN (jsonb_typeof((mb_release.media -> 'items'::text)) = 'array'::text) THEN (mb_release.media -> 'items'::text)
                    ELSE '[]'::jsonb
                END) item(value))
          WHERE ((NULLIF((item.value ->> 'medium'::text), ''::text) IS NOT NULL) AND (NULLIF((item.value ->> 'family'::text), ''::text) IS NOT NULL))) media
  ORDER BY medium_id, family_name;


--
-- Name: member_of; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.member_of AS
 SELECT btrim((element.value ->> 'id'::text)) AS member_artist_id,
    artists.data_id AS group_artist_id
   FROM (public.artists artists
     CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN (jsonb_typeof((artists.data -> 'members'::text)) = 'array'::text) THEN (artists.data -> 'members'::text)
            ELSE '[]'::jsonb
        END) element(value))
  WHERE ((NULLIF(btrim((element.value ->> 'id'::text)), ''::text) IS NOT NULL) AND (btrim((element.value ->> 'id'::text)) <> '0'::text))
UNION
 SELECT artists.data_id AS member_artist_id,
    btrim((element.value ->> 'id'::text)) AS group_artist_id
   FROM (public.artists artists
     CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN (jsonb_typeof((artists.data -> 'groups'::text)) = 'array'::text) THEN (artists.data -> 'groups'::text)
            ELSE '[]'::jsonb
        END) element(value))
  WHERE ((NULLIF(btrim((element.value ->> 'id'::text)), ''::text) IS NOT NULL) AND (btrim((element.value ->> 'id'::text)) <> '0'::text));


--
-- Name: on_label; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.on_label AS
 SELECT DISTINCT entity.data_id AS release_id,
    btrim((element.value ->> 'id'::text)) AS label_id
   FROM (public.releases entity
     CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN (jsonb_typeof((entity.data -> 'labels'::text)) = 'array'::text) THEN (entity.data -> 'labels'::text)
            ELSE '[]'::jsonb
        END) element(value))
  WHERE ((NULLIF(btrim((element.value ->> 'id'::text)), ''::text) IS NOT NULL) AND (btrim((element.value ->> 'id'::text)) <> '0'::text));


--
-- Name: owned_copies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.owned_copies (
    id uuid DEFAULT uuidv7() NOT NULL,
    user_id uuid NOT NULL,
    artifact_id uuid,
    item_id uuid NOT NULL,
    collection_row_id uuid,
    acquired_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: owns; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.owns AS
 SELECT id AS owned_copy_id,
    user_id,
    item_id,
    artifact_id,
    collection_row_id,
    acquired_at
   FROM public.owned_copies owned_copies;


--
-- Name: part_of; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.part_of AS
 SELECT DISTINCT style.value AS style_name,
    genre.genre_name
   FROM ((( SELECT releases.data AS document
           FROM public.releases releases
        UNION ALL
         SELECT masters.data AS document
           FROM public.masters masters) source
     CROSS JOIN LATERAL ( SELECT single.value AS genre_name
           FROM jsonb_array_elements_text(
                CASE
                    WHEN (jsonb_typeof((source.document -> 'genres'::text)) = 'array'::text) THEN (source.document -> 'genres'::text)
                    ELSE '[]'::jsonb
                END) single(value)) genre)
     CROSS JOIN LATERAL jsonb_array_elements_text(
        CASE
            WHEN (jsonb_typeof((source.document -> 'styles'::text)) = 'array'::text) THEN (source.document -> 'styles'::text)
            ELSE '[]'::jsonb
        END) style(value))
  WHERE ((jsonb_array_length(
        CASE
            WHEN (jsonb_typeof((source.document -> 'genres'::text)) = 'array'::text) THEN (source.document -> 'genres'::text)
            ELSE '[]'::jsonb
        END) = 1) AND (NULLIF(genre.genre_name, ''::text) IS NOT NULL) AND (NULLIF(style.value, ''::text) IS NOT NULL));


--
-- Name: person; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.person AS
 SELECT DISTINCT person_name AS name
   FROM ( SELECT releases.data_id AS release_id,
            (credit_1.value ->> 'name'::text) AS person_name,
            (credit_1.value ->> 'role'::text) AS role,
            btrim((credit_1.value ->> 'id'::text)) AS artist_id
           FROM (public.releases releases
             CROSS JOIN LATERAL jsonb_array_elements(
                CASE
                    WHEN (jsonb_typeof((releases.data -> 'extraartists'::text)) = 'array'::text) THEN (releases.data -> 'extraartists'::text)
                    ELSE '[]'::jsonb
                END) credit_1(value))
          WHERE ((NULLIF((credit_1.value ->> 'name'::text), ''::text) IS NOT NULL) AND (NULLIF((credit_1.value ->> 'role'::text), ''::text) IS NOT NULL))) credit;


--
-- Name: release; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.release AS
 SELECT data_id AS release_id,
    (data ->> 'title'::text) AS title,
    (data ->> 'year'::text) AS year,
    NULLIF(btrim((data ->> 'country'::text)), ''::text) AS country,
    ARRAY( SELECT jsonb_array_elements_text((releases.data -> 'genres'::text)) AS jsonb_array_elements_text
          WHERE (jsonb_typeof((releases.data -> 'genres'::text)) = 'array'::text)) AS genres,
    ARRAY( SELECT jsonb_array_elements_text((releases.data -> 'styles'::text)) AS jsonb_array_elements_text
          WHERE (jsonb_typeof((releases.data -> 'styles'::text)) = 'array'::text)) AS styles,
    ARRAY( SELECT jsonb_array_elements_text((releases.media -> 'families'::text)) AS jsonb_array_elements_text
          WHERE (jsonb_typeof((releases.media -> 'families'::text)) = 'array'::text)) AS media_families,
    gm_item_id,
    hash,
    updated_at,
    (data_id)::text AS release_key
   FROM public.releases releases;


--
-- Name: same_as; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.same_as AS
 SELECT DISTINCT person_name,
    artist_id
   FROM ( SELECT releases.data_id AS release_id,
            (credit_1.value ->> 'name'::text) AS person_name,
            (credit_1.value ->> 'role'::text) AS role,
            btrim((credit_1.value ->> 'id'::text)) AS artist_id
           FROM (public.releases releases
             CROSS JOIN LATERAL jsonb_array_elements(
                CASE
                    WHEN (jsonb_typeof((releases.data -> 'extraartists'::text)) = 'array'::text) THEN (releases.data -> 'extraartists'::text)
                    ELSE '[]'::jsonb
                END) credit_1(value))
          WHERE ((NULLIF((credit_1.value ->> 'name'::text), ''::text) IS NOT NULL) AND (NULLIF((credit_1.value ->> 'role'::text), ''::text) IS NOT NULL))) credit
  WHERE ((NULLIF(btrim(artist_id), ''::text) IS NOT NULL) AND (btrim(artist_id) <> '0'::text));


--
-- Name: style; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.style AS
 SELECT DISTINCT style.value AS name
   FROM (( SELECT releases.data AS document
           FROM public.releases releases
        UNION ALL
         SELECT masters.data AS document
           FROM public.masters masters) source
     CROSS JOIN LATERAL jsonb_array_elements_text(
        CASE
            WHEN (jsonb_typeof((source.document -> 'styles'::text)) = 'array'::text) THEN (source.document -> 'styles'::text)
            ELSE '[]'::jsonb
        END) style(value))
  WHERE (NULLIF(style.value, ''::text) IS NOT NULL);


--
-- Name: sublabel_of; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.sublabel_of AS
 SELECT labels.data_id AS sublabel_id,
    btrim(((labels.data -> 'parentLabel'::text) ->> 'id'::text)) AS parent_label_id
   FROM public.labels labels
  WHERE ((NULLIF(btrim(((labels.data -> 'parentLabel'::text) ->> 'id'::text)), ''::text) IS NOT NULL) AND (btrim(((labels.data -> 'parentLabel'::text) ->> 'id'::text)) <> '0'::text))
UNION
 SELECT btrim((element.value ->> 'id'::text)) AS sublabel_id,
    labels.data_id AS parent_label_id
   FROM (public.labels labels
     CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN (jsonb_typeof((labels.data -> 'sublabels'::text)) = 'array'::text) THEN (labels.data -> 'sublabels'::text)
            ELSE '[]'::jsonb
        END) element(value))
  WHERE ((NULLIF(btrim((element.value ->> 'id'::text)), ''::text) IS NOT NULL) AND (btrim((element.value ->> 'id'::text)) <> '0'::text));


--
-- Name: user_wantlists; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_wantlists (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    release_id bigint NOT NULL,
    title character varying(500),
    artist character varying(500),
    year integer,
    format character varying(255),
    media jsonb,
    rating smallint,
    notes text,
    date_added timestamp with time zone,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    gm_item_id uuid
);


--
-- Name: wants; Type: VIEW; Schema: graph; Owner: -
--

CREATE VIEW graph.wants AS
 SELECT wantlist.id AS wantlist_id,
    wantlist.user_id,
    releases.data_id AS release_id,
    wantlist.rating,
    wantlist.date_added
   FROM (public.user_wantlists wantlist
     JOIN public.releases releases ON (((releases.data_id)::text = (wantlist.release_id)::text)));


--
-- Name: catalog; Type: PROPERTY GRAPH; Schema: graph; Owner: -
--

CREATE PROPERTY GRAPH graph.catalog
    VERTEX TABLES (
        graph.app_user KEY (user_id) PROPERTIES (created_at, is_active, is_admin, updated_at, user_id),
        graph.artist KEY (artist_key) PROPERTIES ((artist_id)::text AS artist_id, gm_item_id, hash, name, updated_at),
        graph.catalog_item KEY (item_id) PROPERTIES (created_at, item_id, kind),
        graph.company KEY (company_id) PROPERTIES (company_id, discogs_label_id, name),
        graph.genre KEY (name) PROPERTIES (name),
        graph.label KEY (label_key) PROPERTIES (gm_item_id, hash, (label_id)::text AS label_id, name, updated_at),
        graph.master KEY (master_key) PROPERTIES (genres, gm_item_id, hash, (master_id)::text AS master_id, styles, title, updated_at, year),
        graph.mb_artist KEY (mbid) PROPERTIES (area, begin_area, begin_date, disambiguation, discogs_artist_id, end_area, end_date, ended, gender, mbid, name, sort_name, type, updated_at),
        graph.mb_label KEY (mbid) PROPERTIES (area, begin_date, disambiguation, (discogs_label_id)::text AS discogs_label_id, end_date, ended, label_code, mbid, name, type, updated_at),
        graph.mb_release KEY (mbid) PROPERTIES (barcode, discogs_release_id, mbid, media_families, name, release_group_mbid, status, updated_at),
        graph.mb_release_group KEY (mbid) PROPERTIES (disambiguation, discogs_master_id, first_release_date, mbid, name, secondary_types, type, updated_at),
        graph.media_family KEY (name) PROPERTIES (name),
        graph.medium KEY (medium_id) PROPERTIES (family, label, medium_id),
        graph.person KEY (name) PROPERTIES (name),
        graph.release KEY (release_key) PROPERTIES (country, genres, gm_item_id, hash, media_families, release_id, styles, title, updated_at, year),
        graph.style KEY (name) PROPERTIES (name)
    )
    EDGE TABLES (
        graph.alias_of KEY (alias_artist_id, artist_id) SOURCE KEY (alias_artist_id) REFERENCES artist (artist_key) DESTINATION KEY (artist_id) REFERENCES artist (artist_key) PROPERTIES (alias_artist_id, (artist_id)::text AS artist_id),
        graph.by_artist KEY (release_id, artist_id) SOURCE KEY (release_id) REFERENCES release (release_key) DESTINATION KEY (artist_id) REFERENCES artist (artist_key) PROPERTIES (artist_id, release_id),
        graph.collected KEY (collection_id) SOURCE KEY (user_id) REFERENCES app_user (user_id) DESTINATION KEY (release_id) REFERENCES release (release_key) PROPERTIES (collection_id, condition, date_added, folder_id, instance_id, rating, release_id, user_id),
        graph.credited_on KEY (person_name, release_id, role) SOURCE KEY (person_name) REFERENCES person (name) DESTINATION KEY (release_id) REFERENCES release (release_key) PROPERTIES (person_name, release_id, role, role_category),
        graph.credited_to KEY (release_id, company_id, role, source) SOURCE KEY (release_id) REFERENCES release (release_key) DESTINATION KEY (company_id) REFERENCES company (company_id) PROPERTIES (company_id, release_id, role, role_category, source),
        graph.derived_from KEY (release_id, master_id) SOURCE KEY (release_id) REFERENCES release (release_key) DESTINATION KEY (master_id) REFERENCES master (master_key) PROPERTIES (master_id, release_id),
        graph.in_family KEY (medium_id, family_name) SOURCE KEY (medium_id) REFERENCES medium (medium_id) DESTINATION KEY (family_name) REFERENCES media_family (name) PROPERTIES (family_name, medium_id),
        graph.in_genre KEY (release_id, genre_name) SOURCE KEY (release_id) REFERENCES release (release_key) DESTINATION KEY (genre_name) REFERENCES genre (name) PROPERTIES (genre_name, release_id),
        graph.in_style KEY (release_id, style_name) SOURCE KEY (release_id) REFERENCES release (release_key) DESTINATION KEY (style_name) REFERENCES style (name) PROPERTIES (release_id, style_name),
        graph.issued_on KEY (release_id, medium_id, source) SOURCE KEY (release_id) REFERENCES release (release_key) DESTINATION KEY (medium_id) REFERENCES medium (medium_id) PROPERTIES (medium_id, qty, release_id, source),
        graph.master_by_artist KEY (master_id, artist_id) SOURCE KEY (master_id) REFERENCES master (master_key) DESTINATION KEY (artist_id) REFERENCES artist (artist_key) PROPERTIES (artist_id, (master_id)::text AS master_id),
        graph.master_in_genre KEY (master_id, genre_name) SOURCE KEY (master_id) REFERENCES master (master_key) DESTINATION KEY (genre_name) REFERENCES genre (name) PROPERTIES (genre_name, (master_id)::text AS master_id),
        graph.master_in_style KEY (master_id, style_name) SOURCE KEY (master_id) REFERENCES master (master_key) DESTINATION KEY (style_name) REFERENCES style (name) PROPERTIES ((master_id)::text AS master_id, style_name),
        graph.mb_rel_artist_artist KEY (relationship_id) SOURCE KEY (source_mbid) REFERENCES mb_artist (mbid) DESTINATION KEY (target_mbid) REFERENCES mb_artist (mbid) DEFAULT LABEL PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid) LABEL mb_related PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid),
        graph.mb_rel_artist_label KEY (relationship_id) SOURCE KEY (source_mbid) REFERENCES mb_artist (mbid) DESTINATION KEY (target_mbid) REFERENCES mb_label (mbid) DEFAULT LABEL PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid) LABEL mb_related PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid),
        graph.mb_rel_artist_release KEY (relationship_id) SOURCE KEY (source_mbid) REFERENCES mb_artist (mbid) DESTINATION KEY (target_mbid) REFERENCES mb_release (mbid) DEFAULT LABEL PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid) LABEL mb_related PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid),
        graph.mb_rel_artist_release_group KEY (relationship_id) SOURCE KEY (source_mbid) REFERENCES mb_artist (mbid) DESTINATION KEY (target_mbid) REFERENCES mb_release_group (mbid) DEFAULT LABEL PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid) LABEL mb_related PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid),
        graph.mb_rel_label_artist KEY (relationship_id) SOURCE KEY (source_mbid) REFERENCES mb_label (mbid) DESTINATION KEY (target_mbid) REFERENCES mb_artist (mbid) DEFAULT LABEL PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid) LABEL mb_related PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid),
        graph.mb_rel_label_label KEY (relationship_id) SOURCE KEY (source_mbid) REFERENCES mb_label (mbid) DESTINATION KEY (target_mbid) REFERENCES mb_label (mbid) DEFAULT LABEL PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid) LABEL mb_related PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid),
        graph.mb_rel_label_release KEY (relationship_id) SOURCE KEY (source_mbid) REFERENCES mb_label (mbid) DESTINATION KEY (target_mbid) REFERENCES mb_release (mbid) DEFAULT LABEL PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid) LABEL mb_related PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid),
        graph.mb_rel_label_release_group KEY (relationship_id) SOURCE KEY (source_mbid) REFERENCES mb_label (mbid) DESTINATION KEY (target_mbid) REFERENCES mb_release_group (mbid) DEFAULT LABEL PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid) LABEL mb_related PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid),
        graph.mb_rel_release_artist KEY (relationship_id) SOURCE KEY (source_mbid) REFERENCES mb_release (mbid) DESTINATION KEY (target_mbid) REFERENCES mb_artist (mbid) DEFAULT LABEL PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid) LABEL mb_related PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid),
        graph.mb_rel_release_group_artist KEY (relationship_id) SOURCE KEY (source_mbid) REFERENCES mb_release_group (mbid) DESTINATION KEY (target_mbid) REFERENCES mb_artist (mbid) DEFAULT LABEL PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid) LABEL mb_related PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid),
        graph.mb_rel_release_group_label KEY (relationship_id) SOURCE KEY (source_mbid) REFERENCES mb_release_group (mbid) DESTINATION KEY (target_mbid) REFERENCES mb_label (mbid) DEFAULT LABEL PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid) LABEL mb_related PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid),
        graph.mb_rel_release_group_release KEY (relationship_id) SOURCE KEY (source_mbid) REFERENCES mb_release_group (mbid) DESTINATION KEY (target_mbid) REFERENCES mb_release (mbid) DEFAULT LABEL PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid) LABEL mb_related PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid),
        graph.mb_rel_release_group_release_group KEY (relationship_id) SOURCE KEY (source_mbid) REFERENCES mb_release_group (mbid) DESTINATION KEY (target_mbid) REFERENCES mb_release_group (mbid) DEFAULT LABEL PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid) LABEL mb_related PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid),
        graph.mb_rel_release_label KEY (relationship_id) SOURCE KEY (source_mbid) REFERENCES mb_release (mbid) DESTINATION KEY (target_mbid) REFERENCES mb_label (mbid) DEFAULT LABEL PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid) LABEL mb_related PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid),
        graph.mb_rel_release_release KEY (relationship_id) SOURCE KEY (source_mbid) REFERENCES mb_release (mbid) DESTINATION KEY (target_mbid) REFERENCES mb_release (mbid) DEFAULT LABEL PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid) LABEL mb_related PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid),
        graph.mb_rel_release_release_group KEY (relationship_id) SOURCE KEY (source_mbid) REFERENCES mb_release (mbid) DESTINATION KEY (target_mbid) REFERENCES mb_release_group (mbid) DEFAULT LABEL PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid) LABEL mb_related PROPERTIES (attributes, begin_date, end_date, ended, relationship_id, relationship_type, source_mbid, target_mbid),
        graph.member_of KEY (member_artist_id, group_artist_id) SOURCE KEY (member_artist_id) REFERENCES artist (artist_key) DESTINATION KEY (group_artist_id) REFERENCES artist (artist_key) PROPERTIES (group_artist_id, member_artist_id),
        graph.on_label KEY (release_id, label_id) SOURCE KEY (release_id) REFERENCES release (release_key) DESTINATION KEY (label_id) REFERENCES label (label_key) PROPERTIES (label_id, release_id),
        graph.owns KEY (owned_copy_id) SOURCE KEY (user_id) REFERENCES app_user (user_id) DESTINATION KEY (item_id) REFERENCES catalog_item (item_id) PROPERTIES (acquired_at, artifact_id, collection_row_id, item_id, owned_copy_id, user_id),
        graph.part_of KEY (style_name, genre_name) SOURCE KEY (style_name) REFERENCES style (name) DESTINATION KEY (genre_name) REFERENCES genre (name) PROPERTIES (genre_name, style_name),
        graph.same_as KEY (person_name, artist_id) SOURCE KEY (person_name) REFERENCES person (name) DESTINATION KEY (artist_id) REFERENCES artist (artist_key) PROPERTIES (artist_id, person_name),
        graph.sublabel_of KEY (sublabel_id, parent_label_id) SOURCE KEY (sublabel_id) REFERENCES label (label_key) DESTINATION KEY (parent_label_id) REFERENCES label (label_key) PROPERTIES (parent_label_id, sublabel_id),
        graph.wants KEY (wantlist_id) SOURCE KEY (user_id) REFERENCES app_user (user_id) DESTINATION KEY (release_id) REFERENCES release (release_key) PROPERTIES (date_added, rating, release_id, user_id, wantlist_id)
    );


--
-- Name: activity_summary; Type: TABLE; Schema: insights; Owner: -
--

CREATE TABLE insights.activity_summary (
    summary_date date NOT NULL,
    dimension text NOT NULL,
    dimension_key text NOT NULL,
    record_count bigint NOT NULL,
    subject_count bigint NOT NULL,
    candidate_set_count bigint,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT activity_summary_dimension_check CHECK ((dimension = ANY (ARRAY['event_type'::text, 'policy_id'::text])))
);


--
-- Name: artist_centrality; Type: TABLE; Schema: insights; Owner: -
--

CREATE TABLE insights.artist_centrality (
    rank integer NOT NULL,
    artist_id text NOT NULL,
    artist_name text NOT NULL,
    edge_count bigint NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: community_counts; Type: TABLE; Schema: insights; Owner: -
--

CREATE TABLE insights.community_counts (
    release_id bigint NOT NULL,
    have_count integer DEFAULT 0 NOT NULL,
    want_count integer DEFAULT 0 NOT NULL,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: computation_log; Type: TABLE; Schema: insights; Owner: -
--

CREATE TABLE insights.computation_log (
    id bigint NOT NULL,
    insight_type text NOT NULL,
    status text DEFAULT 'running'::text NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    rows_affected bigint,
    error_message text,
    duration_ms bigint
);


--
-- Name: computation_log_id_seq; Type: SEQUENCE; Schema: insights; Owner: -
--

ALTER TABLE insights.computation_log ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME insights.computation_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: data_completeness; Type: TABLE; Schema: insights; Owner: -
--

CREATE TABLE insights.data_completeness (
    entity_type text NOT NULL,
    total_count bigint NOT NULL,
    with_image bigint DEFAULT 0 NOT NULL,
    with_year bigint DEFAULT 0 NOT NULL,
    with_country bigint DEFAULT 0 NOT NULL,
    with_genre bigint DEFAULT 0 NOT NULL,
    completeness_pct numeric(5,2) DEFAULT 0 NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: genre_trends; Type: TABLE; Schema: insights; Owner: -
--

CREATE TABLE insights.genre_trends (
    genre text NOT NULL,
    decade integer NOT NULL,
    release_count bigint NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: label_longevity; Type: TABLE; Schema: insights; Owner: -
--

CREATE TABLE insights.label_longevity (
    rank integer NOT NULL,
    label_id text NOT NULL,
    label_name text NOT NULL,
    first_year integer NOT NULL,
    last_year integer,
    years_active integer NOT NULL,
    total_releases bigint NOT NULL,
    peak_decade integer,
    still_active boolean DEFAULT false NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: monthly_anniversaries; Type: TABLE; Schema: insights; Owner: -
--

CREATE TABLE insights.monthly_anniversaries (
    master_id text NOT NULL,
    title text NOT NULL,
    artist_name text,
    release_year integer NOT NULL,
    anniversary integer NOT NULL,
    computed_month integer NOT NULL,
    computed_year integer NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: release_rarity; Type: TABLE; Schema: insights; Owner: -
--

CREATE TABLE insights.release_rarity (
    release_id bigint NOT NULL,
    title text,
    artist_name text,
    year integer,
    rarity_score real NOT NULL,
    tier text NOT NULL,
    hidden_gem_score real,
    pressing_scarcity real,
    label_catalog real,
    format_rarity real,
    temporal_scarcity real,
    graph_isolation real,
    collection_prevalence real,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    media_families jsonb,
    family_signals jsonb,
    medium_rarity real
);


--
-- Name: external_links; Type: TABLE; Schema: musicbrainz; Owner: -
--

CREATE TABLE musicbrainz.external_links (
    id bigint NOT NULL,
    mbid uuid NOT NULL,
    entity_type text NOT NULL,
    service_name text NOT NULL,
    url text NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: external_links_id_seq; Type: SEQUENCE; Schema: musicbrainz; Owner: -
--

ALTER TABLE musicbrainz.external_links ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME musicbrainz.external_links_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: relationships_id_seq; Type: SEQUENCE; Schema: musicbrainz; Owner: -
--

ALTER TABLE musicbrainz.relationships ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME musicbrainz.relationships_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: admin_audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_audit_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    admin_id uuid NOT NULL,
    action character varying(100) NOT NULL,
    target character varying(255),
    details jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: app_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.app_config (
    key character varying(255) NOT NULL,
    value text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: app_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.app_tokens (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    scope text[] NOT NULL,
    token_hash character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_used_at timestamp with time zone,
    revoked_at timestamp with time zone
);


--
-- Name: artifacts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.artifacts (
    id uuid DEFAULT uuidv7() NOT NULL,
    item_id uuid NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: collection_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.collection_snapshots (
    id uuid DEFAULT uuidv7() NOT NULL,
    user_id uuid NOT NULL,
    taken_at timestamp with time zone DEFAULT now() NOT NULL,
    content_hash bytea NOT NULL,
    item_count integer NOT NULL,
    copy_ids uuid[] NOT NULL
);


--
-- Name: extraction_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.extraction_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    triggered_by uuid NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    record_counts jsonb,
    error_message text,
    extractor_version character varying(50),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: oauth_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.oauth_tokens (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    provider character varying(50) NOT NULL,
    access_token text NOT NULL,
    access_secret text NOT NULL,
    provider_username character varying(255),
    provider_user_id character varying(255),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: observations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.observations (
    id uuid DEFAULT uuidv7() NOT NULL,
    user_id uuid NOT NULL,
    owned_copy_id uuid,
    artifact_id uuid,
    kind text NOT NULL,
    value text NOT NULL,
    source text NOT NULL,
    confidence real,
    observed_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT observations_subject_present CHECK (((owned_copy_id IS NOT NULL) OR (artifact_id IS NOT NULL)))
);


--
-- Name: provider_aliases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.provider_aliases (
    id uuid DEFAULT uuidv7() NOT NULL,
    provider text NOT NULL,
    entity_kind text NOT NULL,
    external_id text NOT NULL,
    native_id uuid NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_to timestamp with time zone,
    confidence real DEFAULT 1.0 NOT NULL,
    source text DEFAULT 'catalog'::text NOT NULL,
    asserted_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: queue_metrics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.queue_metrics (
    id bigint NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    queue_name character varying(100) NOT NULL,
    messages_ready integer,
    messages_unacknowledged integer,
    consumers integer,
    publish_rate real,
    ack_rate real
);


--
-- Name: queue_metrics_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.queue_metrics ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.queue_metrics_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: service_health_metrics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.service_health_metrics (
    id bigint NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    service_name character varying(50) NOT NULL,
    status character varying(20),
    response_time_ms real,
    endpoint_stats jsonb
);


--
-- Name: service_health_metrics_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.service_health_metrics ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.service_health_metrics_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: sync_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sync_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    sync_type character varying(50) NOT NULL,
    status character varying(50) DEFAULT 'pending'::character varying NOT NULL,
    items_synced integer,
    pages_fetched integer,
    error_message text,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone
);


--
-- Name: events_default; Type: TABLE ATTACH; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.events ATTACH PARTITION activity.events_default DEFAULT;


--
-- Name: impressions_default; Type: TABLE ATTACH; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.impressions ATTACH PARTITION activity.impressions_default DEFAULT;


--
-- Name: consent_grants consent_grants_pkey; Type: CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.consent_grants
    ADD CONSTRAINT consent_grants_pkey PRIMARY KEY (id);


--
-- Name: erasures erasures_pkey; Type: CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.erasures
    ADD CONSTRAINT erasures_pkey PRIMARY KEY (id);


--
-- Name: events events_occurred_at_idempotency_key_key; Type: CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.events
    ADD CONSTRAINT events_occurred_at_idempotency_key_key UNIQUE (occurred_at, idempotency_key);


--
-- Name: events_default events_default_occurred_at_idempotency_key_key; Type: CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.events_default
    ADD CONSTRAINT events_default_occurred_at_idempotency_key_key UNIQUE (occurred_at, idempotency_key);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (occurred_at, event_id);


--
-- Name: events_default events_default_pkey; Type: CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.events_default
    ADD CONSTRAINT events_default_pkey PRIMARY KEY (occurred_at, event_id);


--
-- Name: impressions impressions_pkey; Type: CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.impressions
    ADD CONSTRAINT impressions_pkey PRIMARY KEY (occurred_at, impression_id);


--
-- Name: impressions_default impressions_default_pkey; Type: CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.impressions_default
    ADD CONSTRAINT impressions_default_pkey PRIMARY KEY (occurred_at, impression_id);


--
-- Name: user_subjects user_subjects_pkey; Type: CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.user_subjects
    ADD CONSTRAINT user_subjects_pkey PRIMARY KEY (user_id);


--
-- Name: user_subjects user_subjects_subject_id_key; Type: CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.user_subjects
    ADD CONSTRAINT user_subjects_subject_id_key UNIQUE (subject_id);


--
-- Name: activity_summary activity_summary_pkey; Type: CONSTRAINT; Schema: insights; Owner: -
--

ALTER TABLE ONLY insights.activity_summary
    ADD CONSTRAINT activity_summary_pkey PRIMARY KEY (summary_date, dimension, dimension_key);


--
-- Name: artist_centrality artist_centrality_pkey; Type: CONSTRAINT; Schema: insights; Owner: -
--

ALTER TABLE ONLY insights.artist_centrality
    ADD CONSTRAINT artist_centrality_pkey PRIMARY KEY (rank);


--
-- Name: community_counts community_counts_pkey; Type: CONSTRAINT; Schema: insights; Owner: -
--

ALTER TABLE ONLY insights.community_counts
    ADD CONSTRAINT community_counts_pkey PRIMARY KEY (release_id);


--
-- Name: computation_log computation_log_pkey; Type: CONSTRAINT; Schema: insights; Owner: -
--

ALTER TABLE ONLY insights.computation_log
    ADD CONSTRAINT computation_log_pkey PRIMARY KEY (id);


--
-- Name: data_completeness data_completeness_pkey; Type: CONSTRAINT; Schema: insights; Owner: -
--

ALTER TABLE ONLY insights.data_completeness
    ADD CONSTRAINT data_completeness_pkey PRIMARY KEY (entity_type);


--
-- Name: genre_trends genre_trends_pkey; Type: CONSTRAINT; Schema: insights; Owner: -
--

ALTER TABLE ONLY insights.genre_trends
    ADD CONSTRAINT genre_trends_pkey PRIMARY KEY (genre, decade);


--
-- Name: label_longevity label_longevity_pkey; Type: CONSTRAINT; Schema: insights; Owner: -
--

ALTER TABLE ONLY insights.label_longevity
    ADD CONSTRAINT label_longevity_pkey PRIMARY KEY (rank);


--
-- Name: monthly_anniversaries monthly_anniversaries_pkey; Type: CONSTRAINT; Schema: insights; Owner: -
--

ALTER TABLE ONLY insights.monthly_anniversaries
    ADD CONSTRAINT monthly_anniversaries_pkey PRIMARY KEY (master_id, computed_year, computed_month);


--
-- Name: release_rarity release_rarity_pkey; Type: CONSTRAINT; Schema: insights; Owner: -
--

ALTER TABLE ONLY insights.release_rarity
    ADD CONSTRAINT release_rarity_pkey PRIMARY KEY (release_id);


--
-- Name: artists artists_pkey; Type: CONSTRAINT; Schema: musicbrainz; Owner: -
--

ALTER TABLE ONLY musicbrainz.artists
    ADD CONSTRAINT artists_pkey PRIMARY KEY (mbid);


--
-- Name: external_links external_links_mbid_entity_type_service_name_url_key; Type: CONSTRAINT; Schema: musicbrainz; Owner: -
--

ALTER TABLE ONLY musicbrainz.external_links
    ADD CONSTRAINT external_links_mbid_entity_type_service_name_url_key UNIQUE (mbid, entity_type, service_name, url);


--
-- Name: external_links external_links_pkey; Type: CONSTRAINT; Schema: musicbrainz; Owner: -
--

ALTER TABLE ONLY musicbrainz.external_links
    ADD CONSTRAINT external_links_pkey PRIMARY KEY (id);


--
-- Name: labels labels_pkey; Type: CONSTRAINT; Schema: musicbrainz; Owner: -
--

ALTER TABLE ONLY musicbrainz.labels
    ADD CONSTRAINT labels_pkey PRIMARY KEY (mbid);


--
-- Name: relationships relationships_natural_key; Type: CONSTRAINT; Schema: musicbrainz; Owner: -
--

ALTER TABLE ONLY musicbrainz.relationships
    ADD CONSTRAINT relationships_natural_key UNIQUE NULLS NOT DISTINCT (source_mbid, target_mbid, source_entity_type, target_entity_type, relationship_type, begin_date, end_date, attributes);


--
-- Name: relationships relationships_pkey; Type: CONSTRAINT; Schema: musicbrainz; Owner: -
--

ALTER TABLE ONLY musicbrainz.relationships
    ADD CONSTRAINT relationships_pkey PRIMARY KEY (id);


--
-- Name: release_groups release_groups_pkey; Type: CONSTRAINT; Schema: musicbrainz; Owner: -
--

ALTER TABLE ONLY musicbrainz.release_groups
    ADD CONSTRAINT release_groups_pkey PRIMARY KEY (mbid);


--
-- Name: releases releases_pkey; Type: CONSTRAINT; Schema: musicbrainz; Owner: -
--

ALTER TABLE ONLY musicbrainz.releases
    ADD CONSTRAINT releases_pkey PRIMARY KEY (mbid);


--
-- Name: admin_audit_log admin_audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_audit_log
    ADD CONSTRAINT admin_audit_log_pkey PRIMARY KEY (id);


--
-- Name: app_config app_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_config
    ADD CONSTRAINT app_config_pkey PRIMARY KEY (key);


--
-- Name: app_tokens app_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_tokens
    ADD CONSTRAINT app_tokens_pkey PRIMARY KEY (id);


--
-- Name: artifacts artifacts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifacts
    ADD CONSTRAINT artifacts_pkey PRIMARY KEY (id);


--
-- Name: artists artists_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artists
    ADD CONSTRAINT artists_pkey PRIMARY KEY (data_id);


--
-- Name: catalog_items catalog_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalog_items
    ADD CONSTRAINT catalog_items_pkey PRIMARY KEY (id);


--
-- Name: collection_snapshots collection_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_snapshots
    ADD CONSTRAINT collection_snapshots_pkey PRIMARY KEY (id);


--
-- Name: extraction_history extraction_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.extraction_history
    ADD CONSTRAINT extraction_history_pkey PRIMARY KEY (id);


--
-- Name: labels labels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.labels
    ADD CONSTRAINT labels_pkey PRIMARY KEY (data_id);


--
-- Name: masters masters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.masters
    ADD CONSTRAINT masters_pkey PRIMARY KEY (data_id);


--
-- Name: oauth_tokens oauth_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oauth_tokens
    ADD CONSTRAINT oauth_tokens_pkey PRIMARY KEY (id);


--
-- Name: oauth_tokens oauth_tokens_user_id_provider_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oauth_tokens
    ADD CONSTRAINT oauth_tokens_user_id_provider_key UNIQUE (user_id, provider);


--
-- Name: observations observations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.observations
    ADD CONSTRAINT observations_pkey PRIMARY KEY (id);


--
-- Name: owned_copies owned_copies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.owned_copies
    ADD CONSTRAINT owned_copies_pkey PRIMARY KEY (id);


--
-- Name: provider_aliases provider_aliases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.provider_aliases
    ADD CONSTRAINT provider_aliases_pkey PRIMARY KEY (id);


--
-- Name: queue_metrics queue_metrics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.queue_metrics
    ADD CONSTRAINT queue_metrics_pkey PRIMARY KEY (id);


--
-- Name: releases releases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.releases
    ADD CONSTRAINT releases_pkey PRIMARY KEY (data_id);


--
-- Name: service_health_metrics service_health_metrics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_health_metrics
    ADD CONSTRAINT service_health_metrics_pkey PRIMARY KEY (id);


--
-- Name: sync_history sync_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_history
    ADD CONSTRAINT sync_history_pkey PRIMARY KEY (id);


--
-- Name: user_collections user_collections_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_collections
    ADD CONSTRAINT user_collections_pkey PRIMARY KEY (id);


--
-- Name: user_collections user_collections_user_id_release_id_instance_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_collections
    ADD CONSTRAINT user_collections_user_id_release_id_instance_id_key UNIQUE NULLS NOT DISTINCT (user_id, release_id, instance_id);


--
-- Name: user_wantlists user_wantlists_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_wantlists
    ADD CONSTRAINT user_wantlists_pkey PRIMARY KEY (id);


--
-- Name: user_wantlists user_wantlists_user_id_release_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_wantlists
    ADD CONSTRAINT user_wantlists_user_id_release_id_key UNIQUE (user_id, release_id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: idx_activity_events_type_occurred_at; Type: INDEX; Schema: activity; Owner: -
--

CREATE INDEX idx_activity_events_type_occurred_at ON ONLY activity.events USING btree (event_type, occurred_at DESC);


--
-- Name: events_default_event_type_occurred_at_idx; Type: INDEX; Schema: activity; Owner: -
--

CREATE INDEX events_default_event_type_occurred_at_idx ON activity.events_default USING btree (event_type, occurred_at DESC);


--
-- Name: idx_activity_events_subject_occurred_at; Type: INDEX; Schema: activity; Owner: -
--

CREATE INDEX idx_activity_events_subject_occurred_at ON ONLY activity.events USING btree (subject_id, occurred_at DESC);


--
-- Name: events_default_subject_id_occurred_at_idx; Type: INDEX; Schema: activity; Owner: -
--

CREATE INDEX events_default_subject_id_occurred_at_idx ON activity.events_default USING btree (subject_id, occurred_at DESC);


--
-- Name: idx_activity_consent_grants_user_purpose; Type: INDEX; Schema: activity; Owner: -
--

CREATE INDEX idx_activity_consent_grants_user_purpose ON activity.consent_grants USING btree (user_id, purpose);


--
-- Name: idx_activity_erasures_subject_id; Type: INDEX; Schema: activity; Owner: -
--

CREATE INDEX idx_activity_erasures_subject_id ON activity.erasures USING btree (subject_id);


--
-- Name: idx_activity_impressions_candidate_set_id; Type: INDEX; Schema: activity; Owner: -
--

CREATE INDEX idx_activity_impressions_candidate_set_id ON ONLY activity.impressions USING btree (candidate_set_id);


--
-- Name: idx_activity_impressions_subject_occurred_at; Type: INDEX; Schema: activity; Owner: -
--

CREATE INDEX idx_activity_impressions_subject_occurred_at ON ONLY activity.impressions USING btree (subject_id, occurred_at DESC);


--
-- Name: impressions_default_candidate_set_id_idx; Type: INDEX; Schema: activity; Owner: -
--

CREATE INDEX impressions_default_candidate_set_id_idx ON activity.impressions_default USING btree (candidate_set_id);


--
-- Name: impressions_default_subject_id_occurred_at_idx; Type: INDEX; Schema: activity; Owner: -
--

CREATE INDEX impressions_default_subject_id_occurred_at_idx ON activity.impressions_default USING btree (subject_id, occurred_at DESC);


--
-- Name: idx_activity_summary_date; Type: INDEX; Schema: insights; Owner: -
--

CREATE INDEX idx_activity_summary_date ON insights.activity_summary USING btree (summary_date DESC);


--
-- Name: idx_anniversaries_month_year; Type: INDEX; Schema: insights; Owner: -
--

CREATE INDEX idx_anniversaries_month_year ON insights.monthly_anniversaries USING btree (computed_year, computed_month);


--
-- Name: idx_community_counts_fetched; Type: INDEX; Schema: insights; Owner: -
--

CREATE INDEX idx_community_counts_fetched ON insights.community_counts USING btree (fetched_at);


--
-- Name: idx_computation_log_type_started; Type: INDEX; Schema: insights; Owner: -
--

CREATE INDEX idx_computation_log_type_started ON insights.computation_log USING btree (insight_type, started_at DESC);


--
-- Name: idx_genre_trends_genre; Type: INDEX; Schema: insights; Owner: -
--

CREATE INDEX idx_genre_trends_genre ON insights.genre_trends USING btree (genre);


--
-- Name: idx_release_rarity_gem; Type: INDEX; Schema: insights; Owner: -
--

CREATE INDEX idx_release_rarity_gem ON insights.release_rarity USING btree (hidden_gem_score DESC NULLS LAST);


--
-- Name: idx_release_rarity_score; Type: INDEX; Schema: insights; Owner: -
--

CREATE INDEX idx_release_rarity_score ON insights.release_rarity USING btree (rarity_score DESC);


--
-- Name: idx_release_rarity_tier; Type: INDEX; Schema: insights; Owner: -
--

CREATE INDEX idx_release_rarity_tier ON insights.release_rarity USING btree (tier);


--
-- Name: idx_mb_artists_discogs_id; Type: INDEX; Schema: musicbrainz; Owner: -
--

CREATE INDEX idx_mb_artists_discogs_id ON musicbrainz.artists USING btree (discogs_artist_id) WHERE (discogs_artist_id IS NOT NULL);


--
-- Name: idx_mb_artists_gm_item_id; Type: INDEX; Schema: musicbrainz; Owner: -
--

CREATE INDEX idx_mb_artists_gm_item_id ON musicbrainz.artists USING btree (gm_item_id);


--
-- Name: idx_mb_artists_name; Type: INDEX; Schema: musicbrainz; Owner: -
--

CREATE INDEX idx_mb_artists_name ON musicbrainz.artists USING btree (name);


--
-- Name: idx_mb_labels_discogs_id; Type: INDEX; Schema: musicbrainz; Owner: -
--

CREATE INDEX idx_mb_labels_discogs_id ON musicbrainz.labels USING btree (discogs_label_id) WHERE (discogs_label_id IS NOT NULL);


--
-- Name: idx_mb_labels_gm_item_id; Type: INDEX; Schema: musicbrainz; Owner: -
--

CREATE INDEX idx_mb_labels_gm_item_id ON musicbrainz.labels USING btree (gm_item_id);


--
-- Name: idx_mb_labels_name; Type: INDEX; Schema: musicbrainz; Owner: -
--

CREATE INDEX idx_mb_labels_name ON musicbrainz.labels USING btree (name);


--
-- Name: idx_mb_links_mbid; Type: INDEX; Schema: musicbrainz; Owner: -
--

CREATE INDEX idx_mb_links_mbid ON musicbrainz.external_links USING btree (mbid);


--
-- Name: idx_mb_links_service; Type: INDEX; Schema: musicbrainz; Owner: -
--

CREATE INDEX idx_mb_links_service ON musicbrainz.external_links USING btree (service_name);


--
-- Name: idx_mb_release_groups_discogs_id; Type: INDEX; Schema: musicbrainz; Owner: -
--

CREATE INDEX idx_mb_release_groups_discogs_id ON musicbrainz.release_groups USING btree (discogs_master_id) WHERE (discogs_master_id IS NOT NULL);


--
-- Name: idx_mb_release_groups_gm_item_id; Type: INDEX; Schema: musicbrainz; Owner: -
--

CREATE INDEX idx_mb_release_groups_gm_item_id ON musicbrainz.release_groups USING btree (gm_item_id);


--
-- Name: idx_mb_releases_discogs_id; Type: INDEX; Schema: musicbrainz; Owner: -
--

CREATE INDEX idx_mb_releases_discogs_id ON musicbrainz.releases USING btree (discogs_release_id) WHERE (discogs_release_id IS NOT NULL);


--
-- Name: idx_mb_releases_gm_item_id; Type: INDEX; Schema: musicbrainz; Owner: -
--

CREATE INDEX idx_mb_releases_gm_item_id ON musicbrainz.releases USING btree (gm_item_id);


--
-- Name: idx_mb_releases_media_families; Type: INDEX; Schema: musicbrainz; Owner: -
--

CREATE INDEX idx_mb_releases_media_families ON musicbrainz.releases USING gin (((media -> 'families'::text)));


--
-- Name: idx_mb_rels_source; Type: INDEX; Schema: musicbrainz; Owner: -
--

CREATE INDEX idx_mb_rels_source ON musicbrainz.relationships USING btree (source_mbid);


--
-- Name: idx_mb_rels_target; Type: INDEX; Schema: musicbrainz; Owner: -
--

CREATE INDEX idx_mb_rels_target ON musicbrainz.relationships USING btree (target_mbid);


--
-- Name: idx_mb_rels_type; Type: INDEX; Schema: musicbrainz; Owner: -
--

CREATE INDEX idx_mb_rels_type ON musicbrainz.relationships USING btree (relationship_type);


--
-- Name: idx_admin_audit_log_admin_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_admin_audit_log_admin_id ON public.admin_audit_log USING btree (admin_id);


--
-- Name: idx_admin_audit_log_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_admin_audit_log_created_at ON public.admin_audit_log USING btree (created_at DESC);


--
-- Name: idx_app_tokens_token_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_app_tokens_token_lookup ON public.app_tokens USING btree (token_hash) WHERE (revoked_at IS NULL);


--
-- Name: idx_app_tokens_user_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_app_tokens_user_active ON public.app_tokens USING btree (user_id) WHERE (revoked_at IS NULL);


--
-- Name: idx_artists_fts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_artists_fts ON public.artists USING gin (to_tsvector('english'::regconfig, COALESCE((data ->> 'name'::text), ''::text)));


--
-- Name: idx_artists_gm_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_artists_gm_item_id ON public.artists USING btree (gm_item_id);


--
-- Name: idx_artists_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_artists_hash ON public.artists USING btree (hash);


--
-- Name: idx_artists_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_artists_name ON public.artists USING btree (((data ->> 'name'::text)));


--
-- Name: idx_artists_updated_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_artists_updated_at ON public.artists USING btree (updated_at);


--
-- Name: idx_collection_snapshots_user_taken_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_collection_snapshots_user_taken_at ON public.collection_snapshots USING btree (user_id, taken_at DESC);


--
-- Name: idx_extraction_history_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_extraction_history_created_at ON public.extraction_history USING btree (created_at DESC);


--
-- Name: idx_extraction_history_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_extraction_history_status ON public.extraction_history USING btree (status);


--
-- Name: idx_labels_fts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_labels_fts ON public.labels USING gin (to_tsvector('english'::regconfig, COALESCE((data ->> 'name'::text), ''::text)));


--
-- Name: idx_labels_gm_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_labels_gm_item_id ON public.labels USING btree (gm_item_id);


--
-- Name: idx_labels_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_labels_hash ON public.labels USING btree (hash);


--
-- Name: idx_labels_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_labels_name ON public.labels USING btree (((data ->> 'name'::text)));


--
-- Name: idx_labels_updated_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_labels_updated_at ON public.labels USING btree (updated_at);


--
-- Name: idx_masters_fts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_masters_fts ON public.masters USING gin (to_tsvector('english'::regconfig, COALESCE((data ->> 'title'::text), ''::text)));


--
-- Name: idx_masters_gm_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_masters_gm_item_id ON public.masters USING btree (gm_item_id);


--
-- Name: idx_masters_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_masters_hash ON public.masters USING btree (hash);


--
-- Name: idx_masters_title; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_masters_title ON public.masters USING btree (((data ->> 'title'::text)));


--
-- Name: idx_masters_updated_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_masters_updated_at ON public.masters USING btree (updated_at);


--
-- Name: idx_masters_year; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_masters_year ON public.masters USING btree (((data ->> 'year'::text)));


--
-- Name: idx_observations_artifact_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_observations_artifact_kind ON public.observations USING btree (artifact_id, kind);


--
-- Name: idx_observations_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_observations_user_id ON public.observations USING btree (user_id);


--
-- Name: idx_owned_copies_collection_row_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_owned_copies_collection_row_id ON public.owned_copies USING btree (collection_row_id) WHERE (collection_row_id IS NOT NULL);


--
-- Name: idx_owned_copies_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_owned_copies_user_id ON public.owned_copies USING btree (user_id);


--
-- Name: idx_provider_aliases_native_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_provider_aliases_native_id ON public.provider_aliases USING btree (native_id);


--
-- Name: idx_provider_aliases_provider_entity_kind_external_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_provider_aliases_provider_entity_kind_external_id ON public.provider_aliases USING btree (provider, entity_kind, external_id) WHERE (valid_to IS NULL);


--
-- Name: idx_queue_metrics_recorded_queue; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_queue_metrics_recorded_queue ON public.queue_metrics USING btree (recorded_at, queue_name);


--
-- Name: idx_releases_companies; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_releases_companies ON public.releases USING gin (((data -> 'companies'::text)));


--
-- Name: idx_releases_country; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_releases_country ON public.releases USING btree (((data ->> 'country'::text)));


--
-- Name: idx_releases_fts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_releases_fts ON public.releases USING gin (to_tsvector('english'::regconfig, COALESCE((data ->> 'title'::text), ''::text)));


--
-- Name: idx_releases_genres; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_releases_genres ON public.releases USING gin (((data -> 'genres'::text)));


--
-- Name: idx_releases_gm_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_releases_gm_item_id ON public.releases USING btree (gm_item_id);


--
-- Name: idx_releases_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_releases_hash ON public.releases USING btree (hash);


--
-- Name: idx_releases_identifiers; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_releases_identifiers ON public.releases USING gin (((data -> 'identifiers'::text)));


--
-- Name: idx_releases_labels; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_releases_labels ON public.releases USING gin (((data -> 'labels'::text)));


--
-- Name: idx_releases_media_families; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_releases_media_families ON public.releases USING gin (((media -> 'families'::text)));


--
-- Name: idx_releases_title; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_releases_title ON public.releases USING btree (((data ->> 'title'::text)));


--
-- Name: idx_releases_updated_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_releases_updated_at ON public.releases USING btree (updated_at);


--
-- Name: idx_releases_year; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_releases_year ON public.releases USING btree (((data ->> 'year'::text)));


--
-- Name: idx_service_health_recorded_service; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_service_health_recorded_service ON public.service_health_metrics USING btree (recorded_at, service_name);


--
-- Name: idx_sync_history_running; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sync_history_running ON public.sync_history USING btree (user_id) WHERE ((status)::text = 'running'::text);


--
-- Name: idx_sync_history_user_started; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sync_history_user_started ON public.sync_history USING btree (user_id, started_at DESC);


--
-- Name: idx_user_collections_gm_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_collections_gm_item_id ON public.user_collections USING btree (gm_item_id);


--
-- Name: idx_user_collections_media_families; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_collections_media_families ON public.user_collections USING gin (((media -> 'families'::text)));


--
-- Name: idx_user_collections_owned_copy_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_collections_owned_copy_id ON public.user_collections USING btree (owned_copy_id);


--
-- Name: idx_user_collections_release_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_collections_release_id ON public.user_collections USING btree (release_id);


--
-- Name: idx_user_collections_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_collections_user_id ON public.user_collections USING btree (user_id);


--
-- Name: idx_user_wantlists_gm_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_wantlists_gm_item_id ON public.user_wantlists USING btree (gm_item_id);


--
-- Name: idx_user_wantlists_release_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_wantlists_release_id ON public.user_wantlists USING btree (release_id);


--
-- Name: idx_user_wantlists_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_wantlists_user_id ON public.user_wantlists USING btree (user_id);


--
-- Name: events_default_event_type_occurred_at_idx; Type: INDEX ATTACH; Schema: activity; Owner: -
--

ALTER INDEX activity.idx_activity_events_type_occurred_at ATTACH PARTITION activity.events_default_event_type_occurred_at_idx;


--
-- Name: events_default_occurred_at_idempotency_key_key; Type: INDEX ATTACH; Schema: activity; Owner: -
--

ALTER INDEX activity.events_occurred_at_idempotency_key_key ATTACH PARTITION activity.events_default_occurred_at_idempotency_key_key;


--
-- Name: events_default_pkey; Type: INDEX ATTACH; Schema: activity; Owner: -
--

ALTER INDEX activity.events_pkey ATTACH PARTITION activity.events_default_pkey;


--
-- Name: events_default_subject_id_occurred_at_idx; Type: INDEX ATTACH; Schema: activity; Owner: -
--

ALTER INDEX activity.idx_activity_events_subject_occurred_at ATTACH PARTITION activity.events_default_subject_id_occurred_at_idx;


--
-- Name: impressions_default_candidate_set_id_idx; Type: INDEX ATTACH; Schema: activity; Owner: -
--

ALTER INDEX activity.idx_activity_impressions_candidate_set_id ATTACH PARTITION activity.impressions_default_candidate_set_id_idx;


--
-- Name: impressions_default_pkey; Type: INDEX ATTACH; Schema: activity; Owner: -
--

ALTER INDEX activity.impressions_pkey ATTACH PARTITION activity.impressions_default_pkey;


--
-- Name: impressions_default_subject_id_occurred_at_idx; Type: INDEX ATTACH; Schema: activity; Owner: -
--

ALTER INDEX activity.idx_activity_impressions_subject_occurred_at ATTACH PARTITION activity.impressions_default_subject_id_occurred_at_idx;


--
-- Name: events activity_events_reject_mutation; Type: TRIGGER; Schema: activity; Owner: -
--

CREATE TRIGGER activity_events_reject_mutation BEFORE DELETE OR UPDATE ON activity.events FOR EACH ROW EXECUTE FUNCTION activity.reject_mutation();


--
-- Name: impressions activity_impressions_reject_mutation; Type: TRIGGER; Schema: activity; Owner: -
--

CREATE TRIGGER activity_impressions_reject_mutation BEFORE DELETE OR UPDATE ON activity.impressions FOR EACH ROW EXECUTE FUNCTION activity.reject_mutation();


--
-- Name: consent_grants consent_grants_user_id_fkey; Type: FK CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.consent_grants
    ADD CONSTRAINT consent_grants_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_subjects user_subjects_user_id_fkey; Type: FK CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.user_subjects
    ADD CONSTRAINT user_subjects_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: admin_audit_log admin_audit_log_admin_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_audit_log
    ADD CONSTRAINT admin_audit_log_admin_id_fkey FOREIGN KEY (admin_id) REFERENCES public.users(id);


--
-- Name: app_tokens app_tokens_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_tokens
    ADD CONSTRAINT app_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: artifacts artifacts_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifacts
    ADD CONSTRAINT artifacts_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: artifacts artifacts_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifacts
    ADD CONSTRAINT artifacts_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.catalog_items(id);


--
-- Name: collection_snapshots collection_snapshots_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_snapshots
    ADD CONSTRAINT collection_snapshots_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: extraction_history extraction_history_triggered_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.extraction_history
    ADD CONSTRAINT extraction_history_triggered_by_fkey FOREIGN KEY (triggered_by) REFERENCES public.users(id);


--
-- Name: oauth_tokens oauth_tokens_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oauth_tokens
    ADD CONSTRAINT oauth_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: observations observations_artifact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.observations
    ADD CONSTRAINT observations_artifact_id_fkey FOREIGN KEY (artifact_id) REFERENCES public.artifacts(id);


--
-- Name: observations observations_owned_copy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.observations
    ADD CONSTRAINT observations_owned_copy_id_fkey FOREIGN KEY (owned_copy_id) REFERENCES public.owned_copies(id) ON DELETE CASCADE;


--
-- Name: observations observations_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.observations
    ADD CONSTRAINT observations_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: owned_copies owned_copies_artifact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.owned_copies
    ADD CONSTRAINT owned_copies_artifact_id_fkey FOREIGN KEY (artifact_id) REFERENCES public.artifacts(id);


--
-- Name: owned_copies owned_copies_collection_row_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.owned_copies
    ADD CONSTRAINT owned_copies_collection_row_id_fkey FOREIGN KEY (collection_row_id) REFERENCES public.user_collections(id) ON DELETE SET NULL;


--
-- Name: owned_copies owned_copies_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.owned_copies
    ADD CONSTRAINT owned_copies_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.catalog_items(id);


--
-- Name: owned_copies owned_copies_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.owned_copies
    ADD CONSTRAINT owned_copies_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: sync_history sync_history_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_history
    ADD CONSTRAINT sync_history_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_collections user_collections_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_collections
    ADD CONSTRAINT user_collections_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_wantlists user_wantlists_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_wantlists
    ADD CONSTRAINT user_wantlists_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict UI55ijiWSnDroKMcs7JYrBLkFbaTDw2rbCW1LiGbSvoybdL1y6rmklMlhZTSSHs

