"""The reload seen lists (gm-database-schema-uw6d), checked against the statement lists.

No database: tests/integration/test_reload_seen_lists.py proves the lists, the purge set, the
shadow comparison and the crash reset on a real PostgreSQL. These tests pin the shape both
loaders depend on: which lists exist, their keys, that they are unlogged and index-free, that a
reapply never empties one, and that every purge refuses a list that is not intact.
"""

import re

from groovemap_schema.postgres import (
    _ENTITY_TABLES,
    _SEEN_LIST_STATEMENTS,
    SEEN_LIST_CLOCK_MARGIN,
    SEEN_LIST_HASH_KEY,
    SEEN_LIST_NOT_INTACT_SQLSTATE,
    SEEN_LIST_REGISTRY,
    SEEN_LIST_ROLLBACK_STATEMENTS,
    SEEN_LIST_SCHEMA,
    SEEN_LISTS,
    _noop_probe,
    _schema_statements,
)


_STATEMENTS = dict(_SEEN_LIST_STATEMENTS)


def _flat(statement: str) -> str:
    return " ".join(statement.split())


class TestTheLists:
    def test_one_list_per_reconciled_table_and_loader(self) -> None:
        assert [(seen.loader, seen.data_type, seen.entity) for seen in SEEN_LISTS] == [
            *(("discogs", table, f"public.{table}") for table in _ENTITY_TABLES),
            ("musicbrainz", "relationships", "musicbrainz.relationships"),
            ("musicbrainz", "external_links", "musicbrainz.external_links"),
        ]
        assert {seen.relation for seen in SEEN_LISTS} == {f"{SEEN_LIST_SCHEMA}.{seen.loader}_{seen.data_type}" for seen in SEEN_LISTS}

    def test_each_list_is_unlogged_narrow_and_without_index_or_key(self) -> None:
        for seen in SEEN_LISTS:
            table = _flat(_STATEMENTS[f"{seen.relation} table"])
            assert f"CREATE UNLOGGED TABLE {seen.relation} ( seen_at TIMESTAMPTZ NOT NULL DEFAULT now(), seen_key {seen.key_type} NOT NULL )" in table
            assert "PRIMARY KEY" not in table and "UNIQUE" not in table
            assert "autovacuum_vacuum_insert_threshold = -1" in table
        assert not any(statement.lstrip().upper().startswith("CREATE INDEX") for _name, statement in _SEEN_LIST_STATEMENTS)

    def test_a_list_is_created_and_started_once_and_never_emptied_by_a_reapply(self) -> None:
        for seen in SEEN_LISTS:
            table = _flat(_STATEMENTS[f"{seen.relation} table"])
            created = table.index(f"IF pg_catalog.to_regclass('{seen.relation}') IS NULL THEN")
            registered = table.index(f"INSERT INTO {SEEN_LIST_REGISTRY} (loader, data_type) VALUES ('{seen.loader}', '{seen.data_type}')")  # noqa: S608
            assert created < registered < table.index("END IF")
            assert "TRUNCATE" not in table

    def test_discogs_keys_are_the_data_id(self) -> None:
        for seen in SEEN_LISTS:
            if seen.loader == "discogs":
                assert (seen.key_type, seen.key, seen.row_id, seen.row_id_type) == ("TEXT", "e.data_id", "data_id", "text")

    def test_musicbrainz_keys_are_8_bytes_of_the_row_key_hash(self) -> None:
        keys = {seen.data_type: seen for seen in SEEN_LISTS if seen.loader == "musicbrainz"}
        assert keys["relationships"].key == (
            f"{SEEN_LIST_HASH_KEY}(musicbrainz.relationship_key(e.source_mbid, e.target_mbid, e.source_entity_type, "
            "e.target_entity_type, e.relationship_type, e.begin_date, e.end_date, e.attributes))"
        )
        assert keys["external_links"].key == f"{SEEN_LIST_HASH_KEY}(musicbrainz.external_link_key(e.mbid, e.entity_type, e.service_name, e.url))"
        for seen in keys.values():
            assert (seen.key_type, seen.row_id, seen.row_id_type) == ("BIGINT", "id", "bigint")


class TestTheRegistry:
    def test_the_registry_is_unlogged_so_a_crash_empties_it_with_the_lists(self) -> None:
        registry = _flat(_STATEMENTS[f"{SEEN_LIST_REGISTRY} table"])
        assert registry.startswith(f"CREATE UNLOGGED TABLE IF NOT EXISTS {SEEN_LIST_REGISTRY} (")
        assert "CONSTRAINT lists_pkey PRIMARY KEY (loader, data_type)" in registry

    def test_intact_reads_the_registry_with_the_clock_margin_and_defaults_to_false(self) -> None:
        intact = _flat(_STATEMENTS[f"{SEEN_LIST_SCHEMA}.intact function"])
        assert f"l.opened_at <= p_started_at - interval '{SEEN_LIST_CLOCK_MARGIN}'" in intact
        assert intact.endswith("false) $intact$")
        assert "STABLE" in intact

    def test_start_truncates_only_the_named_list_and_restamps_it(self) -> None:
        start = _STATEMENTS[f"{SEEN_LIST_SCHEMA}.start function"]
        for seen in SEEN_LISTS:
            assert re.search(rf"WHEN '{seen.loader}\.{seen.data_type}' THEN\s+TRUNCATE {re.escape(seen.relation)};", start)
        assert start.count("TRUNCATE") == len(SEEN_LISTS)
        assert "ERRCODE = '22023'" in start
        assert "ON CONFLICT (loader, data_type) DO UPDATE SET opened_at = EXCLUDED.opened_at" in start

    def test_status_counts_distinct_keys_since_the_extraction(self) -> None:
        status = _STATEMENTS[f"{SEEN_LIST_SCHEMA}.status function"]
        assert "RETURNS TABLE (opened_at timestamptz, intact boolean, seen_keys bigint)" in status
        for seen in SEEN_LISTS:
            assert f"(SELECT DISTINCT s.seen_key FROM {seen.relation} AS s WHERE s.seen_at >= p_started_at)" in status  # noqa: S608


class TestThePurge:
    def test_every_stale_function_refuses_a_list_that_is_not_intact_before_it_reads(self) -> None:
        for seen in SEEN_LISTS:
            stale = _STATEMENTS[f"{SEEN_LIST_SCHEMA}.{seen.name}_stale function"]
            refused = stale.index(f"IF NOT {SEEN_LIST_SCHEMA}.intact('{seen.loader}', '{seen.data_type}', p_started_at) THEN")
            assert f"ERRCODE = '{SEEN_LIST_NOT_INTACT_SQLSTATE}'" in stale
            assert refused < stale.index("RETURN QUERY")

    def test_the_stale_set_is_older_than_the_extraction_and_not_seen_since_it(self) -> None:
        for seen in SEEN_LISTS:
            stale = _flat(_STATEMENTS[f"{SEEN_LIST_SCHEMA}.{seen.name}_stale function"])
            assert f"RETURNS TABLE ({seen.row_id} {seen.row_id_type})" in stale
            unseen = f"SELECT e.{seen.row_id} FROM {seen.entity} AS e WHERE NOT EXISTS (SELECT 1 FROM {seen.relation} AS s "  # noqa: S608
            unseen += f"WHERE s.seen_key = {seen.key} AND s.seen_at >= p_started_at)"
            assert f"WHERE stale.{seen.row_id} IN ({unseen}) AND stale.updated_at < p_started_at;" in stale

    def test_the_shadow_compares_the_touch_purge_with_the_seen_purge(self) -> None:
        for seen in SEEN_LISTS:
            shadow = _flat(_STATEMENTS[f"{SEEN_LIST_SCHEMA}.{seen.name}_shadow function"])
            assert "count(*) FILTER (WHERE e.updated_at < p_started_at)," in shadow  # touch_purge
            assert "count(*) FILTER (WHERE d.seen_key IS NULL)," in shadow  # seen_purge, without the touch
            assert "count(*) FILTER (WHERE e.updated_at >= p_started_at AND d.seen_key IS NULL)," in shadow
            assert f"refreshed_not_seen_sample {seen.row_id_type}[], seen_not_refreshed_sample {seen.row_id_type}[]" in shadow
            assert "p_sample integer DEFAULT 20" in shadow

    def test_every_function_pins_its_search_path(self) -> None:
        for name, statement in _SEEN_LIST_STATEMENTS:
            if name.startswith(f"{SEEN_LIST_SCHEMA}.") and name.endswith(" function") and name != f"{SEEN_LIST_HASH_KEY} function":
                assert "SET search_path = pg_catalog, pg_temp" in statement, name


class TestTheKeys:
    def test_hash_key_takes_the_first_8_bytes(self) -> None:
        hash_key = _flat(_STATEMENTS[f"{SEEN_LIST_HASH_KEY} function"])
        assert "RETURNS bigint LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT" in hash_key
        assert "pg_catalog.substr(key, 1, 8), 'hex'))::bit(64)::bigint" in hash_key

    def test_the_external_link_key_length_prefixes_each_part(self) -> None:
        key = _flat(_STATEMENTS["musicbrainz.external_link_key function"])
        assert "musicbrainz.external_link_key(mbid uuid, entity_type text, service_name text, url text) RETURNS bytea" in key
        for part in ("mbid::text", "entity_type", "service_name", "url"):
            assert f"COALESCE(pg_catalog.length({part})::text || ':' || {part}, '-')" in key


class TestTheApply:
    def test_the_lists_follow_the_tables_and_key_functions_they_read(self) -> None:
        order = [name for name, _statement in _schema_statements()]
        assert order.index("musicbrainz.relationships table") < order.index(f"{SEEN_LIST_SCHEMA} schema")
        assert order.index(f"{SEEN_LIST_REGISTRY} table") < min(order.index(f"{seen.relation} table") for seen in SEEN_LISTS)
        assert order.index(f"{SEEN_LIST_HASH_KEY} function") < order.index(f"{SEEN_LIST_SCHEMA}.start function")
        assert len(order) == len(set(order))

    def test_the_bounded_apply_never_skips_a_seen_list_statement(self) -> None:
        assert all(_noop_probe(statement) is None for _name, statement in _SEEN_LIST_STATEMENTS)

    def test_the_rollback_drops_the_schema_and_the_link_key(self) -> None:
        assert (
            f"DROP SCHEMA IF EXISTS {SEEN_LIST_SCHEMA} CASCADE",
            "DROP FUNCTION IF EXISTS musicbrainz.external_link_key(uuid, text, text, text)",
        ) == SEEN_LIST_ROLLBACK_STATEMENTS
