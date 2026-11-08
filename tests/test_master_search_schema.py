"""The stored master title vector and year (gm-database-schema-nu0c), checked against the statement lists.

No database: the integration suite proves the rows on a real PostgreSQL. The statements come
from the same generator as release_search's (tests/test_release_search_schema.py), so these
tests check what differs for masters and that nothing of the shared design was lost.
"""

import re

from groovemap_schema.postgres import (
    _MASTER_SEARCH_STATEMENTS,
    _SEARCH_TRIGGERS,
    _SPECIFIC_INDEXES,
    MASTER_SEARCH_INDEX,
    MASTER_SEARCH_ROLLBACK_STATEMENTS,
    MASTER_SEARCH_TABLE,
    MASTER_TITLE_TSVECTOR,
    MASTER_YEAR,
    SEARCH_BACKFILL_LOCK_TIMEOUT,
    _master_year,
    _noop_probe,
    _schema_statements,
    _title_tsvector,
)


_STATEMENTS = dict(_MASTER_SEARCH_STATEMENTS)
_TRIGGERS = _STATEMENTS["masters master_search capture triggers"]
_BACKFILL = _STATEMENTS["master_search_backfill procedure"]
_BACKFILL_STEPS = {
    name: _STATEMENTS[f"{name} function"]
    for name in ("master_search_backfill_pages", "master_search_backfill_find_missing", "master_search_backfill_add_missing")
}
_WRITING_STEPS = (_BACKFILL_STEPS["master_search_backfill_pages"], _BACKFILL_STEPS["master_search_backfill_add_missing"])
_CAPTURE_FUNCTIONS = [name.removesuffix(" function") for name in _STATEMENTS if name.startswith("master_search_capture_")]
_UPSERT = (
    "ON CONFLICT (master_id) DO UPDATE SET title_tsv = EXCLUDED.title_tsv, year = EXCLUDED.year "
    "WHERE (master_search.title_tsv, master_search.year) IS DISTINCT FROM (EXCLUDED.title_tsv, EXCLUDED.year)"
)


def _trigger(operation: str) -> str:
    match = re.search(rf"CREATE TRIGGER master_search_capture_{operation} .*?;", _TRIGGERS, re.DOTALL)
    assert match, operation
    return match.group(0)


class TestTheValues:
    def test_the_vector_is_the_expression_master_search_ranks_by_today(self) -> None:
        """catalog-api ranks masters by idx_masters_fts's expression; the stored vector must equal it."""
        fts = dict(_SPECIFIC_INDEXES)["idx_masters_fts"]
        assert MASTER_TITLE_TSVECTOR == "to_tsvector('english', COALESCE(data->>'title', ''))"
        assert f"ON masters USING GIN ({MASTER_TITLE_TSVECTOR})" in fts

    def test_the_year_is_the_text_catalog_api_reads(self) -> None:
        """Verbatim text: catalog-api's `year::int` filter and decade regex apply to it unchanged."""
        assert MASTER_YEAR == "data->>'year'"
        assert _master_year("n") == "n.data->>'year'"
        assert f"ON masters (({MASTER_YEAR}))" in dict(_SPECIFIC_INDEXES)["idx_masters_year"]

    def test_every_writer_computes_both_expressions_over_one_row(self) -> None:
        for name in (
            "master_search_capture_inserted function",
            "master_search_capture_updated function",
            "master_search_backfill_pages function",
            "master_search_backfill_add_missing function",
            "master_search_drift function",
        ):
            match = re.search(r"to_tsvector\('english', COALESCE\((\w+)\.data->>'title', ''\)\), (\w+)\.data->>'year'", _STATEMENTS[name])
            assert match, name
            assert match.group(1) == match.group(2), name
            assert f"{_title_tsvector(match.group(1))}, {_master_year(match.group(1))}" in _STATEMENTS[name], name

    def test_the_table_is_one_narrow_row_per_master_keyed_like_masters(self) -> None:
        table = _STATEMENTS["master_search table"]
        assert table.startswith(f"CREATE TABLE IF NOT EXISTS {MASTER_SEARCH_TABLE} (")
        assert re.search(r"master_id VARCHAR\s+PRIMARY KEY", table)
        assert re.search(r"title_tsv TSVECTOR NOT NULL", table)
        assert re.search(r"year\s+TEXT\n", table)  # nullable: a master may have no year
        assert "REFERENCES" not in table  # a foreign key would make `TRUNCATE masters` fail

    def test_the_index_is_a_gin_over_the_stored_vector_and_skippable_on_reapply(self) -> None:
        index = _STATEMENTS[MASTER_SEARCH_INDEX]
        assert index == f"CREATE INDEX IF NOT EXISTS {MASTER_SEARCH_INDEX} ON {MASTER_SEARCH_TABLE} USING GIN (title_tsv)"
        assert _noop_probe(index) is not None


class TestTheTriggers:
    def test_inserts_and_deletes_are_captured_once_per_statement(self) -> None:
        assert "AFTER INSERT ON public.masters" in _trigger("insert")
        assert "REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT" in _trigger("insert")
        assert "AFTER DELETE ON public.masters" in _trigger("delete")
        assert "REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT" in _trigger("delete")
        assert "AFTER TRUNCATE ON public.masters" in _trigger("truncate")

    def test_an_update_is_captured_only_when_it_sets_the_id_or_the_document(self) -> None:
        update = _trigger("update")
        assert "AFTER UPDATE OF data_id, data ON public.masters" in update
        assert "FOR EACH ROW" in update

    def test_the_four_triggers_are_created_together_and_skipped_when_present(self) -> None:
        assert [
            re.search(r"CREATE TRIGGER master_search_capture_(\w+)", line).group(1) for line in _TRIGGERS.splitlines() if "CREATE TRIGGER" in line
        ] == list(_SEARCH_TRIGGERS)
        assert "WHERE tgrelid = 'public.masters'::regclass" in _TRIGGERS
        assert f") = {len(_SEARCH_TRIGGERS)} THEN\n        RETURN;" in _TRIGGERS
        assert _TRIGGERS.index("RETURN;") < _TRIGGERS.index("DROP TRIGGER")

    def test_capture_functions_run_as_their_owner_with_a_pinned_search_path(self) -> None:
        assert sorted(_CAPTURE_FUNCTIONS) == sorted(f"master_search_capture_{event}" for event in ("inserted", "updated", "deleted", "truncated"))
        for name in _CAPTURE_FUNCTIONS:
            statement = _STATEMENTS[f"{name} function"]
            assert "SECURITY DEFINER" in statement, name
            assert "SET search_path = pg_catalog, pg_temp" in statement, name

    def test_an_upsert_rewrites_a_row_only_when_its_vector_or_year_changed(self) -> None:
        for name in ("master_search_capture_inserted function", "master_search_capture_updated function"):
            assert _UPSERT in _STATEMENTS[name], name

    def test_a_renamed_master_drops_its_old_row(self) -> None:
        assert "DELETE FROM public.master_search WHERE master_id = OLD.data_id" in _STATEMENTS["master_search_capture_updated function"]


class TestTheBackfill:
    def test_it_can_commit_between_batches_and_reads_masters(self) -> None:
        header = _BACKFILL.split("AS $master_search_backfill$", 1)[0]
        assert "CREATE OR REPLACE PROCEDURE public.master_search_backfill(batch_pages integer DEFAULT 1000, first_page bigint DEFAULT 0)" in header
        assert "SECURITY DEFINER" not in header
        assert "\nSET " not in header
        assert _BACKFILL.count("COMMIT;") == 3
        assert "pg_catalog.pg_relation_size('public.masters'::pg_catalog.regclass)" in _BACKFILL
        assert _BACKFILL.rstrip().endswith("REVOKE ALL ON PROCEDURE public.master_search_backfill(integer, bigint) FROM PUBLIC")

    def test_the_procedure_runs_no_sql_of_its_own_beyond_its_pinned_steps(self) -> None:
        body = _BACKFILL.split("AS $master_search_backfill$", 1)[1]
        for fragment in ("to_tsvector", "->>", "INSERT", "DELETE", "SELECT", "CREATE", "DROP", "format("):
            assert fragment not in body, fragment
        for name, step in _BACKFILL_STEPS.items():
            assert f"public.{name}(" in body, name
            assert "SET search_path = pg_catalog, pg_temp" in step, name
            assert "SECURITY DEFINER" not in step, name
            assert re.search(rf"REVOKE ALL ON FUNCTION public\.{name}\([a-z, ]*\) FROM PUBLIC\s*$", step), name

    def test_every_batch_takes_its_own_lock_before_it_writes(self) -> None:
        for step in _WRITING_STEPS:
            assert step.index("PERFORM public.master_search_backfill_lock();") < step.index("INSERT INTO public.master_search")
            assert "public.masters" in step
            assert "release" not in step

    def test_it_reads_in_heap_order_then_sweeps_what_moved(self) -> None:
        assert "ctid >= format('(%s,0)', first_page)::tid" in _BACKFILL_STEPS["master_search_backfill_pages"]
        find = _BACKFILL_STEPS["master_search_backfill_find_missing"]
        assert "NOT EXISTS (SELECT 1 FROM public.master_search AS s WHERE s.master_id = r.data_id)" in find
        assert "CREATE TEMPORARY TABLE master_search_missing AS" in find

    def test_the_lock_is_on_master_search_and_waits_in_bounded_tries(self) -> None:
        lock = _STATEMENTS["master_search_backfill_lock function"]
        assert "LOCK TABLE public.master_search IN SHARE MODE;" in lock
        assert f"SET lock_timeout = '{SEARCH_BACKFILL_LOCK_TIMEOUT}'" in lock
        assert "EXCEPTION WHEN lock_not_available THEN" in lock


class TestTheDrift:
    def test_stale_compares_the_vector_and_the_year_together(self) -> None:
        drift = _STATEMENTS["master_search_drift function"]
        assert "CREATE OR REPLACE FUNCTION public.master_search_drift()\nRETURNS TABLE (missing bigint, orphaned bigint, stale bigint)" in drift
        assert f"WHERE (s.title_tsv, s.year) IS DISTINCT FROM ({_title_tsvector('r')}, {_master_year('r')})" in drift
        assert "FROM public.masters AS r" in drift


class TestPlacementAndRollback:
    def test_they_follow_masters_and_release_search(self) -> None:
        names = [name for name, _statement in _schema_statements()]
        first = names.index("master_search table")
        assert first > names.index("masters table")
        assert first > names.index("idx_masters_fts")
        assert first == names.index("releases release_search capture triggers") + 1
        assert names[first : first + len(_MASTER_SEARCH_STATEMENTS)] == list(_STATEMENTS)

    def test_it_is_the_whole_shared_design_for_masters(self) -> None:
        assert list(_STATEMENTS) == [
            "master_search table",
            MASTER_SEARCH_INDEX,
            *(f"master_search_capture_{event} function" for event in ("inserted", "updated", "deleted", "truncated")),
            "master_search_backfill_lock function",
            "master_search_backfill_pages function",
            "master_search_backfill_find_missing function",
            "master_search_backfill_add_missing function",
            "master_search_backfill procedure",
            "master_search_drift function",
            "masters master_search capture triggers",
        ]

    def test_nothing_names_releases(self) -> None:
        for name, statement in _STATEMENTS.items():
            assert "release" not in statement, name

    def test_the_rollback_removes_every_object_the_statements_create(self) -> None:
        script = "\n".join(MASTER_SEARCH_ROLLBACK_STATEMENTS)
        created = re.findall(r"CREATE OR REPLACE (?:FUNCTION|PROCEDURE) (public\.\w+)\(", "\n".join(_STATEMENTS.values()))
        assert len(created) == 10
        for name in created:
            assert re.search(rf"DROP (?:FUNCTION|PROCEDURE) IF EXISTS {re.escape(name)}\(", script), name
        for operation in _SEARCH_TRIGGERS:
            assert f"DROP TRIGGER IF EXISTS master_search_capture_{operation} ON public.masters" in script
        assert MASTER_SEARCH_ROLLBACK_STATEMENTS[-1] == f"DROP TABLE IF EXISTS {MASTER_SEARCH_TABLE}"
        assert script.index("DROP TRIGGER") < script.index("DROP FUNCTION")
        assert "release" not in script


class TestCaptureFunctionsCannotBeBorrowed:
    def test_each_refuses_any_table_but_masters_and_is_not_executable_by_public(self) -> None:
        for name in _CAPTURE_FUNCTIONS:
            statement = _STATEMENTS[f"{name} function"]
            assert "IF TG_TABLE_SCHEMA <> 'public' OR TG_TABLE_NAME <> 'masters' THEN" in statement, name
            assert "BEGIN\n    IF TG_TABLE_SCHEMA <> 'public'" in statement, name
            assert statement.rstrip().endswith(f"REVOKE ALL ON FUNCTION public.{name}() FROM PUBLIC"), name
