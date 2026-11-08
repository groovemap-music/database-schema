"""The stored release title vector (gm-database-schema-fi4a), checked against the statement lists.

No database: the integration suite proves the vectors on a real PostgreSQL.
"""

import re

from groovemap_schema.postgres import (
    _RELEASE_SEARCH_STATEMENTS,
    _RELEASE_SEARCH_TRIGGERS,
    _SPECIFIC_INDEXES,
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
_CAPTURE_FUNCTIONS = [name.removesuffix(" function") for name in _STATEMENTS if name.startswith("release_search_capture_")]


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
            ("release_search_capture_updated function", "NEW"),
            ("release_search_backfill procedure", "r"),
            ("release_search_drift function", "r"),
        ):
            assert _release_title_tsvector(alias) in _STATEMENTS[name], name
        assert _BACKFILL.count(_release_title_tsvector("r")) == 2

    def test_the_table_is_one_narrow_row_per_release_keyed_like_releases(self) -> None:
        table = _STATEMENTS["release_search table"]
        assert table.startswith(f"CREATE TABLE IF NOT EXISTS {RELEASE_SEARCH_TABLE} (")
        assert re.search(r"release_id VARCHAR\s+PRIMARY KEY", table)
        assert re.search(r"title_tsv\s+TSVECTOR NOT NULL", table)
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

    def test_the_four_triggers_are_created_together_and_skipped_when_present(self) -> None:
        assert [
            re.search(r"CREATE TRIGGER release_search_capture_(\w+)", line).group(1) for line in _TRIGGERS.splitlines() if "CREATE TRIGGER" in line
        ] == list(_RELEASE_SEARCH_TRIGGERS)
        assert f") = {len(_RELEASE_SEARCH_TRIGGERS)} THEN\n        RETURN;" in _TRIGGERS
        assert _TRIGGERS.index("RETURN;") < _TRIGGERS.index("DROP TRIGGER")

    def test_capture_functions_run_as_their_owner_with_a_pinned_search_path(self) -> None:
        assert sorted(_CAPTURE_FUNCTIONS) == sorted(f"release_search_capture_{event}" for event in ("inserted", "updated", "deleted", "truncated"))
        for name in _CAPTURE_FUNCTIONS:
            statement = _STATEMENTS[f"{name} function"]
            assert "SECURITY DEFINER" in statement, name
            assert "SET search_path = pg_catalog, pg_temp" in statement, name

    def test_an_upsert_rewrites_a_vector_only_when_it_changed(self) -> None:
        for name in ("release_search_capture_inserted function", "release_search_capture_updated function"):
            assert "ON CONFLICT (release_id) DO UPDATE SET title_tsv = EXCLUDED.title_tsv" in _STATEMENTS[name]
            assert "WHERE release_search.title_tsv IS DISTINCT FROM EXCLUDED.title_tsv" in _STATEMENTS[name]

    def test_a_renamed_release_drops_its_old_vector(self) -> None:
        assert "DELETE FROM public.release_search WHERE release_id = OLD.data_id" in _STATEMENTS["release_search_capture_updated function"]


class TestTheBackfill:
    def test_it_can_commit_between_batches(self) -> None:
        """A procedure that COMMITs may be neither SECURITY DEFINER nor carry a SET clause."""
        header = _BACKFILL.split("AS $release_search_backfill$", 1)[0]
        assert "CREATE OR REPLACE PROCEDURE public.release_search_backfill(batch_pages integer DEFAULT 1000, first_page bigint DEFAULT 0)" in header
        assert "SECURITY DEFINER" not in header
        assert "\nSET " not in header
        assert _BACKFILL.count("COMMIT;") >= 3

    def test_every_batch_takes_the_lock_before_it_writes(self) -> None:
        inserts = [match.start() for match in re.finditer(r"INSERT INTO public\.release_search", _BACKFILL)]
        locks = [match.start() for match in re.finditer(r"PERFORM public\.release_search_backfill_lock\(\);", _BACKFILL)]
        assert len(inserts) == len(locks) == 2
        assert all(lock < insert for lock, insert in zip(locks, inserts, strict=True))

    def test_it_reads_in_heap_order_then_sweeps_what_moved(self) -> None:
        assert "r.ctid >= pg_catalog.format('(%s,0)', page)::tid" in _BACKFILL
        assert "NOT EXISTS (SELECT 1 FROM public.release_search AS s WHERE s.release_id = r.data_id)" in _BACKFILL
        assert _BACKFILL.count("ON CONFLICT (release_id) DO NOTHING") == 2

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
        assert len(created) == 7
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
