"""Tests for the partitioned append-only activity schema.

Covers ADR 0010 (first-party events, consent, and deletion closure): the
`activity` schema, the pseudonymous subject link, per-purpose consent grants,
the two month-partitioned behavioural tables with their DEFAULT partitions and
monthly-partition function, the immutability trigger that makes append-only a
property of the database, and the erasure record.

Every assertion is made against the statement lists, never against a database.
"""

from groovemap_schema.postgres import (
    _ACTIVITY_STATEMENTS,
    _INSIGHTS_TABLES,
    _USER_TABLES,
    _schema_statements,
)


# The two consent purposes published in taxonomy/events/v1.
_CONSENT_PURPOSES = ["product_analytics", "model_training"]

# The two month-partitioned behavioural tables.
_PARTITIONED_TABLES = ["events", "impressions"]


def _activity() -> dict[str, str]:
    return dict(_ACTIVITY_STATEMENTS)


def _activity_names() -> list[str]:
    return [name for name, _stmt in _ACTIVITY_STATEMENTS]


class TestActivitySchema:
    """The schema itself."""

    def test_schema_created_first(self) -> None:
        assert _activity_names()[0] == "activity schema"

    def test_schema_creation_is_idempotent(self) -> None:
        assert _activity()["activity schema"] == "CREATE SCHEMA IF NOT EXISTS activity"

    def test_every_statement_is_a_name_sql_pair(self) -> None:
        for entry in _ACTIVITY_STATEMENTS:
            assert len(entry) == 2, f"Expected (name, sql) pair, got: {entry!r}"
            name, stmt = entry
            assert isinstance(name, str) and name
            assert isinstance(stmt, str) and stmt

    def test_every_statement_is_idempotent(self) -> None:
        """IF NOT EXISTS for objects that have it, CREATE OR REPLACE otherwise."""
        for name, stmt in _ACTIVITY_STATEMENTS:
            upper = stmt.upper()
            assert "IF NOT EXISTS" in upper or "CREATE OR REPLACE" in upper, f"'{name}' is not idempotent"

    def test_no_drop_statements(self) -> None:
        for name, stmt in _ACTIVITY_STATEMENTS:
            assert "DROP" not in stmt.upper(), f"'{name}' contains a DROP statement"

    def test_declared_after_users_and_insights(self) -> None:
        """user_subjects and consent_grants reference users(id)."""
        names = [name for name, _stmt in _schema_statements()]
        first_activity = names.index("activity schema")
        assert names.index("users table") < first_activity
        assert names.index(_INSIGHTS_TABLES[-1][0]) < first_activity

    def test_contributes_to_the_full_schema(self) -> None:
        names = [name for name, _stmt in _schema_statements()]
        for name in _activity_names():
            assert name in names


class TestUserSubjects:
    """activity.user_subjects — the one row that links account to pseudonym."""

    def test_table_defined(self) -> None:
        assert "activity.user_subjects table" in _activity()

    def test_user_id_is_the_primary_key_and_cascades(self) -> None:
        stmt = _activity()["activity.user_subjects table"]
        assert "user_id    UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE" in stmt

    def test_subject_id_is_unique_and_minted_as_uuidv7(self) -> None:
        stmt = _activity()["activity.user_subjects table"]
        assert "subject_id UUID NOT NULL UNIQUE DEFAULT uuidv7()" in stmt

    def test_created_at_present(self) -> None:
        assert "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()" in _activity()["activity.user_subjects table"]


class TestConsentGrants:
    """activity.consent_grants — per-purpose consent, closed at two purposes."""

    def test_table_defined(self) -> None:
        assert "activity.consent_grants table" in _activity()

    def test_purpose_check_is_the_closed_set(self) -> None:
        stmt = _activity()["activity.consent_grants table"]
        assert "CHECK (purpose IN (" in stmt
        for purpose in _CONSENT_PURPOSES:
            assert f"'{purpose}'" in stmt, f"consent purpose '{purpose}' missing from the CHECK"

    def test_no_purpose_beyond_the_closed_set(self) -> None:
        """A third purpose would be a vocabulary change, not a schema change."""
        stmt = _activity()["activity.consent_grants table"]
        check = stmt.split("CHECK (purpose IN (", 1)[1].split("))", 1)[0]
        assert check.count("'") == 2 * len(_CONSENT_PURPOSES)

    def test_revocation_is_a_timestamp_not_a_delete(self) -> None:
        stmt = _activity()["activity.consent_grants table"]
        assert "granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()" in stmt
        assert "revoked_at TIMESTAMPTZ" in stmt

    def test_cascades_on_user_delete(self) -> None:
        assert "REFERENCES users(id) ON DELETE CASCADE" in _activity()["activity.consent_grants table"]

    def test_user_purpose_index_defined(self) -> None:
        stmt = _activity()["idx_activity_consent_grants_user_purpose"]
        assert "ON activity.consent_grants (user_id, purpose)" in stmt
        assert "IF NOT EXISTS" in stmt

    def test_index_follows_its_table(self) -> None:
        names = _activity_names()
        assert names.index("activity.consent_grants table") < names.index("idx_activity_consent_grants_user_purpose")


class TestEvents:
    """activity.events — the typed envelope, partitioned by month."""

    def test_table_defined(self) -> None:
        assert "activity.events table" in _activity()

    def test_envelope_columns(self) -> None:
        stmt = _activity()["activity.events table"]
        for column in (
            "event_id",
            "event_type",
            "schema_version",
            "subject_id",
            "session_id",
            "occurred_at",
            "recorded_at",
            "producer",
            "consent_purposes",
            "model_version",
            "feature_version",
            "idempotency_key",
            "payload",
        ):
            assert column in stmt, f"Missing envelope column '{column}' in activity.events"

    def test_event_id_is_a_uuidv7(self) -> None:
        assert "event_id         UUID NOT NULL DEFAULT uuidv7()" in _activity()["activity.events table"]

    def test_partitioned_by_range_on_occurred_at(self) -> None:
        assert "PARTITION BY RANGE (occurred_at)" in _activity()["activity.events table"]

    def test_primary_key_leads_with_the_partition_key(self) -> None:
        assert "PRIMARY KEY (occurred_at, event_id)" in _activity()["activity.events table"]

    def test_idempotency_key_is_unique_within_its_occurrence(self) -> None:
        """Uniqueness on a partitioned table must include the partition key."""
        assert "UNIQUE (occurred_at, idempotency_key)" in _activity()["activity.events table"]

    def test_occurred_at_and_recorded_at_are_separate(self) -> None:
        """A late or replayed write keeps both times honest."""
        stmt = _activity()["activity.events table"]
        assert "occurred_at      TIMESTAMPTZ NOT NULL," in stmt
        assert "recorded_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()" in stmt

    def test_consent_purposes_snapshotted_and_required(self) -> None:
        assert "consent_purposes TEXT[] NOT NULL" in _activity()["activity.events table"]

    def test_payload_defaults_to_an_empty_object(self) -> None:
        assert "payload          JSONB NOT NULL DEFAULT '{}'" in _activity()["activity.events table"]

    def test_no_user_id_column(self) -> None:
        """Events reference the pseudonym only, never account identity."""
        assert "user_id" not in _activity()["activity.events table"]

    def test_indexes_defined(self) -> None:
        activity = _activity()
        assert "ON activity.events (subject_id, occurred_at DESC)" in activity["idx_activity_events_subject_occurred_at"]
        assert "ON activity.events (event_type, occurred_at DESC)" in activity["idx_activity_events_type_occurred_at"]

    def test_indexes_follow_the_table(self) -> None:
        names = _activity_names()
        for index in ("idx_activity_events_subject_occurred_at", "idx_activity_events_type_occurred_at"):
            assert names.index("activity.events table") < names.index(index)


class TestImpressions:
    """activity.impressions — what an offline evaluation cannot reconstruct."""

    def test_table_defined(self) -> None:
        assert "activity.impressions table" in _activity()

    def test_columns(self) -> None:
        stmt = _activity()["activity.impressions table"]
        for column in (
            "impression_id",
            "subject_id",
            "surface",
            "policy_id",
            "candidate_set_id",
            "position",
            "item_id",
            "score",
            "propensity",
            "request_id",
            "occurred_at",
            "recorded_at",
            "consent_purposes",
        ):
            assert column in stmt, f"Missing column '{column}' in activity.impressions"

    def test_the_four_irrecoverable_fields_are_required(self) -> None:
        """policy_id, candidate_set_id, position, and propensity describe the
        decision as it was made; three are NOT NULL and propensity is nullable
        for a policy that is not probabilistic."""
        stmt = _activity()["activity.impressions table"]
        assert "policy_id        TEXT NOT NULL" in stmt
        assert "candidate_set_id UUID NOT NULL" in stmt
        assert "position         INTEGER NOT NULL" in stmt
        assert "propensity       REAL," in stmt

    def test_item_id_is_the_native_identifier(self) -> None:
        """ADR 0009's native id, not a provider id."""
        assert "item_id          UUID NOT NULL" in _activity()["activity.impressions table"]

    def test_partitioned_by_range_on_occurred_at(self) -> None:
        assert "PARTITION BY RANGE (occurred_at)" in _activity()["activity.impressions table"]

    def test_primary_key_leads_with_the_partition_key(self) -> None:
        assert "PRIMARY KEY (occurred_at, impression_id)" in _activity()["activity.impressions table"]

    def test_consent_purposes_snapshotted_and_required(self) -> None:
        assert "consent_purposes TEXT[] NOT NULL" in _activity()["activity.impressions table"]

    def test_no_outcome_columns(self) -> None:
        """Opened, saved, dismissed, and hidden are events, not columns here."""
        stmt = _activity()["activity.impressions table"].lower()
        for outcome in ("opened", "saved", "dismissed", "hidden"):
            assert outcome not in stmt, f"Outcome '{outcome}' must be an event, not an impression column"

    def test_no_user_id_column(self) -> None:
        assert "user_id" not in _activity()["activity.impressions table"]

    def test_indexes_defined(self) -> None:
        activity = _activity()
        assert "ON activity.impressions (subject_id, occurred_at DESC)" in activity["idx_activity_impressions_subject_occurred_at"]
        assert "ON activity.impressions (candidate_set_id)" in activity["idx_activity_impressions_candidate_set_id"]

    def test_indexes_follow_the_table(self) -> None:
        names = _activity_names()
        for index in (
            "idx_activity_impressions_subject_occurred_at",
            "idx_activity_impressions_candidate_set_id",
        ):
            assert names.index("activity.impressions table") < names.index(index)


class TestDefaultPartitions:
    """An insert never fails for a missing month."""

    def test_default_partition_per_table(self) -> None:
        activity = _activity()
        for table in _PARTITIONED_TABLES:
            name = f"activity.{table}_default partition"
            assert name in activity, f"Missing DEFAULT partition for activity.{table}"
            assert activity[name] == f"CREATE TABLE IF NOT EXISTS activity.{table}_default PARTITION OF activity.{table} DEFAULT"

    def test_default_partition_follows_its_parent(self) -> None:
        names = _activity_names()
        for table in _PARTITIONED_TABLES:
            assert names.index(f"activity.{table} table") < names.index(f"activity.{table}_default partition")


class TestEnsureMonthPartition:
    """activity.ensure_month_partition(table_name, month)."""

    def _function(self) -> str:
        return _activity()["activity.ensure_month_partition function"]

    def test_function_defined(self) -> None:
        assert "activity.ensure_month_partition function" in _activity()

    def test_signature(self) -> None:
        assert "CREATE OR REPLACE FUNCTION activity.ensure_month_partition(table_name TEXT, month DATE)" in self._function()

    def test_is_plpgsql(self) -> None:
        assert "LANGUAGE plpgsql" in self._function()

    def test_partition_name_is_the_year_month_form(self) -> None:
        """activity.<table>_yYYYYmMM."""
        body = self._function()
        assert "format('%s_y%sm%s', table_name, to_char(month_start, 'YYYY'), to_char(month_start, 'MM'))" in body

    def test_bounds_span_exactly_one_month(self) -> None:
        body = self._function()
        assert "date_trunc('month', month)::DATE" in body
        assert "(date_trunc('month', month) + INTERVAL '1 month')::DATE" in body

    def test_creates_a_range_partition_of_the_named_table(self) -> None:
        body = self._function()
        assert "PARTITION OF activity.%I FOR VALUES FROM (%L) TO (%L)" in body

    def test_creation_is_idempotent(self) -> None:
        """Two writers racing on the first event of a month must not collide."""
        assert "CREATE TABLE IF NOT EXISTS activity.%I" in self._function()

    def test_identifiers_are_quoted_not_interpolated(self) -> None:
        """%I quotes an identifier; %L quotes a literal. Neither is %s."""
        executed = self._function().split("EXECUTE format(", 1)[1]
        assert "%I" in executed
        assert "%L" in executed

    def test_declared_after_both_partitioned_tables(self) -> None:
        names = _activity_names()
        position = names.index("activity.ensure_month_partition function")
        for table in _PARTITIONED_TABLES:
            assert names.index(f"activity.{table} table") < position


class TestRejectMutation:
    """activity.reject_mutation() and the triggers that install it."""

    def _function(self) -> str:
        return _activity()["activity.reject_mutation function"]

    def test_function_defined(self) -> None:
        assert "activity.reject_mutation function" in _activity()

    def test_signature_returns_trigger(self) -> None:
        body = self._function()
        assert "CREATE OR REPLACE FUNCTION activity.reject_mutation()" in body
        assert "RETURNS trigger" in body

    def test_raises_by_default(self) -> None:
        assert "RAISE EXCEPTION" in self._function()

    def test_bypass_is_the_session_local_erasure_setting(self) -> None:
        """current_setting's second argument suppresses the error when unset."""
        assert "current_setting('groovemap.erasure', true) = 'on'" in self._function()

    def test_bypass_lets_the_row_through(self) -> None:
        """A BEFORE trigger returning NULL would cancel the erasure it permits."""
        body = self._function()
        assert "RETURN OLD;" in body
        assert "RETURN NEW;" in body

    def test_triggers_defined_for_both_tables(self) -> None:
        activity = _activity()
        for table in _PARTITIONED_TABLES:
            assert f"activity.{table} reject_mutation trigger" in activity

    def test_triggers_fire_before_update_or_delete_for_each_row(self) -> None:
        activity = _activity()
        for table in _PARTITIONED_TABLES:
            stmt = activity[f"activity.{table} reject_mutation trigger"]
            assert f"BEFORE UPDATE OR DELETE ON activity.{table}" in stmt
            assert "FOR EACH ROW EXECUTE FUNCTION activity.reject_mutation()" in stmt

    def test_triggers_do_not_fire_on_insert(self) -> None:
        """Append-only means inserts are the one thing that must still work."""
        activity = _activity()
        for table in _PARTITIONED_TABLES:
            assert "INSERT" not in activity[f"activity.{table} reject_mutation trigger"].upper()

    def test_trigger_creation_is_idempotent(self) -> None:
        activity = _activity()
        for table in _PARTITIONED_TABLES:
            assert "CREATE OR REPLACE TRIGGER" in activity[f"activity.{table} reject_mutation trigger"]

    def test_triggers_follow_the_function_and_their_tables(self) -> None:
        names = _activity_names()
        function_position = names.index("activity.reject_mutation function")
        for table in _PARTITIONED_TABLES:
            trigger_position = names.index(f"activity.{table} reject_mutation trigger")
            assert function_position < trigger_position
            assert names.index(f"activity.{table} table") < trigger_position


class TestErasures:
    """activity.erasures — the record the deletion claim leaves behind."""

    def test_table_defined(self) -> None:
        assert "activity.erasures table" in _activity()

    def test_columns(self) -> None:
        stmt = _activity()["activity.erasures table"]
        for column in (
            "subject_id",
            "requested_at",
            "completed_at",
            "events_deleted",
            "impressions_deleted",
            "model_versions_before",
            "notes",
        ):
            assert column in stmt, f"Missing column '{column}' in activity.erasures"

    def test_keyed_by_subject_not_user(self) -> None:
        """The link row is gone by the time the erasure completes."""
        stmt = _activity()["activity.erasures table"]
        assert "subject_id            UUID NOT NULL" in stmt
        assert "user_id" not in stmt

    def test_model_versions_before_is_an_array(self) -> None:
        """Deleting rows does not retrain a model; naming the versions is what
        makes the residual question answerable."""
        assert "model_versions_before TEXT[]" in _activity()["activity.erasures table"]

    def test_row_counts_are_bigint(self) -> None:
        stmt = _activity()["activity.erasures table"]
        assert "events_deleted        BIGINT" in stmt
        assert "impressions_deleted   BIGINT" in stmt

    def test_subject_index_defined(self) -> None:
        stmt = _activity()["idx_activity_erasures_subject_id"]
        assert "ON activity.erasures (subject_id)" in stmt
        assert "IF NOT EXISTS" in stmt

    def test_index_follows_its_table(self) -> None:
        names = _activity_names()
        assert names.index("activity.erasures table") < names.index("idx_activity_erasures_subject_id")


class TestActivityIsAdditive:
    """The whole schema is new, so every change stays inside contract v1."""

    def test_no_activity_name_collides_with_a_user_table_name(self) -> None:
        assert not (set(_activity_names()) & {name for name, _stmt in _USER_TABLES})

    def test_no_existing_object_is_altered(self) -> None:
        """Nothing outside the activity schema is touched."""
        for name, stmt in _ACTIVITY_STATEMENTS:
            upper = stmt.upper()
            assert "ALTER TABLE" not in upper, f"'{name}' alters an existing table"
            assert "ALTER COLUMN" not in upper, f"'{name}' alters an existing column"

    def test_every_object_lives_in_the_activity_schema(self) -> None:
        for name, stmt in _ACTIVITY_STATEMENTS:
            if name == "activity schema":
                continue
            assert "activity." in stmt, f"'{name}' declares an object outside the activity schema"
