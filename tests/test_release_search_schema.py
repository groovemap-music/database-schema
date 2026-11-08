"""The stored release title vector (gm-database-schema-fi4a) and the search facet and filter
attributes beside it (gm-database-schema-1sle), checked against the statement lists.

No database: the integration suite proves the vectors on a real PostgreSQL.
"""

import re

from groovemap_schema.postgres import (
    _RELEASE_SEARCH,
    _RELEASE_SEARCH_STATEMENTS,
    _RELEASE_SEARCH_TRIGGERS,
    _SPECIFIC_INDEXES,
    RELEASE_SEARCH_ATTRIBUTES,
    RELEASE_SEARCH_BACKFILL_LOCK_TIMEOUT,
    RELEASE_SEARCH_INDEX,
    RELEASE_SEARCH_ROLLBACK_STATEMENTS,
    RELEASE_SEARCH_TABLE,
    RELEASE_TITLE_TSVECTOR,
    _noop_probe,
    _release_title_tsvector,
    _schema_statements,
)


_STATEMENTS = dict(_RELEASE_SEARCH_STATEMENTS)
_TRIGGERS = _STATEMENTS["releases release_search capture triggers"]
_BACKFILL = _STATEMENTS["release_search_backfill procedure"]
_BACKFILL_STEPS = {
    name: _STATEMENTS[f"{name} function"]
    for name in ("release_search_backfill_pages", "release_search_backfill_find_missing", "release_search_backfill_add_missing")
}
_WRITING_STEPS = (_BACKFILL_STEPS["release_search_backfill_pages"], _BACKFILL_STEPS["release_search_backfill_add_missing"])
_CAPTURE_FUNCTIONS = [name.removesuffix(" function") for name in _STATEMENTS if name.startswith("release_search_capture_")]
_MEDIA_CAPTURE = _STATEMENTS["release_search_capture_media_updated function"]
_ATTRIBUTES = {name: (declaration, expression) for name, declaration, expression in RELEASE_SEARCH_ATTRIBUTES}
_CARRIED = ("title_tsv", *_ATTRIBUTES)


def _trigger(operation: str) -> str:
    match = re.search(rf"CREATE TRIGGER release_search_capture_{operation} .*?;", _TRIGGERS, re.DOTALL)
    assert match, operation
    return match.group(0)


class TestTheVector:
    def test_it_is_the_expression_search_ranks_by_today(self) -> None:
        """catalog-api ranks by idx_releases_fts's expression; the stored vector must equal it."""
        fts = dict(_SPECIFIC_INDEXES)["idx_releases_fts"]
        assert RELEASE_TITLE_TSVECTOR == "to_tsvector('english', COALESCE(data->>'title', ''))"
        assert f"USING GIN ({RELEASE_TITLE_TSVECTOR})" in fts

    def test_every_writer_computes_that_expression(self) -> None:
        assert _release_title_tsvector("n") == RELEASE_TITLE_TSVECTOR.replace("data->>", "n.data->>")
        for name, alias in (
            ("release_search_capture_inserted function", "n"),
            ("release_search_capture_updated function", "n"),
            ("release_search_backfill_pages function", "r"),
            ("release_search_backfill_add_missing function", "r"),
            ("release_search_drift function", "r"),
        ):
            assert _release_title_tsvector(alias) in _STATEMENTS[name], name

    def test_the_table_is_one_narrow_row_per_release_keyed_like_releases(self) -> None:
        table = _STATEMENTS["release_search table"]
        assert table.startswith(f"CREATE TABLE IF NOT EXISTS {RELEASE_SEARCH_TABLE} (")
        assert re.search(r"release_id\s+VARCHAR\s+PRIMARY KEY", table)
        assert re.search(r"title_tsv\s+TSVECTOR NOT NULL", table)
        for name, (declaration, _expression) in _ATTRIBUTES.items():
            assert re.search(rf"\b{name}\s+{declaration},?\n", table), name
        assert "REFERENCES" not in table  # a foreign key would make `TRUNCATE releases` fail

    def test_the_index_is_a_gin_over_the_stored_vector_and_skippable_on_reapply(self) -> None:
        index = _STATEMENTS[RELEASE_SEARCH_INDEX]
        assert index == f"CREATE INDEX IF NOT EXISTS {RELEASE_SEARCH_INDEX} ON {RELEASE_SEARCH_TABLE} USING GIN (title_tsv)"
        assert _noop_probe(index) is not None


class TestTheTriggers:
    def test_inserts_and_deletes_are_captured_once_per_statement(self) -> None:
        assert "AFTER INSERT ON public.releases" in _trigger("insert")
        assert "REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT" in _trigger("insert")
        assert "AFTER DELETE ON public.releases" in _trigger("delete")
        assert "REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT" in _trigger("delete")
        assert "AFTER TRUNCATE ON public.releases" in _trigger("truncate")

    def test_an_update_is_captured_only_when_it_sets_the_id_or_the_document(self) -> None:
        """The loader's updated_at refresh and media/identity backfills must not detoast every row."""
        update = _trigger("update")
        assert "AFTER UPDATE OF data_id, data ON public.releases" in update
        assert "FOR EACH ROW" in update

    def test_the_five_triggers_are_created_together_and_skipped_when_present(self) -> None:
        """A database with v0.11.0's four has a different count, so the first reapply recreates all five."""
        assert [
            re.search(r"CREATE TRIGGER release_search_capture_(\w+)", line).group(1) for line in _TRIGGERS.splitlines() if "CREATE TRIGGER" in line
        ] == [*_RELEASE_SEARCH_TRIGGERS, "update_media"]
        assert _RELEASE_SEARCH.triggers == (*_RELEASE_SEARCH_TRIGGERS, "update_media")
        assert ") = 5 THEN\n        RETURN;" in _TRIGGERS
        assert "'release_search_capture_update_media'" in _TRIGGERS.split("THEN", 1)[0]
        assert _TRIGGERS.index("RETURN;") < _TRIGGERS.index("DROP TRIGGER")

    def test_capture_functions_run_as_their_owner_with_a_pinned_search_path(self) -> None:
        assert sorted(_CAPTURE_FUNCTIONS) == sorted(
            f"release_search_capture_{event}" for event in ("inserted", "updated", "deleted", "truncated", "media_updated")
        )
        for name in _CAPTURE_FUNCTIONS:
            statement = _STATEMENTS[f"{name} function"]
            assert "SECURITY DEFINER" in statement, name
            assert "SET search_path = pg_catalog, pg_temp" in statement, name

    def test_an_upsert_rewrites_a_row_only_when_one_of_its_values_changed(self) -> None:
        assignments = ", ".join(f"{name} = EXCLUDED.{name}" for name in _CARRIED)
        stored = ", ".join(f"release_search.{name}" for name in _CARRIED)
        excluded = ", ".join(f"EXCLUDED.{name}" for name in _CARRIED)
        for name in ("release_search_capture_inserted function", "release_search_capture_updated function", "release_search_backfill_pages function"):
            assert f"ON CONFLICT (release_id) DO UPDATE SET {assignments} WHERE ({stored}) IS DISTINCT FROM ({excluded})" in _STATEMENTS[name], name

    def test_each_document_is_read_once_per_row(self) -> None:
        """Every `->` over a toasted `data` detoasts it again: four keys would read it four times."""
        for name, rows in (
            ("release_search_capture_inserted function", "FROM (SELECT data_id, data #> '{}' AS data, media FROM new_rows OFFSET 0) AS n"),
            (
                "release_search_capture_updated function",
                "FROM (SELECT NEW.data_id AS data_id, NEW.data #> '{}' AS data, NEW.media AS media OFFSET 0) AS n",
            ),
            ("release_search_backfill_pages function", "FROM (SELECT data_id, data #> '{}' AS data, media FROM public.releases WHERE ctid >= "),
            (
                "release_search_backfill_add_missing function",
                "FROM (SELECT data_id, data #> '{}' AS data, media FROM public.releases WHERE data_id IN (SELECT data_id FROM batch) OFFSET 0) AS r",
            ),
            ("release_search_drift function", "FROM (SELECT data_id, data #> '{}' AS data, media FROM public.releases OFFSET 0) AS r JOIN"),
        ):
            assert rows in _STATEMENTS[name], name

    def test_a_renamed_release_drops_its_old_vector(self) -> None:
        assert "DELETE FROM public.release_search WHERE release_id = OLD.data_id" in _STATEMENTS["release_search_capture_updated function"]


class TestTheBackfill:
    def test_it_can_commit_between_batches(self) -> None:
        """A procedure that COMMITs may be neither SECURITY DEFINER nor carry a SET clause."""
        header = _BACKFILL.split("AS $release_search_backfill$", 1)[0]
        assert "CREATE OR REPLACE PROCEDURE public.release_search_backfill(batch_pages integer DEFAULT 1000, first_page bigint DEFAULT 0)" in header
        assert "SECURITY DEFINER" not in header
        assert "\nSET " not in header
        assert _BACKFILL.count("COMMIT;") == 3
        assert _BACKFILL.rstrip().endswith("REVOKE ALL ON PROCEDURE public.release_search_backfill(integer, bigint) FROM PUBLIC")

    def test_the_procedure_runs_no_sql_of_its_own_beyond_its_pinned_steps(self) -> None:
        """Its search_path is the caller's, so a schema there could substitute `to_tsvector` or `->>`."""
        body = _BACKFILL.split("AS $release_search_backfill$", 1)[1]
        for fragment in ("to_tsvector", "->>", "INSERT", "DELETE", "SELECT", "CREATE", "DROP", "format("):
            assert fragment not in body, fragment
        for name, step in _BACKFILL_STEPS.items():
            assert f"public.{name}(" in body, name
            assert "SET search_path = pg_catalog, pg_temp" in step, name
            assert "SECURITY DEFINER" not in step, name
            assert re.search(rf"REVOKE ALL ON FUNCTION public\.{name}\([a-z, ]*\) FROM PUBLIC\s*$", step), name

    def test_every_batch_takes_the_lock_before_it_writes(self) -> None:
        for step in _WRITING_STEPS:
            assert step.index("PERFORM public.release_search_backfill_lock();") < step.index("INSERT INTO public.release_search")
            assert step.count("ON CONFLICT (release_id) DO ") == 1

    def test_the_heap_pass_fills_columns_added_to_a_populated_table(self) -> None:
        """v0.11.0 filled title_tsv alone; a DO NOTHING pass would leave every new column NULL."""
        assert "ON CONFLICT (release_id) DO NOTHING" not in _BACKFILL_STEPS["release_search_backfill_pages"]
        assert "ON CONFLICT (release_id) DO NOTHING" in _BACKFILL_STEPS["release_search_backfill_add_missing"]

    def test_it_reads_in_heap_order_then_sweeps_what_moved(self) -> None:
        assert (
            "WHERE ctid >= format('(%s,0)', first_page)::tid AND ctid < format('(%s,0)', first_page + page_count)::tid"
            in (_BACKFILL_STEPS["release_search_backfill_pages"])
        )
        assert (
            "NOT EXISTS (SELECT 1 FROM public.release_search AS s WHERE s.release_id = r.data_id)"
            in (_BACKFILL_STEPS["release_search_backfill_find_missing"])
        )

    def test_the_lock_never_blocks_a_reader_and_waits_in_bounded_tries(self) -> None:
        lock = _STATEMENTS["release_search_backfill_lock function"]
        assert "LOCK TABLE public.release_search IN SHARE MODE;" in lock
        assert f"SET lock_timeout = '{RELEASE_SEARCH_BACKFILL_LOCK_TIMEOUT}'" in lock
        assert "EXCEPTION WHEN lock_not_available THEN" in lock


class TestPlacementAndRollback:
    def test_they_follow_the_releases_table_and_its_indexes(self) -> None:
        names = [name for name, _statement in _schema_statements()]
        first = names.index("release_search table")
        assert first > names.index("releases table")
        assert first > names.index("idx_releases_fts")
        assert names[first : first + len(_RELEASE_SEARCH_STATEMENTS)] == list(_STATEMENTS)

    def test_functions_exist_before_the_triggers_and_the_table_before_its_readers(self) -> None:
        names = list(_STATEMENTS)
        assert names[-1] == "releases release_search capture triggers"
        assert names.index("release_search table") < names.index("release_search_drift function")
        assert names.index("release_search_backfill_lock function") < names.index("release_search_backfill procedure")

    def test_the_rollback_removes_every_object_the_statements_create(self) -> None:
        script = "\n".join(RELEASE_SEARCH_ROLLBACK_STATEMENTS)
        created = re.findall(r"CREATE OR REPLACE (?:FUNCTION|PROCEDURE) (public\.\w+)\(", "\n".join(_STATEMENTS.values()))
        assert len(created) == 11
        for name in created:
            assert re.search(rf"DROP (?:FUNCTION|PROCEDURE) IF EXISTS {re.escape(name)}\(", script), name
        for operation in _RELEASE_SEARCH_TRIGGERS:
            assert f"DROP TRIGGER IF EXISTS release_search_capture_{operation} ON public.releases" in script
        assert RELEASE_SEARCH_ROLLBACK_STATEMENTS[-1] == f"DROP TABLE IF EXISTS {RELEASE_SEARCH_TABLE}"
        # Triggers go before the functions they call.
        assert script.index("DROP TRIGGER") < script.index("DROP FUNCTION")


class TestCaptureFunctionsCannotBeBorrowed:
    def test_each_refuses_any_table_but_releases_and_is_not_executable_by_public(self) -> None:
        for name in _CAPTURE_FUNCTIONS:
            statement = _STATEMENTS[f"{name} function"]
            assert "IF TG_TABLE_SCHEMA <> 'public' OR TG_TABLE_NAME <> 'releases' THEN" in statement, name
            assert "BEGIN\n    IF TG_TABLE_SCHEMA <> 'public'" in statement, name  # the guard runs before any write
            assert statement.rstrip().endswith(f"REVOKE ALL ON FUNCTION public.{name}() FROM PUBLIC"), name


class TestTheFacetAndFilterAttributes:
    """gm-database-schema-1sle: what catalog-api's facets and filtered branches read, verbatim."""

    def test_each_is_exactly_the_expression_catalog_api_evaluates_today(self) -> None:
        """Copied, not typed or cleaned: the clauses then mean the same thing for every row, odd values included."""
        assert {name: (declaration, expression("r")) for name, (declaration, expression) in _ATTRIBUTES.items()} == {
            "genres": ("JSONB", "r.data->'genres'"),
            "year": ("TEXT", "r.data->>'year'"),
            "country": ("TEXT", "r.data->>'country'"),
            "media_families": ("JSONB", "r.media->'families'"),
            "media_mediums": ("JSONB", "jsonb_path_query_array(r.media, '$.items[*].medium[*]')"),
        }

    def test_every_writer_and_the_drift_check_compute_them(self) -> None:
        for name, alias in (
            ("release_search_capture_inserted function", "n"),
            ("release_search_capture_updated function", "n"),
            ("release_search_backfill_pages function", "r"),
            ("release_search_backfill_add_missing function", "r"),
            ("release_search_drift function", "r"),
        ):
            for column, (_declaration, expression) in _ATTRIBUTES.items():
                assert expression(alias) in _STATEMENTS[name], (name, column)
        drift = _STATEMENTS["release_search_drift function"]
        assert f"WHERE ({', '.join(f's.{name}' for name in _CARRIED)}) IS DISTINCT FROM (" in drift

    def test_a_populated_table_gains_them_through_skippable_nullable_adds(self) -> None:
        """v0.11.0's table has title_tsv alone. Each add is skipped, taking no lock, once its column exists."""
        names = list(_STATEMENTS)
        for column, (declaration, _expression) in _ATTRIBUTES.items():
            statement = _STATEMENTS[f"release_search.{column} column"]
            assert statement == f"ALTER TABLE {RELEASE_SEARCH_TABLE} ADD COLUMN IF NOT EXISTS {column} {declaration}"
            assert "NOT NULL" not in declaration and "DEFAULT" not in declaration  # metadata-only, no rewrite
            assert _noop_probe(statement) is not None, column
            assert (
                names.index("release_search table")
                < names.index(f"release_search.{column} column")
                < names.index("release_search_capture_inserted function")
            )

    def test_a_media_only_update_rewrites_the_media_columns_without_reading_the_document(self) -> None:
        """discogs-sql-loader backfills `media` on unchanged rows; that must neither be missed nor detoast `data`."""
        trigger = _trigger("update_media")
        assert "AFTER UPDATE OF media ON public.releases" in trigger
        assert "FOR EACH ROW WHEN (OLD.media IS DISTINCT FROM NEW.media)" in trigger
        assert "EXECUTE FUNCTION public.release_search_capture_media_updated()" in trigger
        body = _MEDIA_CAPTURE.split("END IF;", 1)[1]
        assert "data" not in body.replace("data_id", "")
        assert "media_families = NEW.media->'families', media_mediums = jsonb_path_query_array(NEW.media, '$.items[*].medium[*]')" in body
        assert "WHERE s.release_id = NEW.data_id AND (s.media_families, s.media_mediums) IS DISTINCT FROM (" in body

    def test_it_fires_after_the_update_trigger(self) -> None:
        """Row triggers fire in name order: when one statement sets data and media, this one finds nothing to write."""
        assert sorted(["release_search_capture_update", "release_search_capture_update_media"])[0] == "release_search_capture_update"

    def test_the_update_trigger_still_watches_only_the_id_and_the_document(self) -> None:
        assert "AFTER UPDATE OF data_id, data ON public.releases" in _trigger("update")

    def test_the_rollback_drops_the_media_trigger_and_its_function(self) -> None:
        script = "\n".join(RELEASE_SEARCH_ROLLBACK_STATEMENTS)
        assert "DROP TRIGGER IF EXISTS release_search_capture_update_media ON public.releases" in script
        assert "DROP FUNCTION IF EXISTS public.release_search_capture_media_updated()" in script
