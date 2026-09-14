"""PostgreSQL schema definitions for GrooveMap.

Single source of truth for all PostgreSQL tables and indexes.
All statements use IF NOT EXISTS — safe to run on every startup; subsequent
runs are no-ops for already-created schema objects. Schema is never dropped.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from psycopg import sql


if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator


logger = logging.getLogger(__name__)


# The four Discogs entity tables.  Each has the same base structure:
#   data_id VARCHAR PRIMARY KEY  — Discogs numeric ID as a string
#   hash    VARCHAR NOT NULL     — SHA-256 of the record, used for change detection
#   data    JSONB   NOT NULL     — full Discogs record document
_ENTITY_TABLES = ["artists", "labels", "masters", "releases"]

# Table-specific JSONB field indexes, as (name, sql_string) pairs.
# Plain string literals are safe here because all table/column names are
# hardcoded constants, not user-supplied values.
_SPECIFIC_INDEXES: list[tuple[str, str]] = [
    # Artists
    (
        "idx_artists_name",
        "CREATE INDEX IF NOT EXISTS idx_artists_name ON artists ((data->>'name'))",
    ),
    # Labels
    (
        "idx_labels_name",
        "CREATE INDEX IF NOT EXISTS idx_labels_name ON labels ((data->>'name'))",
    ),
    # Masters
    (
        "idx_masters_title",
        "CREATE INDEX IF NOT EXISTS idx_masters_title ON masters ((data->>'title'))",
    ),
    (
        "idx_masters_year",
        "CREATE INDEX IF NOT EXISTS idx_masters_year ON masters ((data->>'year'))",
    ),
    # Releases
    (
        "idx_releases_title",
        "CREATE INDEX IF NOT EXISTS idx_releases_title ON releases ((data->>'title'))",
    ),
    (
        "idx_releases_year",
        "CREATE INDEX IF NOT EXISTS idx_releases_year ON releases ((data->>'year'))",
    ),
    (
        "idx_releases_country",
        "CREATE INDEX IF NOT EXISTS idx_releases_country ON releases ((data->>'country'))",
    ),
    (
        "idx_releases_genres",
        "CREATE INDEX IF NOT EXISTS idx_releases_genres ON releases USING GIN ((data->'genres'))",
    ),
    (
        "idx_releases_labels",
        "CREATE INDEX IF NOT EXISTS idx_releases_labels ON releases USING GIN ((data->'labels'))",
    ),
    # Canonical media block (ADR 0007) — additive column plus a GIN index on the
    # sorted family-id list for fast media-family filtering.
    (
        "releases add media column",
        "ALTER TABLE releases ADD COLUMN IF NOT EXISTS media JSONB",
    ),
    (
        "idx_releases_media_families",
        "CREATE INDEX IF NOT EXISTS idx_releases_media_families ON releases USING GIN ((media->'families'))",
    ),
    # Full-text search GIN indexes — used by /api/search
    (
        "idx_artists_fts",
        "CREATE INDEX IF NOT EXISTS idx_artists_fts ON artists USING GIN (to_tsvector('english', COALESCE(data->>'name', '')))",
    ),
    (
        "idx_labels_fts",
        "CREATE INDEX IF NOT EXISTS idx_labels_fts ON labels USING GIN (to_tsvector('english', COALESCE(data->>'name', '')))",
    ),
    (
        "idx_masters_fts",
        "CREATE INDEX IF NOT EXISTS idx_masters_fts ON masters USING GIN (to_tsvector('english', COALESCE(data->>'title', '')))",
    ),
    (
        "idx_releases_fts",
        "CREATE INDEX IF NOT EXISTS idx_releases_fts ON releases USING GIN (to_tsvector('english', COALESCE(data->>'title', '')))",
    ),
]


# User-facing tables for auth and personal data
_USER_TABLES: list[tuple[str, str]] = [
    (
        "users table",
        """
        CREATE TABLE IF NOT EXISTS users (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email                VARCHAR(255) UNIQUE NOT NULL,
            hashed_password      VARCHAR(255) NOT NULL,
            is_active            BOOLEAN NOT NULL DEFAULT TRUE,
            is_admin             BOOLEAN NOT NULL DEFAULT FALSE,
            password_changed_at  TIMESTAMP WITH TIME ZONE,
            totp_secret          VARCHAR,
            totp_enabled         BOOLEAN NOT NULL DEFAULT FALSE,
            totp_recovery_codes  JSONB,
            totp_failed_attempts INTEGER NOT NULL DEFAULT 0,
            totp_locked_until    TIMESTAMP WITH TIME ZONE,
            created_at           TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at           TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "users.password_changed_at column",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMP WITH TIME ZONE",
    ),
    (
        "users.totp_secret column",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_secret VARCHAR",
    ),
    (
        "users.totp_enabled column",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_enabled BOOLEAN NOT NULL DEFAULT FALSE",
    ),
    (
        "users.totp_recovery_codes column",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_recovery_codes JSONB",
    ),
    (
        "users.totp_failed_attempts column",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_failed_attempts INTEGER NOT NULL DEFAULT 0",
    ),
    (
        "users.totp_locked_until column",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_locked_until TIMESTAMP WITH TIME ZONE",
    ),
    (
        "oauth_tokens table",
        """
        CREATE TABLE IF NOT EXISTS oauth_tokens (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider          VARCHAR(50) NOT NULL,
            access_token      TEXT NOT NULL,
            access_secret     TEXT NOT NULL,
            provider_username VARCHAR(255),
            provider_user_id  VARCHAR(255),
            created_at        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            UNIQUE(user_id, provider)
        )
        """,
    ),
    # app_tokens: revocable Bearer tokens for third-party app authorization
    # (e.g. GRUVAX kiosk). Plaintext shown ONCE at mint time; only the SHA-256
    # hex hash is persisted. Revoked rows are tombstones — never deleted, so
    # the audit trail is preserved. See docs/specs/v2-gruvax-integration.md.
    (
        "app_tokens table",
        """
        CREATE TABLE IF NOT EXISTS app_tokens (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name         VARCHAR(255) NOT NULL,
            scope        TEXT[] NOT NULL,
            token_hash   VARCHAR(64) NOT NULL,
            created_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            last_used_at TIMESTAMP WITH TIME ZONE,
            revoked_at   TIMESTAMP WITH TIME ZONE
        )
        """,
    ),
    (
        "idx_app_tokens_user_active",
        "CREATE INDEX IF NOT EXISTS idx_app_tokens_user_active ON app_tokens (user_id) WHERE revoked_at IS NULL",
    ),
    (
        "idx_app_tokens_token_lookup",
        "CREATE INDEX IF NOT EXISTS idx_app_tokens_token_lookup ON app_tokens (token_hash) WHERE revoked_at IS NULL",
    ),
    (
        "app_config table",
        """
        CREATE TABLE IF NOT EXISTS app_config (
            key        VARCHAR(255) PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "user_collections table",
        """
        CREATE TABLE IF NOT EXISTS user_collections (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            release_id   BIGINT NOT NULL,
            instance_id  BIGINT,
            folder_id    INTEGER,
            title        VARCHAR(500),
            artist       VARCHAR(500),
            year         INTEGER,
            formats      JSONB,
            media        JSONB,
            label        VARCHAR(255),
            condition    VARCHAR(100),
            rating       SMALLINT,
            notes        TEXT,
            date_added   TIMESTAMP WITH TIME ZONE,
            metadata     JSONB,
            created_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            UNIQUE NULLS NOT DISTINCT(user_id, release_id, instance_id)
        )
        """,
    ),
    # Canonical media block (ADR 0007): user_collections converges on the same
    # `media` column shape as the release tables and user_wantlists.
    (
        "user_collections.media column",
        "ALTER TABLE user_collections ADD COLUMN IF NOT EXISTS media JSONB",
    ),
    # Native identity (ADR 0009): the provider-keyed collection row gains the
    # native catalog item it resolves to and the native owned copy that replaces
    # the Discogs `instance_id` as the identity of the physical copy. Both are
    # nullable and additive; `release_id` and `instance_id` stay authoritative
    # until a future contraction decision retires them.
    (
        "user_collections.gm_item_id column",
        "ALTER TABLE user_collections ADD COLUMN IF NOT EXISTS gm_item_id UUID",
    ),
    (
        "user_collections.owned_copy_id column",
        "ALTER TABLE user_collections ADD COLUMN IF NOT EXISTS owned_copy_id UUID",
    ),
    (
        "idx_user_collections_user_id",
        "CREATE INDEX IF NOT EXISTS idx_user_collections_user_id ON user_collections (user_id)",
    ),
    (
        "idx_user_collections_release_id",
        "CREATE INDEX IF NOT EXISTS idx_user_collections_release_id ON user_collections (release_id)",
    ),
    (
        "idx_user_collections_media_families",
        "CREATE INDEX IF NOT EXISTS idx_user_collections_media_families ON user_collections USING GIN ((media->'families'))",
    ),
    (
        "idx_user_collections_gm_item_id",
        "CREATE INDEX IF NOT EXISTS idx_user_collections_gm_item_id ON user_collections (gm_item_id)",
    ),
    (
        "idx_user_collections_owned_copy_id",
        "CREATE INDEX IF NOT EXISTS idx_user_collections_owned_copy_id ON user_collections (owned_copy_id)",
    ),
    (
        "user_wantlists table",
        """
        CREATE TABLE IF NOT EXISTS user_wantlists (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            release_id BIGINT NOT NULL,
            title      VARCHAR(500),
            artist     VARCHAR(500),
            year       INTEGER,
            format     VARCHAR(255),
            media      JSONB,
            rating     SMALLINT,
            notes      TEXT,
            date_added TIMESTAMP WITH TIME ZONE,
            metadata   JSONB,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            UNIQUE(user_id, release_id)
        )
        """,
    ),
    # Canonical media block (ADR 0007): user_wantlists converges on the same
    # `media` column shape as the release tables and user_collections. The
    # scalar `format` column is retained as legacy provenance.
    (
        "user_wantlists.media column",
        "ALTER TABLE user_wantlists ADD COLUMN IF NOT EXISTS media JSONB",
    ),
    # Native identity (ADR 0009): additive, nullable native catalog item.
    (
        "user_wantlists.gm_item_id column",
        "ALTER TABLE user_wantlists ADD COLUMN IF NOT EXISTS gm_item_id UUID",
    ),
    (
        "idx_user_wantlists_user_id",
        "CREATE INDEX IF NOT EXISTS idx_user_wantlists_user_id ON user_wantlists (user_id)",
    ),
    (
        "idx_user_wantlists_release_id",
        "CREATE INDEX IF NOT EXISTS idx_user_wantlists_release_id ON user_wantlists (release_id)",
    ),
    (
        "idx_user_wantlists_gm_item_id",
        "CREATE INDEX IF NOT EXISTS idx_user_wantlists_gm_item_id ON user_wantlists (gm_item_id)",
    ),
    # ------------------------------------------------------------------
    # Native identity (ADR 0009)
    #
    # GrooveMap mints its own UUID version 7 identifiers for five entities and
    # demotes every provider identifier to evidence in `provider_aliases`.
    # Deployment pins PostgreSQL 18, so `uuidv7()` is a standard facility and no
    # application-side identifier library is introduced.  These tables are
    # declared after `users`, `user_collections`, and `user_wantlists` because
    # they reference them.
    # ------------------------------------------------------------------
    (
        "catalog_items table",
        """
        CREATE TABLE IF NOT EXISTS catalog_items (
            id         UUID PRIMARY KEY DEFAULT uuidv7(),
            kind       TEXT NOT NULL CHECK (kind IN ('release', 'master', 'artist', 'label')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "artifacts table",
        """
        CREATE TABLE IF NOT EXISTS artifacts (
            id         UUID PRIMARY KEY DEFAULT uuidv7(),
            item_id    UUID NOT NULL REFERENCES catalog_items(id),
            created_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    # owned_copies makes the physical copy first class: it exists because a user
    # says it does, not because a provider listed it.  `collection_row_id` is the
    # optional back-link to the provider-keyed collection row, set to NULL rather
    # than cascading so the copy survives a collection resync.
    (
        "owned_copies table",
        """
        CREATE TABLE IF NOT EXISTS owned_copies (
            id                UUID PRIMARY KEY DEFAULT uuidv7(),
            user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            artifact_id       UUID REFERENCES artifacts(id),
            item_id           UUID NOT NULL REFERENCES catalog_items(id),
            collection_row_id UUID REFERENCES user_collections(id) ON DELETE SET NULL,
            acquired_at       TIMESTAMPTZ,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "idx_owned_copies_user_id",
        "CREATE INDEX IF NOT EXISTS idx_owned_copies_user_id ON owned_copies (user_id)",
    ),
    # At most one owned copy per collection row.  `collection_row_id` is nullable
    # and NULLs must stay distinct, so the uniqueness is a partial index rather
    # than a table constraint.
    (
        "idx_owned_copies_collection_row_id",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_owned_copies_collection_row_id ON owned_copies (collection_row_id) WHERE collection_row_id IS NOT NULL",
    ),
    (
        "collection_snapshots table",
        """
        CREATE TABLE IF NOT EXISTS collection_snapshots (
            id           UUID PRIMARY KEY DEFAULT uuidv7(),
            user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            taken_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            content_hash BYTEA NOT NULL,
            item_count   INTEGER NOT NULL,
            copy_ids     UUID[] NOT NULL
        )
        """,
    ),
    (
        "idx_collection_snapshots_user_taken_at",
        "CREATE INDEX IF NOT EXISTS idx_collection_snapshots_user_taken_at ON collection_snapshots (user_id, taken_at DESC)",
    ),
    # observations are user-captured evidence about a copy or an edition — a
    # matrix inscription, a grading, a purchase price — so at least one of the
    # two subjects must be present for the row to mean anything.
    (
        "observations table",
        """
        CREATE TABLE IF NOT EXISTS observations (
            id            UUID PRIMARY KEY DEFAULT uuidv7(),
            user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            owned_copy_id UUID REFERENCES owned_copies(id) ON DELETE CASCADE,
            artifact_id   UUID REFERENCES artifacts(id),
            kind          TEXT NOT NULL,
            value         TEXT NOT NULL,
            source        TEXT NOT NULL,
            confidence    REAL,
            observed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT observations_subject_present CHECK (owned_copy_id IS NOT NULL OR artifact_id IS NOT NULL)
        )
        """,
    ),
    (
        "idx_observations_user_id",
        "CREATE INDEX IF NOT EXISTS idx_observations_user_id ON observations (user_id)",
    ),
    (
        "idx_observations_artifact_kind",
        "CREATE INDEX IF NOT EXISTS idx_observations_artifact_kind ON observations (artifact_id, kind)",
    ),
    # provider_aliases maps every external namespace — Discogs, MusicBrainz,
    # Wikidata, barcodes, catalogue numbers, ISRCs, matrix inscriptions — onto a
    # native id, with a validity interval so a provider merge or split is a new
    # row rather than an in-place rewrite.
    (
        "provider_aliases table",
        """
        CREATE TABLE IF NOT EXISTS provider_aliases (
            id          UUID PRIMARY KEY DEFAULT uuidv7(),
            provider    TEXT NOT NULL,
            entity_kind TEXT NOT NULL,
            external_id TEXT NOT NULL,
            native_id   UUID NOT NULL,
            valid_from  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            valid_to    TIMESTAMPTZ,
            confidence  REAL NOT NULL DEFAULT 1.0,
            source      TEXT NOT NULL DEFAULT 'catalog',
            asserted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    # The lookup-or-create key.  Uniqueness holds only over the currently valid
    # row, which is what makes a concurrent SELECT / INSERT ... ON CONFLICT DO
    # NOTHING / re-SELECT converge on one native id under any number of writers.
    (
        "idx_provider_aliases_provider_entity_kind_external_id",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_aliases_provider_entity_kind_external_id "
        "ON provider_aliases (provider, entity_kind, external_id) WHERE valid_to IS NULL",
    ),
    (
        "idx_provider_aliases_native_id",
        "CREATE INDEX IF NOT EXISTS idx_provider_aliases_native_id ON provider_aliases (native_id)",
    ),
    (
        "sync_history table",
        """
        CREATE TABLE IF NOT EXISTS sync_history (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            sync_type     VARCHAR(50) NOT NULL,
            status        VARCHAR(50) NOT NULL DEFAULT 'pending',
            items_synced  INTEGER,
            pages_fetched INTEGER,
            error_message TEXT,
            started_at    TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            completed_at  TIMESTAMP WITH TIME ZONE
        )
        """,
    ),
    (
        "idx_sync_history_user_started",
        "CREATE INDEX IF NOT EXISTS idx_sync_history_user_started ON sync_history (user_id, started_at DESC)",
    ),
    (
        "idx_sync_history_running",
        "CREATE INDEX IF NOT EXISTS idx_sync_history_running ON sync_history (user_id) WHERE status = 'running'",
    ),
    (
        "extraction_history",
        """
        CREATE TABLE IF NOT EXISTS extraction_history (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            triggered_by UUID NOT NULL REFERENCES users(id),
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            started_at TIMESTAMP WITH TIME ZONE,
            completed_at TIMESTAMP WITH TIME ZONE,
            record_counts JSONB,
            error_message TEXT,
            extractor_version VARCHAR(50),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "idx_extraction_history_status",
        "CREATE INDEX IF NOT EXISTS idx_extraction_history_status ON extraction_history(status)",
    ),
    (
        "idx_extraction_history_created_at",
        "CREATE INDEX IF NOT EXISTS idx_extraction_history_created_at ON extraction_history(created_at DESC)",
    ),
    (
        "queue_metrics table",
        """
        CREATE TABLE IF NOT EXISTS queue_metrics (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            recorded_at TIMESTAMPTZ NOT NULL,
            queue_name VARCHAR(100) NOT NULL,
            messages_ready INTEGER,
            messages_unacknowledged INTEGER,
            consumers INTEGER,
            publish_rate REAL,
            ack_rate REAL
        )
        """,
    ),
    (
        "idx_queue_metrics_recorded_queue",
        "CREATE INDEX IF NOT EXISTS idx_queue_metrics_recorded_queue ON queue_metrics (recorded_at, queue_name)",
    ),
    (
        "service_health_metrics table",
        """
        CREATE TABLE IF NOT EXISTS service_health_metrics (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            recorded_at TIMESTAMPTZ NOT NULL,
            service_name VARCHAR(50) NOT NULL,
            status VARCHAR(20),
            response_time_ms REAL,
            endpoint_stats JSONB
        )
        """,
    ),
    (
        "idx_service_health_recorded_service",
        "CREATE INDEX IF NOT EXISTS idx_service_health_recorded_service ON service_health_metrics (recorded_at, service_name)",
    ),
    (
        "admin_audit_log table",
        """
        CREATE TABLE IF NOT EXISTS admin_audit_log (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            admin_id   UUID NOT NULL REFERENCES users(id),
            action     VARCHAR(100) NOT NULL,
            target     VARCHAR(255),
            details    JSONB,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "idx_admin_audit_log_created_at",
        "CREATE INDEX IF NOT EXISTS idx_admin_audit_log_created_at ON admin_audit_log (created_at DESC)",
    ),
    (
        "idx_admin_audit_log_admin_id",
        "CREATE INDEX IF NOT EXISTS idx_admin_audit_log_admin_id ON admin_audit_log (admin_id)",
    ),
]


# Insights tables — precomputed analytics stored in a dedicated schema.
# All tables include computed_at for cache freshness checks.
_INSIGHTS_TABLES: list[tuple[str, str]] = [
    (
        "insights schema",
        "CREATE SCHEMA IF NOT EXISTS insights",
    ),
    (
        "insights.artist_centrality table",
        """
        CREATE TABLE IF NOT EXISTS insights.artist_centrality (
            rank            INT NOT NULL,
            artist_id       TEXT NOT NULL,
            artist_name     TEXT NOT NULL,
            edge_count      BIGINT NOT NULL,
            computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (rank)
        )
        """,
    ),
    (
        "insights.genre_trends table",
        """
        CREATE TABLE IF NOT EXISTS insights.genre_trends (
            genre           TEXT NOT NULL,
            decade          INT NOT NULL,
            release_count   BIGINT NOT NULL,
            computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (genre, decade)
        )
        """,
    ),
    (
        "insights.label_longevity table",
        """
        CREATE TABLE IF NOT EXISTS insights.label_longevity (
            rank            INT NOT NULL,
            label_id        TEXT NOT NULL,
            label_name      TEXT NOT NULL,
            first_year      INT NOT NULL,
            last_year       INT,
            years_active    INT NOT NULL,
            total_releases  BIGINT NOT NULL,
            peak_decade     INT,
            still_active    BOOLEAN NOT NULL DEFAULT FALSE,
            computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (rank)
        )
        """,
    ),
    (
        "insights.monthly_anniversaries table",
        """
        CREATE TABLE IF NOT EXISTS insights.monthly_anniversaries (
            master_id       TEXT NOT NULL,
            title           TEXT NOT NULL,
            artist_name     TEXT,
            release_year    INT NOT NULL,
            anniversary     INT NOT NULL,
            computed_month  INT NOT NULL,
            computed_year   INT NOT NULL,
            computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (master_id, computed_year, computed_month)
        )
        """,
    ),
    (
        "insights.data_completeness table",
        """
        CREATE TABLE IF NOT EXISTS insights.data_completeness (
            entity_type     TEXT NOT NULL,
            total_count     BIGINT NOT NULL,
            with_image      BIGINT NOT NULL DEFAULT 0,
            with_year       BIGINT NOT NULL DEFAULT 0,
            with_country    BIGINT NOT NULL DEFAULT 0,
            with_genre      BIGINT NOT NULL DEFAULT 0,
            completeness_pct NUMERIC(5,2) NOT NULL DEFAULT 0,
            computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (entity_type)
        )
        """,
    ),
    (
        "insights.release_rarity table",
        """
        CREATE TABLE IF NOT EXISTS insights.release_rarity (
            release_id      BIGINT PRIMARY KEY,
            title           TEXT,
            artist_name     TEXT,
            year            INTEGER,
            rarity_score    REAL NOT NULL,
            tier            TEXT NOT NULL,
            hidden_gem_score REAL,
            pressing_scarcity REAL,
            label_catalog   REAL,
            format_rarity   REAL,
            temporal_scarcity REAL,
            graph_isolation REAL,
            collection_prevalence REAL,
            computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "idx_release_rarity_score",
        "CREATE INDEX IF NOT EXISTS idx_release_rarity_score ON insights.release_rarity (rarity_score DESC)",
    ),
    (
        "idx_release_rarity_tier",
        "CREATE INDEX IF NOT EXISTS idx_release_rarity_tier ON insights.release_rarity (tier)",
    ),
    (
        "idx_release_rarity_gem",
        "CREATE INDEX IF NOT EXISTS idx_release_rarity_gem ON insights.release_rarity (hidden_gem_score DESC NULLS LAST)",
    ),
    (
        "insights.community_counts table",
        """
        CREATE TABLE IF NOT EXISTS insights.community_counts (
            release_id      BIGINT PRIMARY KEY,
            have_count      INTEGER NOT NULL DEFAULT 0,
            want_count      INTEGER NOT NULL DEFAULT 0,
            fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "idx_community_counts_fetched",
        "CREATE INDEX IF NOT EXISTS idx_community_counts_fetched ON insights.community_counts (fetched_at)",
    ),
    (
        "insights.computation_log table",
        """
        CREATE TABLE IF NOT EXISTS insights.computation_log (
            id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            insight_type    TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'running',
            started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at    TIMESTAMPTZ,
            rows_affected   BIGINT,
            error_message   TEXT,
            duration_ms     BIGINT
        )
        """,
    ),
    (
        "idx_computation_log_type_started",
        "CREATE INDEX IF NOT EXISTS idx_computation_log_type_started ON insights.computation_log (insight_type, started_at DESC)",
    ),
    (
        "idx_anniversaries_month_year",
        "CREATE INDEX IF NOT EXISTS idx_anniversaries_month_year ON insights.monthly_anniversaries (computed_year, computed_month)",
    ),
    (
        "idx_genre_trends_genre",
        "CREATE INDEX IF NOT EXISTS idx_genre_trends_genre ON insights.genre_trends (genre)",
    ),
    (
        "insights.release_rarity add collection_prevalence",
        "ALTER TABLE insights.release_rarity ADD COLUMN IF NOT EXISTS collection_prevalence REAL",
    ),
    # Media-neutral rarity (ADR 0007): the descriptor-keyed format_rarity signal
    # is retained, and rarity gains a medium-keyed signal plus the per-family
    # breakdown so a filtered or family-scoped score can be reconstructed.
    (
        "insights.release_rarity add media_families",
        "ALTER TABLE insights.release_rarity ADD COLUMN IF NOT EXISTS media_families JSONB",
    ),
    (
        "insights.release_rarity add family_signals",
        "ALTER TABLE insights.release_rarity ADD COLUMN IF NOT EXISTS family_signals JSONB",
    ),
    (
        "insights.release_rarity add medium_rarity",
        "ALTER TABLE insights.release_rarity ADD COLUMN IF NOT EXISTS medium_rarity REAL",
    ),
]


# First-party activity (ADR 0010) — the append-only behavioural record.
#
# Both behavioural tables are range-partitioned by month on `occurred_at`, so
# retention and archival act on whole partitions rather than on row deletes, and
# both are immutable by construction: a BEFORE UPDATE OR DELETE trigger raises
# unless the session-local `groovemap.erasure` setting is on, which only the
# erasure procedure sets.  Events and impressions reference the pseudonymous
# `subject_id` and never the user id, so the behavioural tables can be read and
# joined without carrying account identity, and the link is one row to remove.
#
# Declared after the user-owned tables because `activity.user_subjects` and
# `activity.consent_grants` reference `users(id)`.
_ACTIVITY_STATEMENTS: list[tuple[str, str]] = [
    (
        "activity schema",
        "CREATE SCHEMA IF NOT EXISTS activity",
    ),
    # The pseudonym link.  Removing this single row is what makes an erased
    # subject unre-associable with the account it belonged to.
    (
        "activity.user_subjects table",
        """
        CREATE TABLE IF NOT EXISTS activity.user_subjects (
            user_id    UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            subject_id UUID NOT NULL UNIQUE DEFAULT uuidv7(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    # Per-purpose consent, drawn from exactly the two purposes published in
    # taxonomy/events/v1.  A revocation sets `revoked_at` rather than deleting
    # the grant, so the history stays reconstructible.
    (
        "activity.consent_grants table",
        """
        CREATE TABLE IF NOT EXISTS activity.consent_grants (
            id         UUID PRIMARY KEY DEFAULT uuidv7(),
            user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            purpose    TEXT NOT NULL CHECK (purpose IN ('product_analytics', 'model_training')),
            granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            revoked_at TIMESTAMPTZ
        )
        """,
    ),
    (
        "idx_activity_consent_grants_user_purpose",
        "CREATE INDEX IF NOT EXISTS idx_activity_consent_grants_user_purpose ON activity.consent_grants (user_id, purpose)",
    ),
    # The typed event envelope.  `occurred_at` and `recorded_at` are split so a
    # late or replayed write stays honest, and the idempotency key is scoped to
    # the occurrence time because the uniqueness of a partitioned table must
    # include its partition key.  `consent_purposes` is snapshotted at write, so
    # an old row stays interpretable without reconstructing the grant history.
    (
        "activity.events table",
        """
        CREATE TABLE IF NOT EXISTS activity.events (
            event_id         UUID NOT NULL DEFAULT uuidv7(),
            event_type       TEXT NOT NULL,
            schema_version   SMALLINT NOT NULL,
            subject_id       UUID NOT NULL,
            session_id       UUID,
            occurred_at      TIMESTAMPTZ NOT NULL,
            recorded_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            producer         TEXT NOT NULL,
            consent_purposes TEXT[] NOT NULL,
            model_version    TEXT,
            feature_version  TEXT,
            idempotency_key  TEXT NOT NULL,
            payload          JSONB NOT NULL DEFAULT '{}',
            PRIMARY KEY (occurred_at, event_id),
            UNIQUE (occurred_at, idempotency_key)
        ) PARTITION BY RANGE (occurred_at)
        """,
    ),
    # A DEFAULT partition means an insert never fails for a missing month; the
    # writer calls ensure_month_partition() below to land the row in its own.
    (
        "activity.events_default partition",
        "CREATE TABLE IF NOT EXISTS activity.events_default PARTITION OF activity.events DEFAULT",
    ),
    (
        "idx_activity_events_subject_occurred_at",
        "CREATE INDEX IF NOT EXISTS idx_activity_events_subject_occurred_at ON activity.events (subject_id, occurred_at DESC)",
    ),
    (
        "idx_activity_events_type_occurred_at",
        "CREATE INDEX IF NOT EXISTS idx_activity_events_type_occurred_at ON activity.events (event_type, occurred_at DESC)",
    ),
    # What a shown recommendation needs for later offline evaluation.
    # `policy_id`, `candidate_set_id`, `position`, and `propensity` describe the
    # decision as it was made and cannot be recovered afterwards from the
    # catalog or from the outcome; everything else about an impression can.
    # Outcomes are events carrying the impression id, never columns here, which
    # is what keeps the row immutable while one impression accrues several.
    (
        "activity.impressions table",
        """
        CREATE TABLE IF NOT EXISTS activity.impressions (
            impression_id    UUID NOT NULL DEFAULT uuidv7(),
            subject_id       UUID NOT NULL,
            surface          TEXT NOT NULL,
            policy_id        TEXT NOT NULL,
            candidate_set_id UUID NOT NULL,
            position         INTEGER NOT NULL,
            item_id          UUID NOT NULL,
            score            REAL,
            propensity       REAL,
            request_id       UUID,
            occurred_at      TIMESTAMPTZ NOT NULL,
            recorded_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            consent_purposes TEXT[] NOT NULL,
            PRIMARY KEY (occurred_at, impression_id)
        ) PARTITION BY RANGE (occurred_at)
        """,
    ),
    (
        "activity.impressions_default partition",
        "CREATE TABLE IF NOT EXISTS activity.impressions_default PARTITION OF activity.impressions DEFAULT",
    ),
    (
        "idx_activity_impressions_subject_occurred_at",
        "CREATE INDEX IF NOT EXISTS idx_activity_impressions_subject_occurred_at ON activity.impressions (subject_id, occurred_at DESC)",
    ),
    (
        "idx_activity_impressions_candidate_set_id",
        "CREATE INDEX IF NOT EXISTS idx_activity_impressions_candidate_set_id ON activity.impressions (candidate_set_id)",
    ),
    # Partition creation is owned here and invoked by the writer before insert,
    # so a write into a month that has no partition creates it rather than
    # failing.  The partition name is built with format()'s %I so the identifier
    # is quoted rather than interpolated, and the bounds with %L.
    (
        "activity.ensure_month_partition function",
        """
        CREATE OR REPLACE FUNCTION activity.ensure_month_partition(table_name TEXT, month DATE)
        RETURNS TEXT
        LANGUAGE plpgsql
        AS $ensure_month_partition$
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
        $ensure_month_partition$
        """,
    ),
    # Immutability is a property of the database, not a convention the
    # application is trusted to keep: a mistaken migration or an ad hoc session
    # cannot quietly rewrite history.  Only the erasure procedure sets
    # `groovemap.erasure`, and it is session-local, so the bypass never leaks
    # past the transaction that opened it.
    (
        "activity.reject_mutation function",
        """
        CREATE OR REPLACE FUNCTION activity.reject_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $reject_mutation$
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
        $reject_mutation$
        """,
    ),
    # Row-level triggers declared on a partitioned table apply to every existing
    # partition and are cloned onto partitions created later, so
    # ensure_month_partition() cannot mint an unprotected month.
    (
        "activity.events reject_mutation trigger",
        """
        CREATE OR REPLACE TRIGGER activity_events_reject_mutation
            BEFORE UPDATE OR DELETE ON activity.events
            FOR EACH ROW EXECUTE FUNCTION activity.reject_mutation()
        """,
    ),
    (
        "activity.impressions reject_mutation trigger",
        """
        CREATE OR REPLACE TRIGGER activity_impressions_reject_mutation
            BEFORE UPDATE OR DELETE ON activity.impressions
            FOR EACH ROW EXECUTE FUNCTION activity.reject_mutation()
        """,
    ),
    # The erasure record.  `model_versions_before` names the model versions that
    # had already been trained when the erasure ran: deleting rows does not
    # retrain a model, and naming the affected versions is what makes the
    # residual question answerable rather than invisible.
    (
        "activity.erasures table",
        """
        CREATE TABLE IF NOT EXISTS activity.erasures (
            id                    UUID PRIMARY KEY DEFAULT uuidv7(),
            subject_id            UUID NOT NULL,
            requested_at          TIMESTAMPTZ NOT NULL,
            completed_at          TIMESTAMPTZ,
            events_deleted        BIGINT,
            impressions_deleted   BIGINT,
            model_versions_before TEXT[],
            notes                 JSONB
        )
        """,
    ),
    (
        "idx_activity_erasures_subject_id",
        "CREATE INDEX IF NOT EXISTS idx_activity_erasures_subject_id ON activity.erasures (subject_id)",
    ),
]


# MusicBrainz tables — external music metadata and relationships
# Stores artist, label, and release data from MusicBrainz with cross-references to Discogs IDs.
_MUSICBRAINZ_TABLES: list[tuple[str, str]] = [
    (
        "musicbrainz schema",
        "CREATE SCHEMA IF NOT EXISTS musicbrainz",
    ),
    (
        "musicbrainz.artists table",
        """CREATE TABLE IF NOT EXISTS musicbrainz.artists (
            mbid UUID PRIMARY KEY,
            name TEXT NOT NULL,
            sort_name TEXT,
            type TEXT,
            gender TEXT,
            begin_date TEXT,
            end_date TEXT,
            ended BOOLEAN DEFAULT FALSE,
            area TEXT,
            begin_area TEXT,
            end_area TEXT,
            disambiguation TEXT,
            discogs_artist_id BIGINT,
            aliases JSONB,
            tags JSONB,
            data JSONB,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )""",
    ),
    (
        "musicbrainz.labels table",
        """CREATE TABLE IF NOT EXISTS musicbrainz.labels (
            mbid UUID PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT,
            label_code INTEGER,
            begin_date TEXT,
            end_date TEXT,
            ended BOOLEAN DEFAULT FALSE,
            area TEXT,
            disambiguation TEXT,
            discogs_label_id BIGINT,
            data JSONB,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )""",
    ),
    (
        "musicbrainz.releases table",
        """CREATE TABLE IF NOT EXISTS musicbrainz.releases (
            mbid UUID PRIMARY KEY,
            name TEXT NOT NULL,
            barcode TEXT,
            status TEXT,
            release_group_mbid UUID,
            discogs_release_id BIGINT,
            data JSONB,
            media JSONB,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )""",
    ),
    # Canonical media block (ADR 0007), additive for existing installs.
    (
        "musicbrainz.releases.media column",
        "ALTER TABLE musicbrainz.releases ADD COLUMN IF NOT EXISTS media JSONB",
    ),
    (
        "musicbrainz.release_groups table",
        """CREATE TABLE IF NOT EXISTS musicbrainz.release_groups (
            mbid UUID PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT,
            secondary_types JSONB,
            first_release_date TEXT,
            disambiguation TEXT,
            discogs_master_id BIGINT,
            data JSONB,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )""",
    ),
    (
        "musicbrainz.relationships table",
        """CREATE TABLE IF NOT EXISTS musicbrainz.relationships (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            source_mbid UUID NOT NULL,
            target_mbid UUID NOT NULL,
            source_entity_type TEXT NOT NULL,
            target_entity_type TEXT NOT NULL,
            relationship_type TEXT NOT NULL,
            begin_date TEXT,
            end_date TEXT,
            ended BOOLEAN DEFAULT FALSE,
            attributes JSONB,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT relationships_natural_key UNIQUE NULLS NOT DISTINCT (
                source_mbid, target_mbid, source_entity_type, target_entity_type,
                relationship_type, begin_date, end_date, attributes
            )
        )""",
    ),
    # Widen the relationships natural key from (source_mbid, target_mbid,
    # source_entity_type, target_entity_type, relationship_type) to also include
    # begin_date/end_date/attributes. The narrower key let two genuinely distinct
    # MusicBrainz relationship instances that only differ by date range (e.g. a
    # re-joined band membership) or attributes (e.g. a multi-instrument performer
    # credit) collapse into one row: the second INSERT's ON CONFLICT DO UPDATE
    # silently overwrote the first instance instead of coexisting as a separate
    # row. CREATE TABLE IF NOT EXISTS above won't alter a
    # pre-existing table, so migrate the constraint explicitly. The old
    # constraint's name is autogenerated by Postgres, so it is located by its
    # column set rather than a hardcoded name.
    (
        "musicbrainz.relationships widen natural key to include begin_date/end_date/attributes",
        """DO $mb_rel_key$
        DECLARE
            old_name TEXT;
        BEGIN
            SELECT c.conname INTO old_name
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = 'musicbrainz'
              AND t.relname = 'relationships'
              AND c.contype = 'u'
              AND c.conname <> 'relationships_natural_key'
              AND c.conkey = ARRAY(
                  SELECT a.attnum
                  FROM pg_attribute a
                  WHERE a.attrelid = t.oid
                    AND a.attname IN (
                        'source_mbid', 'target_mbid', 'source_entity_type',
                        'target_entity_type', 'relationship_type'
                    )
                  ORDER BY a.attnum
              );

            IF old_name IS NOT NULL THEN
                EXECUTE format('ALTER TABLE musicbrainz.relationships DROP CONSTRAINT IF EXISTS %I', old_name);
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE n.nspname = 'musicbrainz'
                  AND t.relname = 'relationships'
                  AND c.conname = 'relationships_natural_key'
            ) THEN
                ALTER TABLE musicbrainz.relationships
                    ADD CONSTRAINT relationships_natural_key
                    UNIQUE NULLS NOT DISTINCT (
                        source_mbid, target_mbid, source_entity_type, target_entity_type,
                        relationship_type, begin_date, end_date, attributes
                    );
            END IF;
        END
        $mb_rel_key$""",
    ),
    (
        "musicbrainz.external_links table",
        """CREATE TABLE IF NOT EXISTS musicbrainz.external_links (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            mbid UUID NOT NULL,
            entity_type TEXT NOT NULL,
            service_name TEXT NOT NULL,
            url TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (mbid, entity_type, service_name, url)
        )""",
    ),
    # Widen Discogs cross-reference IDs from INTEGER (int4, max 2,147,483,647)
    # to BIGINT. Discogs IDs are emitted as i64 by the extractor and release IDs
    # now exceed 2.1B, causing "integer out of range" on INSERT. CREATE TABLE
    # IF NOT EXISTS above won't alter pre-existing tables, so widen explicitly.
    # ALTER COLUMN ... TYPE BIGINT is a no-op when the column is already BIGINT.
    # Native identity (ADR 0009): additive, nullable native catalog item on each
    # MusicBrainz entity table, so a second catalog becomes more aliases rather
    # than a parallel schema joined by convention at read time.
    (
        "musicbrainz.artists.gm_item_id column",
        "ALTER TABLE musicbrainz.artists ADD COLUMN IF NOT EXISTS gm_item_id UUID",
    ),
    (
        "musicbrainz.labels.gm_item_id column",
        "ALTER TABLE musicbrainz.labels ADD COLUMN IF NOT EXISTS gm_item_id UUID",
    ),
    (
        "musicbrainz.releases.gm_item_id column",
        "ALTER TABLE musicbrainz.releases ADD COLUMN IF NOT EXISTS gm_item_id UUID",
    ),
    (
        "musicbrainz.release_groups.gm_item_id column",
        "ALTER TABLE musicbrainz.release_groups ADD COLUMN IF NOT EXISTS gm_item_id UUID",
    ),
    (
        "musicbrainz.artists.discogs_artist_id widen to BIGINT",
        "ALTER TABLE musicbrainz.artists ALTER COLUMN discogs_artist_id TYPE BIGINT",
    ),
    (
        "musicbrainz.labels.discogs_label_id widen to BIGINT",
        "ALTER TABLE musicbrainz.labels ALTER COLUMN discogs_label_id TYPE BIGINT",
    ),
    (
        "musicbrainz.releases.discogs_release_id widen to BIGINT",
        "ALTER TABLE musicbrainz.releases ALTER COLUMN discogs_release_id TYPE BIGINT",
    ),
    (
        "musicbrainz.release_groups.discogs_master_id widen to BIGINT",
        "ALTER TABLE musicbrainz.release_groups ALTER COLUMN discogs_master_id TYPE BIGINT",
    ),
]


# MusicBrainz indexes — optimized queries for cross-database lookups
_MUSICBRAINZ_INDEXES: list[tuple[str, str]] = [
    (
        "idx_mb_artists_discogs_id",
        "CREATE INDEX IF NOT EXISTS idx_mb_artists_discogs_id ON musicbrainz.artists (discogs_artist_id) WHERE discogs_artist_id IS NOT NULL",
    ),
    (
        "idx_mb_labels_discogs_id",
        "CREATE INDEX IF NOT EXISTS idx_mb_labels_discogs_id ON musicbrainz.labels (discogs_label_id) WHERE discogs_label_id IS NOT NULL",
    ),
    (
        "idx_mb_releases_discogs_id",
        "CREATE INDEX IF NOT EXISTS idx_mb_releases_discogs_id ON musicbrainz.releases (discogs_release_id) WHERE discogs_release_id IS NOT NULL",
    ),
    (
        "idx_mb_releases_media_families",
        "CREATE INDEX IF NOT EXISTS idx_mb_releases_media_families ON musicbrainz.releases USING GIN ((media->'families'))",
    ),
    (
        "idx_mb_release_groups_discogs_id",
        "CREATE INDEX IF NOT EXISTS idx_mb_release_groups_discogs_id ON musicbrainz.release_groups (discogs_master_id) WHERE discogs_master_id IS NOT NULL",
    ),
    (
        "idx_mb_artists_name",
        "CREATE INDEX IF NOT EXISTS idx_mb_artists_name ON musicbrainz.artists (name)",
    ),
    (
        "idx_mb_labels_name",
        "CREATE INDEX IF NOT EXISTS idx_mb_labels_name ON musicbrainz.labels (name)",
    ),
    (
        "idx_mb_rels_source",
        "CREATE INDEX IF NOT EXISTS idx_mb_rels_source ON musicbrainz.relationships (source_mbid)",
    ),
    (
        "idx_mb_rels_target",
        "CREATE INDEX IF NOT EXISTS idx_mb_rels_target ON musicbrainz.relationships (target_mbid)",
    ),
    (
        "idx_mb_rels_type",
        "CREATE INDEX IF NOT EXISTS idx_mb_rels_type ON musicbrainz.relationships (relationship_type)",
    ),
    (
        "idx_mb_links_mbid",
        "CREATE INDEX IF NOT EXISTS idx_mb_links_mbid ON musicbrainz.external_links (mbid)",
    ),
    (
        "idx_mb_links_service",
        "CREATE INDEX IF NOT EXISTS idx_mb_links_service ON musicbrainz.external_links (service_name)",
    ),
    (
        "idx_mb_artists_gm_item_id",
        "CREATE INDEX IF NOT EXISTS idx_mb_artists_gm_item_id ON musicbrainz.artists (gm_item_id)",
    ),
    (
        "idx_mb_labels_gm_item_id",
        "CREATE INDEX IF NOT EXISTS idx_mb_labels_gm_item_id ON musicbrainz.labels (gm_item_id)",
    ),
    (
        "idx_mb_releases_gm_item_id",
        "CREATE INDEX IF NOT EXISTS idx_mb_releases_gm_item_id ON musicbrainz.releases (gm_item_id)",
    ),
    (
        "idx_mb_release_groups_gm_item_id",
        "CREATE INDEX IF NOT EXISTS idx_mb_release_groups_gm_item_id ON musicbrainz.release_groups (gm_item_id)",
    ),
]


def _entity_schema_statements() -> Iterator[tuple[str, Any]]:
    """Yield each Discogs entity table followed by its shared indexes."""
    for table_name in _ENTITY_TABLES:
        yield (
            f"{table_name} table",
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {table} (
                    data_id    VARCHAR PRIMARY KEY,
                    hash       VARCHAR NOT NULL,
                    data       JSONB   NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            ).format(table=sql.Identifier(table_name)),
        )
        for column, template in (
            ("hash", "CREATE INDEX IF NOT EXISTS {index} ON {table} (hash)"),
            ("updated_at", "CREATE INDEX IF NOT EXISTS {index} ON {table} (updated_at)"),
        ):
            index_name = f"idx_{table_name}_{column}"
            yield (
                index_name,
                sql.SQL(template).format(
                    index=sql.Identifier(index_name),
                    table=sql.Identifier(table_name),
                ),
            )
        # Native identity (ADR 0009): every provider-keyed entity row carries the
        # native catalog item the loader minted for it, as an additive nullable
        # column beside the Discogs `data_id` that remains the primary key.
        yield (
            f"{table_name} add gm_item_id column",
            sql.SQL("ALTER TABLE {table} ADD COLUMN IF NOT EXISTS gm_item_id UUID").format(table=sql.Identifier(table_name)),
        )
        gm_index_name = f"idx_{table_name}_gm_item_id"
        yield (
            gm_index_name,
            sql.SQL("CREATE INDEX IF NOT EXISTS {index} ON {table} (gm_item_id)").format(
                index=sql.Identifier(gm_index_name),
                table=sql.Identifier(table_name),
            ),
        )


def _schema_statements() -> Iterator[tuple[str, Any]]:
    """Yield the complete PostgreSQL schema in compatibility-sensitive order."""
    yield from _entity_schema_statements()
    yield from _SPECIFIC_INDEXES
    yield from _USER_TABLES
    yield from _INSIGHTS_TABLES
    yield from _ACTIVITY_STATEMENTS
    yield from _MUSICBRAINZ_TABLES
    yield from _MUSICBRAINZ_INDEXES


async def _execute_schema_statements(cursor: Any, statements: Iterable[tuple[str, Any]]) -> tuple[int, int]:
    """Execute every statement, returning success and failure counts."""
    success_count = 0
    failure_count = 0
    for name, statement in statements:
        try:
            await cursor.execute(statement)
            logger.info("✅ Schema: %s", name)
            success_count += 1
        except Exception as error:
            logger.error("❌ Failed to create schema object '%s': %s", name, error)
            failure_count += 1
    return success_count, failure_count


async def create_postgres_schema(pool: Any) -> int:
    """Create all PostgreSQL tables and indexes.

    Safe to call on every startup; all statements use IF NOT EXISTS so
    subsequent calls are no-ops for already-created schema objects.

    Args:
        pool: An AsyncPostgreSQLPool instance (from common.postgres_resilient).

    Returns:
        Number of failed schema statements (0 means all succeeded).
    """
    logger.info("🔧 Creating PostgreSQL schema (tables and indexes)...")

    async with pool.connection() as conn:
        await conn.set_autocommit(True)
        # psycopg async cursor types are not fully inferred by mypy
        async with conn.cursor() as cursor_cm:
            cursor = cast("Any", cursor_cm)
            statements = list(_schema_statements())
            success_count, failure_count = await _execute_schema_statements(cursor, statements)

    total = len(statements)
    logger.info(f"✅ PostgreSQL schema creation complete: {success_count} succeeded, {failure_count} failed (total: {total})")
    return failure_count
