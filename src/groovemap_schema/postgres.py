"""PostgreSQL schema definitions for GrooveMap.

Single source of truth for all PostgreSQL tables and indexes.
All statements use IF NOT EXISTS — safe to run on every startup; subsequent
runs are no-ops for already-created schema objects. Schema is never dropped.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from common.credit_roles import ROLE_CATEGORIES
from common.media import medium_ids, medium_label
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
    # Catalog identifiers and manufacturing credits (ADR 0011) — GIN indexes on
    # the additive identifiers/companies blocks for containment queries. Exact
    # lookup (barcode, catalogue number) resolves through provider_aliases
    # instead; these serve analytical containment queries.
    (
        "idx_releases_identifiers",
        "CREATE INDEX IF NOT EXISTS idx_releases_identifiers ON releases USING GIN ((data->'identifiers'))",
    ),
    (
        "idx_releases_companies",
        "CREATE INDEX IF NOT EXISTS idx_releases_companies ON releases USING GIN ((data->'companies'))",
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
        "insights.activity_summary table",
        """
        CREATE TABLE IF NOT EXISTS insights.activity_summary (
            summary_date        DATE NOT NULL,
            dimension           TEXT NOT NULL CHECK (dimension IN ('event_type', 'policy_id')),
            dimension_key       TEXT NOT NULL,
            record_count        BIGINT NOT NULL,
            subject_count       BIGINT NOT NULL,
            candidate_set_count BIGINT,
            computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (summary_date, dimension, dimension_key)
        )
        """,
    ),
    (
        "idx_activity_summary_date",
        "CREATE INDEX IF NOT EXISTS idx_activity_summary_date ON insights.activity_summary (summary_date DESC)",
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
def _widen_to_bigint(table: str, column: str) -> str:
    """Return a re-runnable widening of one MusicBrainz provider-id column.

    A bare `ALTER COLUMN ... TYPE BIGINT` is only idempotent while nothing
    depends on the column: PostgreSQL refuses to retype a column a view reads,
    even when the requested type is the one it already has. The `graph` schema
    exposes exactly these columns as the bridge between the MusicBrainz and
    Discogs halves of the property graph, so the rewrite is gated twice — on
    the column still being narrow, and on no view depending on it.

    The second gate is not theoretical. `_execute_schema_statements` logs a
    failing statement and continues, so an install whose column was still
    `integer` when a single earlier run failed transiently goes on to create
    the graph views in that same run. From then on the column is narrow *and*
    depended upon, and an unguarded ALTER raises `cannot alter type of a column
    used by a view or rule` on every subsequent startup — a permanent, noisy
    failure this path can never clear. Reaching that state is a migration
    problem, not a startup problem: retyping a column under a view needs a
    coordinated DROP ... CASCADE under the persistence contract's
    expand/migrate/contract rule. So the statement says so once, as a NOTICE,
    and succeeds.
    """
    qualified = f"musicbrainz.{table}"
    return f"""
        DO $widen$
        DECLARE
            current_type    text;
            dependent_views text;
        BEGIN
            SELECT data_type INTO current_type
            FROM information_schema.columns
            WHERE table_schema = 'musicbrainz'
              AND table_name = '{table}'
              AND column_name = '{column}';

            -- Absent (a fresh install has not created the table yet) or already
            -- wide: nothing to do, and nothing to say about it.
            IF current_type IS NULL OR current_type = 'bigint' THEN
                RETURN;
            END IF;

            -- A view reads the column through a rewrite rule, so pg_depend
            -- records the dependency from the rule to this exact attribute.
            SELECT string_agg(view_name, ', ' ORDER BY view_name)
            INTO dependent_views
            FROM (
                SELECT DISTINCT
                    dependent.relnamespace::regnamespace::text || '.' || dependent.relname AS view_name
                FROM pg_depend AS dependency
                JOIN pg_rewrite AS rule ON rule.oid = dependency.objid
                JOIN pg_class AS dependent ON dependent.oid = rule.ev_class
                WHERE dependency.classid = 'pg_rewrite'::regclass
                  AND dependency.refclassid = 'pg_class'::regclass
                  AND dependency.refobjid = '{qualified}'::regclass
                  AND dependency.refobjsubid = (
                      SELECT attribute.attnum
                      FROM pg_attribute AS attribute
                      WHERE attribute.attrelid = '{qualified}'::regclass
                        AND attribute.attname = '{column}'
                  )
                  AND dependent.relkind IN ('v', 'm')
                  AND dependent.oid <> '{qualified}'::regclass
            ) AS dependents;

            IF dependent_views IS NOT NULL THEN
                RAISE NOTICE
                    'skipping widen of {qualified}.{column}: still %, and % reads it. '
                    'Retyping a column a view depends on needs a coordinated '
                    'migration that recreates the dependent views, not a startup ALTER.',
                    current_type, dependent_views;
                RETURN;
            END IF;

            ALTER TABLE {qualified} ALTER COLUMN {column} TYPE BIGINT;
        END
        $widen$
        """  # noqa: S608 — `table` and `column` are module constants, never input


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
        _widen_to_bigint("artists", "discogs_artist_id"),
    ),
    (
        "musicbrainz.labels.discogs_label_id widen to BIGINT",
        _widen_to_bigint("labels", "discogs_label_id"),
    ),
    (
        "musicbrainz.releases.discogs_release_id widen to BIGINT",
        _widen_to_bigint("releases", "discogs_release_id"),
    ),
    (
        "musicbrainz.release_groups.discogs_master_id widen to BIGINT",
        _widen_to_bigint("release_groups", "discogs_master_id"),
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
    # The sixteen `graph.mb_rel_<source>_<target>` views each filter on their own
    # ordered endpoint-type pair and inner-join both endpoint tables, and until
    # now nothing indexed that filter. The natural key leads with the source
    # identifier, so it is a uniqueness constraint rather than a useful access
    # path for the pair. Both directions are indexed because a relationship is
    # reached from its target as often as from its source.
    (
        "idx_mb_rels_endpoint_source",
        "CREATE INDEX IF NOT EXISTS idx_mb_rels_endpoint_source ON musicbrainz.relationships (source_entity_type, target_entity_type, source_mbid)",
    ),
    (
        "idx_mb_rels_endpoint_target",
        "CREATE INDEX IF NOT EXISTS idx_mb_rels_endpoint_target ON musicbrainz.relationships (source_entity_type, target_entity_type, target_mbid)",
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


# ── Graph schema (vertex and edge views) ──────────────────────────────────────
# The `graph` schema re-presents the catalog tables as the vertex and edge
# relations of the property graph the Neo4j enrichers already build, without
# copying a byte: every object here is a view over a table defined above.
#
# Naming is the contract. A vertex view is named for the Neo4j label it mirrors
# (`graph.artist` for `:Artist`) and an edge view for the relationship type
# (`graph.by_artist` for `[:BY]`), lowercased and de-reserved — `:User` becomes
# `graph.app_user`, the label ADR 0012 records for it, because `user` is a
# reserved word, and `[:BY]`, `[:ON]` and `[:IS]` become `by_artist`,
# `on_label`, `in_genre` and `in_style` so a later CREATE PROPERTY GRAPH can
# use the view name as the label verbatim. Where ADR 0012 names a label, its
# mapping table is the contract and this schema follows it.
#
# Every edge view exposes a stable key column set, catalogued in
# docs/architecture.md, plus the source and target key columns that join to the
# corresponding vertex views. Discogs ids live inside JSONB documents as numbers
# while the catalog tables key on `data_id VARCHAR`, so every id is read with
# `->>` and compared as text. Base tables are schema-qualified so a view's
# meaning does not depend on the search_path in force when it was created.
#
# Several bodies are composed with f-strings and carry an S608 suppression. Every
# interpolated value is a module-level constant — a table name, a JSON key, a
# fragment built by the helpers below — and none of it reaches this module from
# a caller, a request, or a row. There is no runtime input to inject.
_GRAPH_SCHEMA_STATEMENT = ("graph schema", "CREATE SCHEMA IF NOT EXISTS graph")

# Trigram search is what the six full-text query functions the coverage spike
# found need, and it is one of the two reasons the name-keyed vertex relations
# had to stop being views. Creating the extension can need a privilege a
# hardened deployment withholds, so every index that uses it is guarded on the
# extension being present rather than on this statement having succeeded: the
# worst case is a deployment without trigram search, not a deployment without a
# schema.
_PG_TRGM_STATEMENT = ("pg_trgm extension", "CREATE EXTENSION IF NOT EXISTS pg_trgm")


def _jsonb_array(expression: str) -> str:
    """Return the JSONB array at EXPRESSION, or an empty array when it is not one.

    Discogs documents are not schema-checked, so `data->'artists'` can be absent,
    null, or — for a malformed record — a scalar. `jsonb_array_elements` raises on
    all three, which would take down a whole view rather than skip one row, so
    every unnest in this schema goes through this guard.
    """
    return f"CASE WHEN jsonb_typeof({expression}) = 'array' THEN {expression} ELSE '[]'::jsonb END"


def _text_array(expression: str) -> str:
    """Return the JSONB string array at EXPRESSION as `text[]`, empty when absent.

    The guard is the subquery's own WHERE rather than a surrounding CASE: with no
    row qualifying, the set-returning function in the target list is never
    reached, and `ARRAY()` over zero rows is an empty array rather than NULL.
    """
    return f"ARRAY(SELECT jsonb_array_elements_text({expression}) WHERE jsonb_typeof({expression}) = 'array')"


def _usable_id(expression: str) -> str:
    """Return the predicate keeping the ids the graph enricher keeps.

    `graphinator` drops every array element whose `id` is falsy, so a missing id
    and the Discogs "no entity" sentinel `0` both drop the element rather than
    producing an edge to a vertex that does not exist.
    """
    return f"NULLIF(btrim({expression}), '') IS NOT NULL AND btrim({expression}) <> '0'"


def _non_empty(expression: str) -> str:
    """Return the predicate keeping a non-null, non-empty text value."""
    return f"NULLIF({expression}, '') IS NOT NULL"


def _view(name: str, body: str) -> tuple[str, str]:
    """Return the named CREATE OR REPLACE VIEW statement for one graph relation.

    CREATE OR REPLACE is the idempotency spelling PostgreSQL offers for a view:
    re-running it against an identical definition is a no-op, and nothing here
    ever drops a relation a consumer may be reading.
    """
    return (f"graph.{name} view", f"CREATE OR REPLACE VIEW graph.{name} AS\n{body.strip()}")


# Genre and Style vertices are the distinct tag names across both documents that
# carry them; the enricher MERGEs the same node from a release and from a master.
_TAGGED_DOCUMENTS = """
    SELECT releases.data AS document FROM public.releases AS releases
    UNION ALL
    SELECT masters.data AS document FROM public.masters AS masters
"""


# The native catalog identity ADR 0009 mints, read from the one table
# catalog-api's `gm_id` projection job reads. `provider_aliases` carries a
# partial UNIQUE index on `(provider, entity_kind, external_id) WHERE valid_to
# IS NULL`, so this is a single index probe per entity row and cannot turn one
# vertex into two. A Discogs id is stringified into `external_id`, which is what
# makes the comparison against `data_id` a text comparison rather than a cast.
_NATIVE_IDENTITY_JOIN = """
LEFT JOIN public.provider_aliases AS alias
       ON alias.provider = 'discogs'
      AND alias.entity_kind = '{kind}'
      AND alias.external_id = {table}.data_id
      AND alias.valid_to IS NULL
"""


def _native_identity_join(table: str, kind: str) -> str:
    """Return the LEFT JOIN exposing one entity table's native `gm_id`."""
    return _NATIVE_IDENTITY_JOIN.format(table=table, kind=kind).strip()


# `Release.formats` is read by six catalog-api functions and is what
# `graphinator` flattens out of `data->'formats'[].name`. The same array-typed
# guard the tag arrays use applies, because a malformed document must skip a row
# rather than fail the view.
_FORMAT_NAMES = f"""ARRAY(SELECT format.value ->> 'name'
             FROM jsonb_array_elements({_jsonb_array("releases.data -> 'formats'")}) AS format(value)
             WHERE {_non_empty("btrim(format.value ->> 'name')")})"""  # noqa: S608

# `Release.catalog_number` is written twice in the graph today — by `graphinator`
# from `labels[0].catno` and again by catalog-api's syncer — and neither
# `user_collections` nor `user_wantlists` has a column for it. The Discogs copy
# is the one with a relational home. Subscripting a non-array by an integer
# yields NULL, so a flattened `labels` block contributes nothing rather than
# raising.
_CATALOG_NUMBER = "NULLIF(btrim(releases.data -> 'labels' -> 0 ->> 'catno'), '')"


def _discogs_vertex_views() -> list[tuple[str, str]]:
    """Return the four vertex views over the Discogs entity tables.

    Each publishes its key as `text` rather than the `character varying` the
    catalog table keys on, because PostgreSQL 19 cannot resolve an equality
    operator for a `character varying` property-graph vertex key. That retires
    the appended `<entity>_key` restatements the phase 0 views carried for the
    same reason: with the published key already `text` there is nothing left for
    a second column to restate. `_key_retype_migration` is what lets the
    republished view land on a database that still holds the old shape.

    Each also appends `gm_id`, the native catalog identity ADR 0009 mints. It is
    read from `provider_aliases` — the one table catalog-api's `gm_id`
    projection job reads — through the partial unique index on
    `(provider, entity_kind, external_id) WHERE valid_to IS NULL`, so the join
    is a single index probe and cannot multiply a row. Exposing it here is what
    makes the projection job's cross-store copy unnecessary: a consumer that
    wants the native id of a Discogs entity reads it off the vertex.

    `graph.genre` and `graph.style` are no longer here. They are tables now; see
    `_vertex_table_statements`.
    """
    return [
        _view(
            "artist",
            f"""
SELECT artists.data_id::text   AS artist_id,
       artists.data ->> 'name' AS name,
       artists.gm_item_id      AS gm_item_id,
       artists.hash            AS hash,
       artists.updated_at      AS updated_at,
       alias.native_id         AS gm_id
FROM public.artists AS artists
{_native_identity_join("artists", "artist")}
""",  # noqa: S608
        ),
        _view(
            "label",
            f"""
SELECT labels.data_id::text   AS label_id,
       labels.data ->> 'name' AS name,
       labels.gm_item_id      AS gm_item_id,
       labels.hash            AS hash,
       labels.updated_at      AS updated_at,
       alias.native_id        AS gm_id
FROM public.labels AS labels
{_native_identity_join("labels", "label")}
""",  # noqa: S608
        ),
        _view(
            "master",
            f"""
SELECT masters.data_id::text    AS master_id,
       masters.data ->> 'title' AS title,
       masters.data ->> 'year'  AS year,
       {_text_array("masters.data -> 'genres'")} AS genres,
       {_text_array("masters.data -> 'styles'")} AS styles,
       masters.gm_item_id       AS gm_item_id,
       masters.hash             AS hash,
       masters.updated_at       AS updated_at,
       alias.native_id          AS gm_id
FROM public.masters AS masters
{_native_identity_join("masters", "master")}
""",  # noqa: S608
        ),
        _view(
            "release",
            f"""
SELECT releases.data_id::text    AS release_id,
       releases.data ->> 'title' AS title,
       releases.data ->> 'year'  AS year,
       NULLIF(btrim(releases.data ->> 'country'), '') AS country,
       {_text_array("releases.data -> 'genres'")} AS genres,
       {_text_array("releases.data -> 'styles'")} AS styles,
       {_text_array("releases.media -> 'families'")} AS media_families,
       releases.gm_item_id       AS gm_item_id,
       releases.hash             AS hash,
       releases.updated_at       AS updated_at,
       {_FORMAT_NAMES}           AS formats,
       {_CATALOG_NUMBER}         AS catalog_number,
       alias.native_id           AS gm_id
FROM public.releases AS releases
{_native_identity_join("releases", "release")}
""",  # noqa: S608
        ),
    ]


def _discogs_edge_views() -> list[tuple[str, str]]:
    """Return the two Discogs edge relations that stay views.

    The other thirteen are tables now; see `_EDGE_TABLES`. These two are the
    ones Table 2 of the coverage spike keeps as views, and for the same reason
    in both cases: the relation is bounded by a taxonomy rather than by the
    catalog. `part_of` is bounded by 757 styles times 16 genres and `sublabel_of`
    is not read by anything in `api/queries/` at all, so neither pays for a
    write path. Table 2 says to materialize `sublabel_of` only when a caller
    appears, and none has.

    Both now inner-join the vertex tables their endpoints resolve to, which is
    the discipline the MusicBrainz relationship views already follow: an edge
    appears once both of its endpoints are loaded, and never points at a vertex
    row that is not there. `sublabel_of` is the exception — both its endpoints
    are `public.labels`, which is a real table the loader writes directly, so
    there is nothing to wait for.
    """
    genres = "source.document -> 'genres'"
    styles = "source.document -> 'styles'"
    return [
        # PART_OF is unambiguous only when the source record carries exactly one
        # genre: with two, nothing in the document says which genre a style sits
        # under. Release and master documents both assert it and the enricher
        # projects both, so both are unioned here. The single-genre guard is
        # carried over verbatim from the phase 0 view — it is the whole
        # correctness argument for the relation and materializing the two
        # endpoint vertices around it must not weaken it.
        _view(
            "part_of",
            f"""
SELECT asserted.style_name AS style_name,
       asserted.genre_name AS genre_name
FROM (
    SELECT DISTINCT style.value   AS style_name,
           genre.genre_name       AS genre_name
    FROM ({_TAGGED_DOCUMENTS.strip()}) AS source
    CROSS JOIN LATERAL (
        SELECT single.value AS genre_name
        FROM jsonb_array_elements_text({_jsonb_array(genres)}) AS single(value)
    ) AS genre
    CROSS JOIN LATERAL jsonb_array_elements_text({_jsonb_array(styles)}) AS style(value)
    WHERE jsonb_array_length({_jsonb_array(genres)}) = 1
      AND {_non_empty("genre.genre_name")}
      AND {_non_empty("style.value")}
) AS asserted
JOIN graph.style AS style ON style.name = asserted.style_name
JOIN graph.genre AS genre ON genre.name = asserted.genre_name
""",  # noqa: S608
        ),
        # A label hierarchy is stated from both ends: `parentLabel` on the child
        # and `sublabels` on the parent.
        _view(
            "sublabel_of",
            f"""
SELECT labels.data_id::text                            AS sublabel_id,
       btrim(labels.data -> 'parentLabel' ->> 'id')    AS parent_label_id
FROM public.labels AS labels
WHERE {_usable_id("labels.data -> 'parentLabel' ->> 'id'")}
UNION
SELECT btrim(element.value ->> 'id') AS sublabel_id,
       labels.data_id::text          AS parent_label_id
FROM public.labels AS labels
CROSS JOIN LATERAL jsonb_array_elements({_jsonb_array("labels.data -> 'sublabels'")}) AS element(value)
WHERE {_usable_id("element.value ->> 'id'")}
""",  # noqa: S608
        ),
    ]


# The MusicBrainz entity types the loader writes into `musicbrainz.relationships`,
# each paired with the table keyed on that type's mbid and the fragment used in
# view names. `release-group` is the spelling the loader normalizes onto, so it is
# matched verbatim and spelled `release_group` in identifiers.
_MUSICBRAINZ_GRAPH_ENTITIES: list[tuple[str, str, str]] = [
    ("artist", "musicbrainz.artists", "artist"),
    ("label", "musicbrainz.labels", "label"),
    ("release", "musicbrainz.releases", "release"),
    ("release-group", "musicbrainz.release_groups", "release_group"),
]


def _musicbrainz_vertex_views() -> list[tuple[str, str]]:
    """Return the vertex views over the four MusicBrainz catalog tables."""
    return [
        _view(
            "mb_artist",
            """
SELECT artists.mbid              AS mbid,
       artists.name              AS name,
       artists.sort_name         AS sort_name,
       artists.type              AS type,
       artists.gender            AS gender,
       artists.begin_date        AS begin_date,
       artists.end_date          AS end_date,
       artists.ended             AS ended,
       artists.area              AS area,
       artists.begin_area        AS begin_area,
       artists.end_area          AS end_area,
       artists.disambiguation    AS disambiguation,
       artists.discogs_artist_id AS discogs_artist_id,
       artists.updated_at        AS updated_at
FROM musicbrainz.artists AS artists
""",
        ),
        _view(
            "mb_label",
            """
SELECT labels.mbid             AS mbid,
       labels.name             AS name,
       labels.type             AS type,
       labels.label_code       AS label_code,
       labels.begin_date       AS begin_date,
       labels.end_date         AS end_date,
       labels.ended            AS ended,
       labels.area             AS area,
       labels.disambiguation   AS disambiguation,
       labels.discogs_label_id AS discogs_label_id,
       labels.updated_at       AS updated_at
FROM musicbrainz.labels AS labels
""",
        ),
        _view(
            "mb_release",
            f"""
SELECT releases.mbid               AS mbid,
       releases.name               AS name,
       releases.barcode            AS barcode,
       releases.status             AS status,
       releases.release_group_mbid AS release_group_mbid,
       releases.discogs_release_id AS discogs_release_id,
       {_text_array("releases.media -> 'families'")} AS media_families,
       releases.updated_at         AS updated_at
FROM musicbrainz.releases AS releases
""",  # noqa: S608
        ),
        _view(
            "mb_release_group",
            """
SELECT release_groups.mbid               AS mbid,
       release_groups.name               AS name,
       release_groups.type               AS type,
       release_groups.secondary_types    AS secondary_types,
       release_groups.first_release_date AS first_release_date,
       release_groups.disambiguation     AS disambiguation,
       release_groups.discogs_master_id  AS discogs_master_id,
       release_groups.updated_at         AS updated_at
FROM musicbrainz.release_groups AS release_groups
""",
        ),
    ]


def _musicbrainz_edge_views() -> list[tuple[str, str]]:
    """Return one edge view per ordered MusicBrainz endpoint-type pair.

    `musicbrainz.relationships` is polymorphic — one table holding every
    (source type, target type) combination — and carries no foreign keys, so a
    row can name an mbid the loader has not stored yet. A property graph needs
    the opposite: one typed edge relation per endpoint pair, every row of which
    resolves to a vertex. Each view therefore filters on its pair and inner-joins
    both endpoint tables, which is what drops the dangling rows.

    All sixteen ordered pairs over the four modelled entity types are declared
    rather than only the pairs some catalog happens to hold today, so the set of
    relations is a property of the schema and not of the data loaded into it.

    `relationship_type` publishes the Neo4j type name, so a query ported from
    Cypher reads the vocabulary it was written against; `raw_relationship_type`
    is appended after it and publishes the MusicBrainz string the loader stored.
    Both are text and both are present on all sixteen views, which is what lets
    them keep sharing one `mb_related` label.
    """
    views: list[tuple[str, str]] = []
    for source_type, source_table, source_name in _MUSICBRAINZ_GRAPH_ENTITIES:
        for target_type, target_table, target_name in _MUSICBRAINZ_GRAPH_ENTITIES:
            views.append(
                _view(
                    f"mb_rel_{source_name}_{target_name}",
                    f"""
SELECT relationship.id                AS relationship_id,
       relationship.source_mbid       AS source_mbid,
       relationship.target_mbid       AS target_mbid,
       graph.mb_relationship_type(relationship.relationship_type) AS relationship_type,
       relationship.begin_date        AS begin_date,
       relationship.end_date          AS end_date,
       relationship.ended             AS ended,
       relationship.attributes        AS attributes,
       relationship.relationship_type AS raw_relationship_type
FROM musicbrainz.relationships AS relationship
JOIN {source_table} AS source_entity ON source_entity.mbid = relationship.source_mbid
JOIN {target_table} AS target_entity ON target_entity.mbid = relationship.target_mbid
WHERE relationship.source_entity_type = '{source_type}'
  AND relationship.target_entity_type = '{target_type}'
""",  # noqa: S608
                )
            )
    return views


def _collection_views() -> list[tuple[str, str]]:
    """Return the account, catalog-item, and personal-collection graph relations.

    `user_collections` and `user_wantlists` hold the raw Discogs release id as a
    BIGINT and carry no foreign key to `releases`, so both edge views cast it to
    text and inner-join the catalog. That cast is the join the whole graph turns
    on: `releases.data_id` is the Discogs id as a string.

    `graph.app_user` is named for the vertex label ADR 0012 records for the Neo4j
    `:User` node; `user` itself is reserved. It deliberately omits `email` and
    every credential column. The `:User` node carries only an id, and a graph
    relation is the wrong surface on which to widen personal data.
    """
    return [
        _view(
            "app_user",
            """
SELECT users.id         AS user_id,
       users.is_active  AS is_active,
       users.is_admin   AS is_admin,
       users.created_at AS created_at,
       users.updated_at AS updated_at
FROM public.users AS users
""",
        ),
        _view(
            "catalog_item",
            """
SELECT catalog_items.id         AS item_id,
       catalog_items.kind       AS kind,
       catalog_items.created_at AS created_at
FROM public.catalog_items AS catalog_items
""",
        ),
        _view(
            "collected",
            """
SELECT collection.id          AS collection_id,
       collection.user_id     AS user_id,
       releases.data_id       AS release_id,
       collection.instance_id AS instance_id,
       collection.folder_id   AS folder_id,
       collection.condition   AS condition,
       collection.rating      AS rating,
       collection.date_added  AS date_added
FROM public.user_collections AS collection
JOIN public.releases AS releases ON releases.data_id = collection.release_id::text
""",
        ),
        _view(
            "wants",
            """
SELECT wantlist.id         AS wantlist_id,
       wantlist.user_id    AS user_id,
       releases.data_id    AS release_id,
       wantlist.rating     AS rating,
       wantlist.date_added AS date_added
FROM public.user_wantlists AS wantlist
JOIN public.releases AS releases ON releases.data_id = wantlist.release_id::text
""",
        ),
        # `owned_copies` is foreign-keyed to both endpoints, so no join is needed
        # to prove the edge resolves.
        _view(
            "owns",
            """
SELECT owned_copies.id                AS owned_copy_id,
       owned_copies.user_id           AS user_id,
       owned_copies.item_id           AS item_id,
       owned_copies.artifact_id       AS artifact_id,
       owned_copies.collection_row_id AS collection_row_id,
       owned_copies.acquired_at       AS acquired_at
FROM public.owned_copies AS owned_copies
""",
        ),
    ]


# ── Credit, company, and media graph relations (ADR 0007, ADR 0011) ───────────
# The remaining Neo4j projections read structure the catalog stores but the
# entity tables do not key on: the person credits in `releases.data->'extraartists'`,
# the manufacturing credits in the canonical `companies` block, and the canonical
# `media` block on both providers' release tables.
#
# Two vocabularies live in `groovemap-runtime` and are owned there: the credit-role
# taxonomy behind `common.credit_roles.categorize_role`, and the media taxonomy
# behind `common.media.medium_label`. A view cannot call Python, so each is
# rendered into an IMMUTABLE SQL function at statement-build time, from the
# runtime's own data rather than a second copy of it. When the runtime pin moves,
# the rendered CASE moves with it; nothing here restates a role or a label.


def _sql_literal(value: str) -> str:
    """Return VALUE as a single-quoted SQL string literal."""
    return "'" + value.replace("'", "''") + "'"


def _role_category_branches() -> list[tuple[str, str]]:
    """Return every (role fragment, category) in the order `categorize_role` scans.

    `categorize_role` tries an exact match on the lowered, stripped role and then
    scans fragments longest-first — globally, across categories, so a generic
    fragment declared in an earlier category cannot pre-empt a longer, more
    specific one declared later. The same ordering is reproduced here so the
    rendered CASE resolves a compound credit ("Recorded By, Mastered By") the way
    the Python does.
    """
    fragments = {role: category for category, roles in ROLE_CATEGORIES.items() for role in roles}
    return sorted(fragments.items(), key=lambda pair: (-len(pair[0]), pair[0]))


def _credit_role_category_function() -> str:
    """Return the IMMUTABLE function rendering the shared credit-role taxonomy."""
    branches = _role_category_branches()
    exact = "\n".join(f"        WHEN normalized.role = {_sql_literal(fragment)} THEN {_sql_literal(category)}" for fragment, category in branches)
    contained = "\n".join(
        f"        WHEN strpos(normalized.role, {_sql_literal(fragment)}) > 0 THEN {_sql_literal(category)}" for fragment, category in branches
    )
    return f"""
        CREATE OR REPLACE FUNCTION graph.credit_role_category(raw_role text)
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        RETURNS NULL ON NULL INPUT
        AS $credit_role_category$
        SELECT CASE
{exact}
{contained}
            ELSE 'other'
        END
        FROM (SELECT btrim(lower(raw_role)) AS role) AS normalized
        $credit_role_category$
        """  # noqa: S608


# The MusicBrainz relationship vocabulary, copied verbatim from
# `musicbrainz-graph-enricher`'s `brainzgraphinator/_projections.py`. The two
# stores disagree today and that disagreement is the gap this closes:
# `musicbrainz-sql-loader` stores the raw MusicBrainz string while the graph
# enricher maps it to a Neo4j relationship type before it writes an edge, so a
# query ported from Cypher asks for `MEMBER_OF` and finds `member of band`.
#
# The enricher resolves an unmapped string to None and writes no edge at all.
# The relational side cannot drop the row — `musicbrainz.relationships` holds
# every relationship the loader ingested, not only the eight the enricher
# projects — so the mapped column is NULL there and the raw string stays
# readable beside it. A consumer filtering on the mapped name therefore sees
# exactly the edges Neo4j carries, and one filtering on the raw string sees
# everything.
MUSICBRAINZ_RELATIONSHIP_TYPES: dict[str, str] = {
    "member of band": "MEMBER_OF",
    "collaboration": "COLLABORATED_WITH",
    "teacher": "TAUGHT",
    "tribute": "TRIBUTE_TO",
    "founder": "FOUNDED",
    "supporting musician": "SUPPORTED",
    "subgroup": "SUBGROUP_OF",
    "artist rename": "RENAMED_TO",
}


def _mb_relationship_type_function() -> str:
    """Return the IMMUTABLE function rendering the enricher's relationship map."""
    branches = "\n".join(
        f"            WHEN {_sql_literal(raw)} THEN {_sql_literal(mapped)}" for raw, mapped in sorted(MUSICBRAINZ_RELATIONSHIP_TYPES.items())
    )
    return f"""
        CREATE OR REPLACE FUNCTION graph.mb_relationship_type(raw_relationship_type text)
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        RETURNS NULL ON NULL INPUT
        AS $mb_relationship_type$
        SELECT CASE raw_relationship_type
{branches}
        END
        $mb_relationship_type$
        """


def _medium_label_function() -> str:
    """Return the IMMUTABLE function rendering the vendored media taxonomy's labels.

    An id the vendored vocabulary does not carry falls back to the id itself,
    which is what the enricher does: a release whose producer ran a newer taxonomy
    keeps its media in the graph, with a cosmetic label a later pass corrects.
    """
    branches = "\n".join(f"            WHEN {_sql_literal(medium)} THEN {_sql_literal(medium_label(medium))}" for medium in sorted(medium_ids()))
    return f"""
        CREATE OR REPLACE FUNCTION graph.medium_label(medium_id text)
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        RETURNS NULL ON NULL INPUT
        AS $medium_label$
        SELECT CASE medium_id
{branches}
            ELSE medium_id
        END
        $medium_label$
        """


# One row per `extraartists` credit that names both a person and a role — the two
# the enricher requires before it MERGEs a `:Person` or a `[:CREDITED_ON]`. Names
# are read verbatim, not normalized: `Person.name` is the Neo4j key, so folding it
# here would key the vertex differently from the node it mirrors.
_CREDIT_SOURCE = f"""
    SELECT releases.data_id              AS release_id,
           credit.value ->> 'name'       AS person_name,
           credit.value ->> 'role'       AS role,
           btrim(credit.value ->> 'id')  AS artist_id
    FROM public.releases AS releases
    CROSS JOIN LATERAL jsonb_array_elements({_jsonb_array("releases.data -> 'extraartists'")}) AS credit(value)
    WHERE {_non_empty("credit.value ->> 'name'")}
      AND {_non_empty("credit.value ->> 'role'")}
"""  # noqa: S608

# One row per entry of the canonical companies block (ADR 0011). A pre-cutover
# record whose `companies` key still holds the RAW Discogs list contributes
# nothing: subscripting a JSON array by a text key yields NULL, so the guard
# resolves it to an empty array. That is the intended reading — such a record is
# silent about company credits rather than asserting it has none.
#
# The identity rule is the producer's: a whole Discogs id of at least one when the
# source supplies one, otherwise `name:` followed by the name case-folded with
# inner whitespace collapsed. PostgreSQL's `lower` is an approximation of Python's
# `casefold` — they differ for a handful of characters such as the German eszett —
# and punctuation is deliberately left alone, so two spellings differing by a comma
# stay two companies a later reconciliation can merge.
_COMPANY_SOURCE = f"""
    SELECT releases.data_id AS release_id,
           item.ordinality  AS entry_position,
           CASE
               WHEN btrim(item.value ->> 'discogs_id') ~ '^0*[1-9][0-9]*$' THEN btrim(item.value ->> 'discogs_id')
               WHEN {_non_empty("btrim(item.value ->> 'name')")}
                   THEN 'name:' || lower(regexp_replace(btrim(item.value ->> 'name'), '\\s+', ' ', 'g'))
           END AS company_id,
           NULLIF(btrim(item.value ->> 'name'), '') AS company_name,
           NULLIF(btrim(item.value ->> 'role'), '') AS role,
           COALESCE(NULLIF(btrim(item.value ->> 'role_category'), ''), 'other') AS role_category
    FROM public.releases AS releases
    CROSS JOIN LATERAL jsonb_array_elements({_jsonb_array("releases.data -> 'companies' -> 'items'")}) WITH ORDINALITY AS item(value, ordinality)
"""  # noqa: S608

# A medium entry's unit count, defaulting to one exactly as the enricher does for
# an absent, non-integer, boolean, or non-positive `qty`. The digit bound keeps a
# pathological value from overflowing the cast rather than defaulting.
_MEDIUM_QUANTITY = (
    "CASE WHEN jsonb_typeof(item.value -> 'qty') = 'number' "
    "AND (item.value ->> 'qty') ~ '^[0-9]{1,9}$' "
    "AND (item.value ->> 'qty')::bigint >= 1 "
    "THEN (item.value ->> 'qty')::bigint ELSE 1 END"
)

# One row per canonical media item, from both providers' release tables. `:Medium`
# and `:MediaFamily` nodes are shared across catalogs and each provider writes its
# own `[:ISSUED_ON]` edge to them, so `source` is part of the edge key rather than
# a property, and the MusicBrainz side joins down to the Discogs release id the
# enricher keys `:Release` on.
_MEDIA_SOURCE = f"""
    SELECT releases.data_id          AS release_id,
           'discogs'::text           AS provider,
           item.value ->> 'medium'   AS medium_id,
           item.value ->> 'family'   AS family_name,
           {_MEDIUM_QUANTITY}        AS qty
    FROM public.releases AS releases
    CROSS JOIN LATERAL jsonb_array_elements({_jsonb_array("releases.media -> 'items'")}) AS item(value)
    WHERE {_non_empty("item.value ->> 'medium'")}
      AND {_non_empty("item.value ->> 'family'")}
    UNION ALL
    SELECT releases.data_id          AS release_id,
           'musicbrainz'::text       AS provider,
           item.value ->> 'medium'   AS medium_id,
           item.value ->> 'family'   AS family_name,
           {_MEDIUM_QUANTITY}        AS qty
    FROM musicbrainz.releases AS mb_release
    JOIN public.releases AS releases ON releases.data_id = mb_release.discogs_release_id::text
    CROSS JOIN LATERAL jsonb_array_elements({_jsonb_array("mb_release.media -> 'items'")}) AS item(value)
    WHERE {_non_empty("item.value ->> 'medium'")}
      AND {_non_empty("item.value ->> 'family'")}
"""  # noqa: S608


def _credit_views() -> list[tuple[str, str]]:
    """Return the one credit-and-media relation that stays a view.

    `person`, `company`, `medium`, and `media_family` are vertex tables now, and
    `credited_on`, `same_as`, `credited_to`, and `issued_on` are edge tables; the
    sources above are retained because the phase 0 comparison schema still
    renders them, not because the initializer creates them.

    `in_family` is bounded by the media taxonomy — a few dozen rows — so Table 2
    of the coverage spike keeps it a view, now over `graph.medium` rather than
    over a re-unnest of both providers' media blocks. The `medium` table already
    carries the family each medium belongs to, so the relation is a projection
    of one small table joined to the family vertex it resolves to.
    """
    return [
        _view(
            "in_family",
            """
SELECT medium.medium_id AS medium_id,
       medium.family    AS family_name
FROM graph.medium AS medium
JOIN graph.media_family AS family ON family.name = medium.family
""",
        ),
    ]


# The four labels Neo4j carries counters on, each paired with the relation
# holding its own rows and the relation holding its counters. A property graph
# admits ONE element table per label — two tables sharing a label must expose an
# identical property set, and PostgreSQL 19 beta 3 refuses the pair outright
# with `mismatching number of properties in definition of label "genre"` — so
# the counters cannot be attached to the label as a second element table. A view
# that joins the two is one element table, which is what makes
# `MATCH (g IS genre) COLUMNS (g.release_count)` read exactly as the Cypher it
# replaces.
_COUNTER_BEARING_VERTICES: tuple[tuple[str, str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("genre_vertex", "genre", "genre_stats", ("name",), ("release_count", "artist_count", "label_count", "style_count")),
    ("style_vertex", "style", "style_stats", ("name",), ("release_count", "artist_count", "label_count", "genre_count")),
    (
        "label_vertex",
        "label",
        "label_stats",
        ("label_id", "name", "gm_item_id", "hash", "updated_at", "gm_id"),
        ("release_count", "artist_count", "genre_count"),
    ),
    ("artist_vertex", "artist", "artist_degree", ("artist_id", "name", "gm_item_id", "hash", "updated_at", "gm_id"), ("degree",)),
)

# The join column of each, which is also the key of both relations either side
# of it. That the counter relation is UNIQUE on it is the whole reason this
# shape is free: the planner removes a LEFT JOIN to a uniquely-keyed relation
# outright when no column of it is selected, so a traversal that never names a
# counter plans exactly as it did against the storage relation alone.
_COUNTER_JOIN_COLUMNS = {"genre_vertex": "name", "style_vertex": "name", "label_vertex": "label_id", "artist_vertex": "artist_id"}


def _counter_vertex_view(relation: str, storage: str, counters: str, carried: tuple[str, ...], counted: tuple[str, ...]) -> tuple[str, str]:
    """Return the vertex projection joining one storage relation to its counters.

    A count reads zero where the loader has not computed one yet, because every
    caller of these does arithmetic on it and a null would propagate through a
    ratio or a sum. `first_year` is deliberately not defaulted: an unknown first
    year must not read as year zero, and every caller of it tests for null
    already.
    """
    join = _COUNTER_JOIN_COLUMNS[relation]
    columns = [f"       {storage}.{column:<14} AS {column}," for column in carried]
    columns.extend(f"       COALESCE({counters}.{column}, 0)::bigint AS {column}," for column in counted)
    if counters.endswith("_stats") and counters != "label_stats":
        columns.append(f"       {counters}.first_year AS first_year,")
    body = "\n".join(columns).rstrip(",")
    return _view(
        relation,
        f"""
SELECT
{body}
FROM graph.{storage} AS {storage}
LEFT JOIN graph.{counters} AS {counters} ON {counters}.{join} = {storage}.{join}
""",  # noqa: S608
    )


def _counter_views() -> list[tuple[str, str]]:
    """Return the vertex projections carrying the counters, and release degree.

    Eight `catalog-api` functions read counters that `graphinator` writes onto
    `:Genre`, `:Style`, `:Label`, and `:Artist` nodes in a post-import pass.
    `explore_genre`'s own docstring records that reading `g.release_count`
    replaces four traversal queries — roughly 200 million database hits for Rock
    — with one property read, and re-aggregating on request is the failure that
    took the rarity pipeline down for thirty-three days. So these have to stay
    properties of the label the Cypher names, not a second label a rewrite has
    to learn.

    Each label therefore binds a projection that joins its storage relation to
    its counter relation. The counter relations stay exactly as the loaders
    write them; nothing here duplicates a row.

    `graph.release_degree` is the one counter that is NOT folded onto its label,
    and the reason is a measurement rather than a rule. Its live half is a pair
    of lateral counts over `user_collections` and `user_wantlists`, which no
    unique key makes removable, so folding it onto the `release` vertex would
    make every release binding in every traversal count collection and wantlist
    rows even where degree is never read — a nine-line plan becomes nineteen.
    It stays its own label, and `MATCH (r IS release_degree WHERE r.release_id =
    …)` is the one spelling a rewrite has to carry forward.
    """
    views = [_counter_vertex_view(*entry) for entry in _COUNTER_BEARING_VERTICES]
    views.append(
        _view(
            "release_degree",
            """
SELECT base.release_id                                             AS release_id,
       (base.degree + collected.tally + wanted.tally)::bigint      AS degree
FROM graph.release_degree_base AS base
CROSS JOIN LATERAL (
    SELECT CASE WHEN base.release_id ~ '^[0-9]+$' THEN base.release_id::bigint END AS discogs_id
) AS numeric_id
CROSS JOIN LATERAL (
    SELECT count(*) AS tally
    FROM public.user_collections AS collection
    WHERE collection.release_id = numeric_id.discogs_id
) AS collected
CROSS JOIN LATERAL (
    SELECT count(*) AS tally
    FROM public.user_wantlists AS wantlist
    WHERE wantlist.release_id = numeric_id.discogs_id
) AS wanted
""",
        )
    )
    return views


# ── Loader-written graph relations (spike gm-database-schema-9c8.1 / 9c8.2) ───
# The relations below are TABLES, not views, and this repository does not write
# a single row into any of them. `discogs-sql-loader` owns every one except the
# `medium`, `media_family`, and `issued_on` rows carrying `source =
# 'musicbrainz'`, which `musicbrainz-sql-loader` upserts alongside them. The
# initializer's whole job here is to declare the shape and the indexes and then
# get out of the way; an empty table on a fresh database is the expected state
# until a loader runs.
#
# Why tables at all, when the phase 0 views already produced the same rows:
# spike gm-database-schema-9c8.1 measured the views and found every hot edge
# re-unnesting a JSONB document per query, unable to carry an index, and unable
# to be entered from the target end at all. Its recommendation is these tables
# with both directions indexed. Spike gm-database-schema-9c8.2 Table 2 and
# Table 3 are the shapes, keys, indexes, and owners; nothing here invents one.
#
# Three rules hold across the whole set:
#
# - **Every key column is `text`.** PostgreSQL 19 beta 3 cannot resolve an
#   equality operator for a `character varying` property-graph vertex key, which
#   is why the phase 0 Discogs vertex views carried appended `<entity>_key`
#   restatements. Tables written as `text` from the start need no such
#   workaround, and the four appended columns are retired with them.
# - **Every edge table is indexed in both directions.** The primary key serves
#   the forward walk and a second index serves the reverse, because the ported
#   Cypher enters these edges from the target end as often as from the source.
#   An edge table indexed one way only reproduces the exact failure the views
#   have.
# - **No foreign key to any base table.** A loader writes an edge in the same
#   transaction as the document it came from and may legitimately reach an
#   entity it has not ingested yet, exactly as `graphinator` merges a target
#   node as it writes the edge. Resolution is the property graph's job.

# Retiring a phase 0 view is the one place this schema drops anything, and it
# drops only a relation it is replacing in the same pass, only when a view of
# that name is actually still there. A fresh database drops nothing; a second
# apply finds a table rather than a view and drops nothing either. CASCADE is
# required because on PostgreSQL 19 `graph.catalog` depends on the view, and
# `_apply_property_graph` re-declares the graph on the same run.
_VIEW_TO_TABLE_MIGRATION = """
DO ${tag}$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'graph'
          AND relation.relname = '{relation}'
          AND relation.relkind = 'v'
    ) THEN
        DROP VIEW graph.{relation} CASCADE;
    END IF;
END
${tag}$
"""

# The same guard for the four Discogs vertex relations that stay views. Their
# key column is being retyped from `character varying` to `text` and their
# appended `<entity>_key` restatement removed, and `CREATE OR REPLACE VIEW`
# refuses both. The condition is the retype itself rather than the presence of
# the appended column, so the guard is self-healing from any half-applied state
# and is a no-op the moment the key already reads as `text`.
_KEY_RETYPE_MIGRATION = """
DO ${tag}$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'graph'
          AND table_name = '{relation}'
          AND column_name = '{column}'
          AND data_type <> 'text'
    ) THEN
        DROP VIEW graph.{relation} CASCADE;
    END IF;
END
${tag}$
"""

# Trigram indexes are what the six full-text query functions the coverage spike
# found need, and they are the reason `graph.genre`, `graph.style`, and
# `graph.person` had to stop being views: a view cannot carry an index at all.
# The extension is created above, but a deployment whose role cannot create one
# should lose the index rather than the schema, so each index is guarded on the
# extension actually being present instead of assuming the CREATE succeeded.
_TRIGRAM_INDEX = """
DO ${tag}$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
        CREATE INDEX IF NOT EXISTS {index} ON graph.{relation} USING GIN ({column} gin_trgm_ops);
    END IF;
END
${tag}$
"""


def _view_to_table_migration(relation: str) -> tuple[str, str]:
    """Return the guarded statement retiring one phase 0 view before its table."""
    return (
        f"graph.{relation} view-to-table migration",
        _VIEW_TO_TABLE_MIGRATION.format(tag=f"retire_{relation}", relation=relation).strip(),
    )


def _key_retype_migration(relation: str, column: str) -> tuple[str, str]:
    """Return the guarded statement letting one vertex view republish a text key."""
    return (
        f"graph.{relation} key-type migration",
        _KEY_RETYPE_MIGRATION.format(tag=f"retype_{relation}", relation=relation, column=column).strip(),
    )


def _table(relation: str, columns: str) -> tuple[str, str]:
    """Return the named CREATE TABLE IF NOT EXISTS statement for one relation.

    COLUMNS is written the way it reads best — aligned across several lines for
    a wide relation, on one line for a relation with a single column — so the
    body is re-indented here rather than at every call site.
    """
    body = "\n".join(line if line.startswith(" ") else f"    {line}" for line in columns.strip(chr(10)).rstrip().splitlines())
    return (f"graph.{relation} table", f"CREATE TABLE IF NOT EXISTS graph.{relation} (\n{body}\n)")


def _table_index(relation: str, suffix: str, columns: str) -> tuple[str, str]:
    """Return a named secondary index on one graph table."""
    index = f"{relation}_{suffix}"
    return (f"graph.{index} index", f"CREATE INDEX IF NOT EXISTS {index} ON graph.{relation} ({columns})")


def _trigram_index(relation: str, column: str) -> tuple[str, str]:
    """Return the extension-guarded GIN trigram index autocomplete reads."""
    index = f"{relation}_{column}_trgm"
    return (
        f"graph.{index} index",
        _TRIGRAM_INDEX.format(tag=f"trgm_{relation}", index=index, relation=relation, column=column).strip(),
    )


# The six vertex relations that were `SELECT DISTINCT` over a full unnest of
# every release and every master to yield a few hundred rows. Each keeps exactly
# the columns its phase 0 view published, so the property graph's property lists
# do not move; only the shape underneath them does.
def _vertex_table_statements() -> list[tuple[str, str]]:
    """Return the name-keyed vertex tables and their indexes."""
    return [
        _table("genre", "name text PRIMARY KEY"),
        _trigram_index("genre", "name"),
        _table("style", "name text PRIMARY KEY"),
        _trigram_index("style", "name"),
        # `:Person` is keyed on the verbatim credit name, exactly as Neo4j keys
        # it. Folding it here would key the vertex differently from the node.
        _table("person", "name text PRIMARY KEY"),
        _trigram_index("person", "name"),
        _table("media_family", "name text PRIMARY KEY"),
        _table(
            "medium",
            """
    medium_id text PRIMARY KEY,
    family    text,
    label     text
""",
        ),
        # The producer's identity rule for a company is the loader's to apply and
        # is deliberately NOT recomputed here: a whole Discogs id of at least one
        # when the source supplies one, otherwise `name:` followed by the name
        # case-folded with inner whitespace collapsed and punctuation left alone.
        # The phase 0 view approximated Python's `casefold` with SQL `lower`,
        # which disagrees on the German eszett and a handful of other characters.
        # `discogs-sql-loader` writes the rule's own answer, so the approximation
        # retires with the view rather than being carried forward as a generated
        # column that would re-introduce it.
        _table(
            "company",
            """
    company_id       text PRIMARY KEY,
    name             text,
    discogs_label_id text
""",
        ),
    ]


# Table 2 of the coverage spike, in its own order. Each entry is the relation,
# its column block, the reverse index that makes the target-end walk cheap, and
# any extra index the table lists. `part_of`, `in_family`, and `sublabel_of` are
# absent on purpose and stay views; see `_discogs_edge_views` and `_credit_views`.
_EDGE_TABLES: list[tuple[str, str, str, tuple[tuple[str, str], ...]]] = [
    (
        "by_artist",
        """
    release_id text NOT NULL,
    artist_id  text NOT NULL,
    PRIMARY KEY (release_id, artist_id)
""",
        "artist_id, release_id",
        (),
    ),
    (
        "on_label",
        """
    release_id text NOT NULL,
    label_id   text NOT NULL,
    PRIMARY KEY (release_id, label_id)
""",
        "label_id, release_id",
        (),
    ),
    (
        "derived_from",
        """
    release_id text NOT NULL,
    master_id  text NOT NULL,
    PRIMARY KEY (release_id, master_id)
""",
        "master_id, release_id",
        (),
    ),
    (
        "in_genre",
        """
    release_id text NOT NULL,
    genre_name text NOT NULL,
    PRIMARY KEY (release_id, genre_name)
""",
        "genre_name, release_id",
        (),
    ),
    (
        "in_style",
        """
    release_id text NOT NULL,
    style_name text NOT NULL,
    PRIMARY KEY (release_id, style_name)
""",
        "style_name, release_id",
        (),
    ),
    (
        "master_by_artist",
        """
    master_id text NOT NULL,
    artist_id text NOT NULL,
    PRIMARY KEY (master_id, artist_id)
""",
        "artist_id, master_id",
        (),
    ),
    (
        "master_in_genre",
        """
    master_id  text NOT NULL,
    genre_name text NOT NULL,
    PRIMARY KEY (master_id, genre_name)
""",
        "genre_name, master_id",
        (),
    ),
    (
        "master_in_style",
        """
    master_id  text NOT NULL,
    style_name text NOT NULL,
    PRIMARY KEY (master_id, style_name)
""",
        "style_name, master_id",
        (),
    ),
    # Read in both directions by `expand_artist_aliases` and
    # `count_artist_aliases`, which is the whole reason it cannot stay a view.
    (
        "member_of",
        """
    member_artist_id text NOT NULL,
    group_artist_id  text NOT NULL,
    PRIMARY KEY (member_artist_id, group_artist_id)
""",
        "group_artist_id, member_artist_id",
        (),
    ),
    (
        "alias_of",
        """
    alias_artist_id text NOT NULL,
    artist_id       text NOT NULL,
    PRIMARY KEY (alias_artist_id, artist_id)
""",
        "artist_id, alias_artist_id",
        (),
    ),
    # One person credited twice on a release under two roles is two edges, so
    # `role` is in the key. `role_category` is generated rather than written:
    # nine credits functions read it and a loader that forgot to set it would
    # produce a silent null rather than a failure. The expression is bound to
    # `graph.credit_role_category` at creation time, so re-rendering that
    # function from a newer runtime pin does not recompute stored rows; a
    # taxonomy move is a backfill, which is the same thing it is in Neo4j.
    (
        "credited_on",
        """
    person_name   text NOT NULL,
    release_id    text NOT NULL,
    role          text NOT NULL,
    role_category text GENERATED ALWAYS AS (graph.credit_role_category(role)) STORED,
    PRIMARY KEY (person_name, release_id, role)
""",
        "release_id, person_name",
        (("role_category", "role_category, person_name"),),
    ),
    (
        "same_as",
        """
    person_name text NOT NULL,
    artist_id   text NOT NULL,
    PRIMARY KEY (person_name, artist_id)
""",
        "artist_id",
        (),
    ),
    # `source` stays in the key, as ADR 0011 and the phase 0 view already have
    # it, and carries its own index because each loader prunes its own rows.
    (
        "credited_to",
        """
    release_id    text NOT NULL,
    company_id    text NOT NULL,
    role          text NOT NULL,
    role_category text NOT NULL DEFAULT 'other',
    source        text NOT NULL,
    PRIMARY KEY (release_id, company_id, role, source)
""",
        "company_id, release_id",
        (("source", "source"),),
    ),
    # `:Medium` and `:MediaFamily` are shared across catalogs and each provider
    # writes its own edge to them, so `source` is part of the key rather than a
    # property — precisely so `discogs-sql-loader` writing `source='discogs'`
    # and `musicbrainz-sql-loader` writing `source='musicbrainz'` do not
    # collide, and so each one's source-scoped prune reaches only its own rows.
    (
        "issued_on",
        """
    release_id text NOT NULL,
    medium_id  text NOT NULL,
    source     text NOT NULL,
    qty        bigint NOT NULL DEFAULT 1,
    PRIMARY KEY (release_id, medium_id, source)
""",
        "medium_id, release_id",
        (("source", "source"),),
    ),
]


def _edge_table_statements() -> list[tuple[str, str]]:
    """Return the fourteen Discogs edge tables with both directions indexed."""
    statements: list[tuple[str, str]] = []
    for relation, columns, reverse, extras in _EDGE_TABLES:
        statements.append(_table(relation, columns))
        statements.append(_table_index(relation, "reverse", reverse))
        statements.extend(_table_index(relation, suffix, index_columns) for suffix, index_columns in extras)
    return statements


# Table 3 of the coverage spike: the pre-computed node properties `graphinator`
# writes in a post-import pass and that eight catalog-api functions read as if
# they were free. A graph declared over views has nowhere to put them.
#
# `discogs-sql-loader` refreshes all of these on the `extraction_complete`
# message it already handles, which is the same latch `graphinator` uses to
# start its own post-import pass — so they cost no new scheduler. The degree
# relations are sums over the edge tables above and never re-read a JSONB
# document.
#
# `graph.release_degree_base` is the one relation in the whole edge model split
# across two owners. Neo4j's release degree counts COLLECTED and WANTS edges,
# which catalog-api writes and the loader never sees. The loader writes the base
# count here and `graph.release_degree` adds the live counts; see
# `_counter_views`.
def _counter_table_statements() -> list[tuple[str, str]]:
    """Return the counter, degree, and genre-aggregate relations and their indexes."""
    return [
        _table(
            "genre_stats",
            """
    name          text PRIMARY KEY,
    release_count bigint NOT NULL DEFAULT 0,
    artist_count  bigint NOT NULL DEFAULT 0,
    label_count   bigint NOT NULL DEFAULT 0,
    style_count   bigint NOT NULL DEFAULT 0,
    first_year    integer
""",
        ),
        _table_index("genre_stats", "first_year", "first_year"),
        _table(
            "style_stats",
            """
    name          text PRIMARY KEY,
    release_count bigint NOT NULL DEFAULT 0,
    artist_count  bigint NOT NULL DEFAULT 0,
    label_count   bigint NOT NULL DEFAULT 0,
    genre_count   bigint NOT NULL DEFAULT 0,
    first_year    integer
""",
        ),
        _table_index("style_stats", "first_year", "first_year"),
        _table(
            "label_stats",
            """
    label_id      text PRIMARY KEY,
    release_count bigint NOT NULL DEFAULT 0,
    artist_count  bigint NOT NULL DEFAULT 0,
    genre_count   bigint NOT NULL DEFAULT 0
""",
        ),
        _table_index("label_stats", "release_count", "release_count"),
        # `size([(a)-[]-() | 1])` and `COUNT { (a)--() }` are documented memory
        # hazards in Neo4j; an indexed counter read replaces a list-materializing
        # scan. The descending index is what the leaderboard reads order on.
        _table(
            "artist_degree",
            """
    artist_id text PRIMARY KEY,
    degree    bigint NOT NULL DEFAULT 0
""",
        ),
        _table_index("artist_degree", "degree", "degree DESC"),
        _table(
            "release_degree_base",
            """
    release_id text PRIMARY KEY,
    degree     bigint NOT NULL DEFAULT 0
""",
        ),
        # The two aggregates that have no Neo4j counterpart at all. Both are
        # keyed on the pair and carry the reverse index, so "which genres does
        # this artist release in" and "which artists release in this genre" are
        # both index-only reads.
        _table(
            "artist_genre",
            """
    artist_id     text NOT NULL,
    genre_name    text NOT NULL,
    release_count bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (artist_id, genre_name)
""",
        ),
        _table_index("artist_genre", "reverse", "genre_name, artist_id"),
        _table(
            "label_genre",
            """
    label_id      text NOT NULL,
    genre_name    text NOT NULL,
    release_count bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (label_id, genre_name)
""",
        ),
        _table_index("label_genre", "reverse", "genre_name, label_id"),
    ]


# Every relation that stops being a view, in the order its table is created.
_MATERIALIZED_VERTICES = ("genre", "style", "person", "media_family", "medium", "company")
_MATERIALIZED_EDGES = tuple(relation for relation, _columns, _reverse, _extras in _EDGE_TABLES)

# The relations that stay views but republish a key column as `text`. The four
# Discogs vertices do it because a property-graph vertex key cannot be
# `character varying`; `sublabel_of` does it because a UNION takes the type of
# its first branch and that branch reads `labels.data_id`, so the column came
# out `character varying` while every other edge key in the schema is `text`.
_TEXT_KEY_RETYPES: tuple[tuple[str, str], ...] = (
    ("artist", "artist_id"),
    ("label", "label_id"),
    ("master", "master_id"),
    ("release", "release_id"),
    ("sublabel_of", "sublabel_id"),
)


def _graph_table_statements() -> list[tuple[str, str]]:
    """Return every loader-owned table, preceded by the migrations that free its name."""
    migrations = [_view_to_table_migration(relation) for relation in (*_MATERIALIZED_VERTICES, *_MATERIALIZED_EDGES)]
    migrations.extend(_key_retype_migration(relation, column) for relation, column in _TEXT_KEY_RETYPES)
    return [*migrations, *_vertex_table_statements(), *_edge_table_statements(), *_counter_table_statements()]


# ── Phase 0 definitions, retained for the table-versus-view comparison ───────
# Everything below builds the view bodies the twenty materialized relations had
# before they became tables. NOTHING HERE IS SHIPPED AS A RELATION: none of it
# is reachable from `_schema_statements()`, the initializer never creates it,
# and `graph` never holds one of these views again after the migration above
# retires it. `tests/test_graph_schema.py` pins that.
#
# It is kept, rather than copied into the test tree, because two things read it
# and neither is worth having if the two sides can drift. The parity harness
# creates these definitions in a throwaway schema, fills the tables from them,
# and then runs the same `GRAPH_TABLE` query over a graph declared on the tables
# and over the retained views. And `graph.bootstrap_fill()` below — the one
# shipped thing in this module that writes a graph row — inlines the same bodies
# to populate an environment once, so the fill is the phase 0 projection by
# construction rather than by inspection. A hand-copied body would turn a real
# disagreement into a stale-copy artifact the first time a projection rule moved.


def _phase0_relation_bodies() -> dict[str, str]:
    """Return the phase 0 view body of every relation that is now a table."""
    genres = "source.document -> 'genres'"
    styles = "source.document -> 'styles'"
    bodies = {
        "genre": f"""
SELECT DISTINCT genre.value AS name
FROM ({_TAGGED_DOCUMENTS.strip()}) AS source
CROSS JOIN LATERAL jsonb_array_elements_text({_jsonb_array(genres)}) AS genre(value)
WHERE {_non_empty("genre.value")}
""",  # noqa: S608
        "style": f"""
SELECT DISTINCT style.value AS name
FROM ({_TAGGED_DOCUMENTS.strip()}) AS source
CROSS JOIN LATERAL jsonb_array_elements_text({_jsonb_array(styles)}) AS style(value)
WHERE {_non_empty("style.value")}
""",  # noqa: S608
        "person": f"""
SELECT DISTINCT credit.person_name AS name
FROM ({_CREDIT_SOURCE.strip()}) AS credit
""",  # noqa: S608
        "company": f"""
SELECT DISTINCT ON (credit.company_id)
       credit.company_id                                      AS company_id,
       COALESCE(credit.company_name, credit.company_id)       AS name,
       CASE WHEN credit.company_id ~ '^[0-9]+$' THEN credit.company_id END AS discogs_label_id
FROM ({_COMPANY_SOURCE.strip()}) AS credit
WHERE credit.company_id IS NOT NULL
  AND credit.role IS NOT NULL
ORDER BY credit.company_id, COALESCE(credit.company_name, credit.company_id)
""",  # noqa: S608
        "medium": f"""
SELECT DISTINCT ON (media.medium_id)
       media.medium_id                       AS medium_id,
       media.family_name                     AS family,
       graph.medium_label(media.medium_id)   AS label
FROM ({_MEDIA_SOURCE.strip()}) AS media
ORDER BY media.medium_id, media.family_name
""",  # noqa: S608
        "media_family": f"""
SELECT DISTINCT media.family_name AS name
FROM ({_MEDIA_SOURCE.strip()}) AS media
""",  # noqa: S608
        "derived_from": f"""
SELECT releases.data_id::text                 AS release_id,
       btrim(releases.data ->> 'master_id')   AS master_id
FROM public.releases AS releases
WHERE {_usable_id("releases.data ->> 'master_id'")}
""",  # noqa: S608
        # Discogs states band membership from both ends — the band lists
        # `members`, the member lists `groups` — so the two unnests become one
        # directed edge and UNION collapses the duplicate a reciprocal pair makes.
        "member_of": f"""
SELECT btrim(element.value ->> 'id') AS member_artist_id,
       artists.data_id::text         AS group_artist_id
FROM public.artists AS artists
CROSS JOIN LATERAL jsonb_array_elements({_jsonb_array("artists.data -> 'members'")}) AS element(value)
WHERE {_usable_id("element.value ->> 'id'")}
UNION
SELECT artists.data_id::text         AS member_artist_id,
       btrim(element.value ->> 'id') AS group_artist_id
FROM public.artists AS artists
CROSS JOIN LATERAL jsonb_array_elements({_jsonb_array("artists.data -> 'groups'")}) AS element(value)
WHERE {_usable_id("element.value ->> 'id'")}
""",  # noqa: S608
        "alias_of": f"""
SELECT DISTINCT btrim(element.value ->> 'id') AS alias_artist_id,
       artists.data_id::text                  AS artist_id
FROM public.artists AS artists
CROSS JOIN LATERAL jsonb_array_elements({_jsonb_array("artists.data -> 'aliases'")}) AS element(value)
WHERE {_usable_id("element.value ->> 'id'")}
""",  # noqa: S608
        # CREDITED_ON is keyed on (person, release, role): one person credited
        # twice on a release under two roles is two edges.
        "credited_on": f"""
SELECT DISTINCT credit.person_name                          AS person_name,
       credit.release_id                                    AS release_id,
       credit.role                                          AS role,
       graph.credit_role_category(credit.role)              AS role_category
FROM ({_CREDIT_SOURCE.strip()}) AS credit
""",  # noqa: S608
        "same_as": f"""
SELECT DISTINCT credit.person_name AS person_name,
       credit.artist_id            AS artist_id
FROM ({_CREDIT_SOURCE.strip()}) AS credit
WHERE {_usable_id("credit.artist_id")}
""",  # noqa: S608
        # CREDITED_TO is keyed on (release, company, role, source); the same entry
        # repeated in the document is one edge, and the first occurrence wins so
        # the row reads the way the release does.
        "credited_to": f"""
SELECT DISTINCT ON (credit.release_id, credit.company_id, credit.role)
       credit.release_id    AS release_id,
       credit.company_id    AS company_id,
       credit.role          AS role,
       credit.role_category AS role_category,
       'discogs'::text      AS source
FROM ({_COMPANY_SOURCE.strip()}) AS credit
WHERE credit.company_id IS NOT NULL
  AND credit.role IS NOT NULL
ORDER BY credit.release_id, credit.company_id, credit.role, credit.entry_position
""",  # noqa: S608
        # Two format entries resolving to the same canonical medium — a 2xLP split
        # across two Discogs entries — are one edge whose qty is their sum.
        "issued_on": f"""
SELECT media.release_id       AS release_id,
       media.medium_id        AS medium_id,
       media.provider         AS source,
       SUM(media.qty)::bigint AS qty
FROM ({_MEDIA_SOURCE.strip()}) AS media
GROUP BY media.release_id, media.medium_id, media.provider
""",  # noqa: S608
    }
    for relation, table, source_column, json_key, target_column in (
        ("by_artist", "releases", "release_id", "artists", "artist_id"),
        ("on_label", "releases", "release_id", "labels", "label_id"),
        ("master_by_artist", "masters", "master_id", "artists", "artist_id"),
    ):
        bodies[relation] = f"""
SELECT DISTINCT entity.data_id::text          AS {source_column},
       btrim(element.value ->> 'id')          AS {target_column}
FROM public.{table} AS entity
CROSS JOIN LATERAL jsonb_array_elements({_jsonb_array(f"entity.data -> '{json_key}'")}) AS element(value)
WHERE {_usable_id("element.value ->> 'id'")}
"""  # noqa: S608
    for relation, table, source_column, json_key, target_column in (
        ("in_genre", "releases", "release_id", "genres", "genre_name"),
        ("in_style", "releases", "release_id", "styles", "style_name"),
        ("master_in_genre", "masters", "master_id", "genres", "genre_name"),
        ("master_in_style", "masters", "master_id", "styles", "style_name"),
    ):
        bodies[relation] = f"""
SELECT DISTINCT entity.data_id::text AS {source_column},
       tag.value                     AS {target_column}
FROM public.{table} AS entity
CROSS JOIN LATERAL jsonb_array_elements_text({_jsonb_array(f"entity.data -> '{json_key}'")}) AS tag(value)
WHERE {_non_empty("tag.value")}
"""  # noqa: S608
    return bodies


# The columns the bootstrap copies into each table. `credited_on.role_category`
# is absent on purpose: it is a generated column, and naming it in an INSERT is
# an error rather than an overwrite.
_BOOTSTRAP_COLUMNS: dict[str, tuple[str, ...]] = {
    "genre": ("name",),
    "style": ("name",),
    "person": ("name",),
    "media_family": ("name",),
    "medium": ("medium_id", "family", "label"),
    "company": ("company_id", "name", "discogs_label_id"),
    "by_artist": ("release_id", "artist_id"),
    "on_label": ("release_id", "label_id"),
    "derived_from": ("release_id", "master_id"),
    "in_genre": ("release_id", "genre_name"),
    "in_style": ("release_id", "style_name"),
    "master_by_artist": ("master_id", "artist_id"),
    "master_in_genre": ("master_id", "genre_name"),
    "master_in_style": ("master_id", "style_name"),
    "member_of": ("member_artist_id", "group_artist_id"),
    "alias_of": ("alias_artist_id", "artist_id"),
    "credited_on": ("person_name", "release_id", "role"),
    "same_as": ("person_name", "artist_id"),
    "credited_to": ("release_id", "company_id", "role", "role_category", "source"),
    "issued_on": ("release_id", "medium_id", "source", "qty"),
}

# How `discogs-sql-loader` computes each counter relation on the
# `extraction_complete` latch. These are sums over the edge tables and never
# re-read a JSONB document, which is the property that makes the post-import
# pass affordable. They are written here because the loader needs one definition
# to implement rather than five readings of a prose table, and because the
# parity harness fills the relations with them.
_COUNTER_BOOTSTRAP: dict[str, str] = {
    "genre_stats": """
SELECT genre.name AS name,
       (SELECT count(*) FROM graph.in_genre AS edge WHERE edge.genre_name = genre.name) AS release_count,
       (SELECT count(DISTINCT by_artist.artist_id)
          FROM graph.in_genre AS edge
          JOIN graph.by_artist AS by_artist ON by_artist.release_id = edge.release_id
         WHERE edge.genre_name = genre.name) AS artist_count,
       (SELECT count(DISTINCT on_label.label_id)
          FROM graph.in_genre AS edge
          JOIN graph.on_label AS on_label ON on_label.release_id = edge.release_id
         WHERE edge.genre_name = genre.name) AS label_count,
       (SELECT count(*) FROM graph.part_of AS part WHERE part.genre_name = genre.name) AS style_count,
       (SELECT min(NULLIF(btrim(release.year), '')::integer)
          FROM graph.in_genre AS edge
          JOIN graph.release AS release ON release.release_id = edge.release_id
         WHERE edge.genre_name = genre.name
           AND btrim(release.year) ~ '^[0-9]{4}$') AS first_year
FROM graph.genre AS genre
""",
    "style_stats": """
SELECT style.name AS name,
       (SELECT count(*) FROM graph.in_style AS edge WHERE edge.style_name = style.name) AS release_count,
       (SELECT count(DISTINCT by_artist.artist_id)
          FROM graph.in_style AS edge
          JOIN graph.by_artist AS by_artist ON by_artist.release_id = edge.release_id
         WHERE edge.style_name = style.name) AS artist_count,
       (SELECT count(DISTINCT on_label.label_id)
          FROM graph.in_style AS edge
          JOIN graph.on_label AS on_label ON on_label.release_id = edge.release_id
         WHERE edge.style_name = style.name) AS label_count,
       (SELECT count(*) FROM graph.part_of AS part WHERE part.style_name = style.name) AS genre_count,
       (SELECT min(NULLIF(btrim(release.year), '')::integer)
          FROM graph.in_style AS edge
          JOIN graph.release AS release ON release.release_id = edge.release_id
         WHERE edge.style_name = style.name
           AND btrim(release.year) ~ '^[0-9]{4}$') AS first_year
FROM graph.style AS style
""",
    "label_stats": """
SELECT on_label.label_id AS label_id,
       count(DISTINCT on_label.release_id) AS release_count,
       count(DISTINCT by_artist.artist_id) AS artist_count,
       count(DISTINCT in_genre.genre_name) AS genre_count
FROM graph.on_label AS on_label
LEFT JOIN graph.by_artist AS by_artist ON by_artist.release_id = on_label.release_id
LEFT JOIN graph.in_genre AS in_genre ON in_genre.release_id = on_label.release_id
GROUP BY on_label.label_id
""",
    # Every edge a Neo4j `:Artist` node carries, counted undirected exactly as
    # `COUNT { (a)--() }` does.
    "artist_degree": """
SELECT endpoint.artist_id AS artist_id, count(*) AS degree
FROM (
    SELECT artist_id FROM graph.by_artist
    UNION ALL SELECT artist_id FROM graph.master_by_artist
    UNION ALL SELECT artist_id FROM graph.same_as
    UNION ALL SELECT member_artist_id AS artist_id FROM graph.member_of
    UNION ALL SELECT group_artist_id AS artist_id FROM graph.member_of
    UNION ALL SELECT alias_artist_id AS artist_id FROM graph.alias_of
    UNION ALL SELECT artist_id FROM graph.alias_of
) AS endpoint
GROUP BY endpoint.artist_id
""",
    # The catalog half of release degree. The personal half is counted live by
    # `graph.release_degree`, which is the one relation split across two owners.
    "release_degree_base": """
SELECT endpoint.release_id AS release_id, count(*) AS degree
FROM (
    SELECT release_id FROM graph.by_artist
    UNION ALL SELECT release_id FROM graph.on_label
    UNION ALL SELECT release_id FROM graph.in_genre
    UNION ALL SELECT release_id FROM graph.in_style
    UNION ALL SELECT release_id FROM graph.derived_from
    UNION ALL SELECT release_id FROM graph.credited_on
    UNION ALL SELECT release_id FROM graph.credited_to
    UNION ALL SELECT release_id FROM graph.issued_on
) AS endpoint
GROUP BY endpoint.release_id
""",
    "artist_genre": """
SELECT by_artist.artist_id AS artist_id,
       in_genre.genre_name AS genre_name,
       count(DISTINCT by_artist.release_id) AS release_count
FROM graph.by_artist AS by_artist
JOIN graph.in_genre AS in_genre ON in_genre.release_id = by_artist.release_id
GROUP BY by_artist.artist_id, in_genre.genre_name
""",
    "label_genre": """
SELECT on_label.label_id AS label_id,
       in_genre.genre_name AS genre_name,
       count(DISTINCT on_label.release_id) AS release_count
FROM graph.on_label AS on_label
JOIN graph.in_genre AS in_genre ON in_genre.release_id = on_label.release_id
GROUP BY on_label.label_id, in_genre.genre_name
""",
}


def phase0_comparison_statements(schema: str) -> list[tuple[str, str]]:
    """Return the statements creating the retained phase 0 views in SCHEMA.

    Test-only. `_schema_statements()` never yields any of this and the shipped
    `graph` schema never holds one of these views once the migration above has
    run. SCHEMA is a caller-chosen identifier, not input from a row or a request.
    """
    statements = [(f"{schema} schema", f"CREATE SCHEMA IF NOT EXISTS {schema}")]
    statements.extend(
        (f"{schema}.{relation} view", f"CREATE OR REPLACE VIEW {schema}.{relation} AS\n{body.strip()}")
        for relation, body in sorted(_phase0_relation_bodies().items())
    )
    return statements


def graph_bootstrap_statements(schema: str) -> list[tuple[str, str]]:
    """Return the statements filling every loader-owned table once, from SCHEMA.

    Test-only, and the reference the loaders implement rather than a shipped
    refresh: `discogs-sql-loader` writes these rows incrementally, in the same
    transaction as the document they came from, and recomputes the counter
    relations on the `extraction_complete` message it already handles.
    """
    statements = [
        (
            f"graph.{relation} bootstrap",
            f"INSERT INTO graph.{relation} ({', '.join(columns)}) "  # noqa: S608
            f"SELECT {', '.join(columns)} FROM {schema}.{relation} ON CONFLICT DO NOTHING",
        )
        for relation, columns in _BOOTSTRAP_COLUMNS.items()
    ]
    statements.extend(
        (f"graph.{relation} bootstrap", f"INSERT INTO graph.{relation}\n{body.strip()}\nON CONFLICT DO NOTHING")
        for relation, body in _COUNTER_BOOTSTRAP.items()
    )
    return statements


# ── The bootstrap fill (shipped) ─────────────────────────────────────────────
# Everything above this line is a definition. `graph.bootstrap_fill()` is the
# one thing in this module that writes a graph row, and it is deliberately a
# function nobody calls rather than a statement the initializer runs: applying
# the schema must stay a declaration, and an environment that wants rows asks
# for them.
#
# **It is not authoritative.** The SQL loaders own every relation it touches —
# `discogs-sql-loader` writes an edge in the same transaction as the document it
# came from and recomputes the counters on the `extraction_complete` latch, and
# `musicbrainz-sql-loader` upserts its half of the shared medium vocabulary.
# This fill exists so an environment can be populated once, before a loader has
# run, so a read rewrite in `catalog-api` is not blocked on the dual-write. The
# first loader pass supersedes it.

# The counter relations' column lists, in the order each table declares them.
# The vertex and edge lists are `_BOOTSTRAP_COLUMNS` above; these are separate
# because the counter bodies are computed from the edge tables rather than
# projected from a retained view, and only the fill needs them named.
_COUNTER_COLUMNS: dict[str, tuple[str, ...]] = {
    "genre_stats": ("name", "release_count", "artist_count", "label_count", "style_count", "first_year"),
    "style_stats": ("name", "release_count", "artist_count", "label_count", "genre_count", "first_year"),
    "label_stats": ("label_id", "release_count", "artist_count", "genre_count"),
    "artist_degree": ("artist_id", "degree"),
    "release_degree_base": ("release_id", "degree"),
    "artist_genre": ("artist_id", "genre_name", "release_count"),
    "label_genre": ("label_id", "genre_name", "release_count"),
}

# The order the fill runs in, and every group boundary in it is load-bearing.
#
# - **Vertices before edges.** `part_of` and `in_family` inner-join the vertex
#   tables, so an edge written before its endpoints exist is silently absent
#   rather than wrong, and `genre_stats` reads `part_of`.
# - **Edges before counters.** Every counter's count is a sum over the edge
#   tables — genre_stats and style_stats also join graph.release for
#   first_year, a document-backed view, but the counts themselves read no
#   document; filled first they would sum an empty relation and report a
#   converged zero.
# - **`artist_genre` and `label_genre` last**, with the other counters, because
#   both join `by_artist`/`on_label` to `in_genre`.
_BOOTSTRAP_FILL_ORDER: tuple[str, ...] = (*_MATERIALIZED_VERTICES, *_MATERIALIZED_EDGES, *tuple(_COUNTER_BOOTSTRAP))


def _bootstrap_fill_source(relation: str) -> tuple[tuple[str, ...], str]:
    """Return the columns one relation is filled with, and the body they come from.

    The body is the phase 0 view's own, read out of `_phase0_relation_bodies()`
    rather than copied, which is the whole reason those definitions are retained
    in this module instead of in the test tree: the fill and the parity
    comparison cannot disagree about what a relation projects, because there is
    one text and both read it.
    """
    if relation in _COUNTER_BOOTSTRAP:
        return _COUNTER_COLUMNS[relation], _COUNTER_BOOTSTRAP[relation]
    return _BOOTSTRAP_COLUMNS[relation], _phase0_relation_bodies()[relation]


def _bootstrap_fill_step(relation: str) -> str:
    """Return the plpgsql that empties one relation, refills it, and reports the count.

    `TRUNCATE` then `INSERT`, rather than an upsert: `ON CONFLICT DO NOTHING`
    converges upward only. A row the documents no longer justify — a release
    whose genre was corrected, a credit that was removed — would survive every
    re-run, so the relation would drift away from its own definition instead of
    toward it. Emptying it first makes the relation exactly the projection of
    the documents present, which is what "idempotent" has to mean here.

    Nothing names a conflict target because nothing can conflict: every body is
    unique on its table's key, by a `DISTINCT`, a `DISTINCT ON`, a `GROUP BY`, or
    a `UNION` over exactly those columns. A duplicate would raise rather than be
    dropped in silence, which is the right failure for a projection that claims
    to be the key.
    """
    columns, body = _bootstrap_fill_source(relation)
    indented = "\n".join(f"        {line}" if line.strip() else "" for line in body.strip().splitlines())
    projection = ", ".join(columns)
    return f"""    TRUNCATE graph.{relation};
    INSERT INTO graph.{relation} ({projection})
    SELECT {projection}
    FROM (
{indented}
    ) AS bootstrap;
    GET DIAGNOSTICS row_count = ROW_COUNT;
    relation := 'graph.{relation}';
    RAISE NOTICE 'bootstrap_fill: % <- % row(s)', relation, row_count;
    RETURN NEXT;
"""  # noqa: S608


def _bootstrap_fill_function() -> str:
    """Return `graph.bootstrap_fill()`, the one-off fill of every loader-owned table.

    One transaction, because the caller's statement is one: `SELECT * FROM
    graph.bootstrap_fill()` either replaces all twenty-seven relations or
    replaces none, so a failure half way through cannot leave edges pointing at
    vertices that were truncated and never refilled.

    It reports a row per relation, in fill order, and raises the same line as a
    `NOTICE` so a psql session watching a long fill sees progress rather than
    silence until the end.

    `credited_on.role_category` is projected away rather than written: it is a
    generated column, and naming it in an `INSERT` is an error rather than an
    overwrite. Dropping it from the outer list costs nothing — it is a function
    of `role`, which is in the key, so the `DISTINCT` inside the body yields the
    same rows with or without it.

    One known difference from what the loaders write. `graph.company` keys a
    company with no Discogs id on `name:` followed by the name case-folded, and
    this fill folds it with SQL `lower`, which is only an approximation of
    Python's `str.casefold` — they disagree on the German eszett and a handful
    of other characters. `discogs-sql-loader` applies the rule itself, so for
    those few names the loader's id is the right one and this fill's is not.
    The loader's row wins, as it does for every relation here.
    """
    steps = "\n".join(_bootstrap_fill_step(relation) for relation in _BOOTSTRAP_FILL_ORDER)
    return f"""CREATE OR REPLACE FUNCTION graph.bootstrap_fill()
RETURNS TABLE (relation text, row_count bigint)
LANGUAGE plpgsql
AS $bootstrap_fill$
#variable_conflict use_column
-- The two output columns are in scope for every statement below, so a body
-- free to publish either name resolves to its own column rather than failing.
BEGIN
{steps}END
$bootstrap_fill$"""


def _build_graph_statements() -> list[tuple[str, str]]:
    """Return the ordered graph-schema statements: schema, functions, tables, views.

    The order is load-bearing four times over. The rendered vocabulary
    functions precede everything, because `graph.credited_on` generates a column
    with one of them and four views call the others. The tables precede the
    views, because `part_of`, `in_family`, and `release_degree` now read tables
    rather than documents. Every migration that frees a name precedes the
    relation that takes it, which is what `_graph_table_statements` returns. And
    `graph.bootstrap_fill` comes last, after every relation it writes or reads —
    a plpgsql body resolves its names at first call rather than at creation, so
    the position is a statement about what the function means rather than a
    requirement, and it is kept honest by a test.
    """
    return [
        _GRAPH_SCHEMA_STATEMENT,
        _PG_TRGM_STATEMENT,
        # The rendered vocabulary functions precede the relations that call them.
        ("graph.credit_role_category function", _credit_role_category_function()),
        ("graph.medium_label function", _medium_label_function()),
        ("graph.mb_relationship_type function", _mb_relationship_type_function()),
        *_graph_table_statements(),
        *_discogs_vertex_views(),
        *_discogs_edge_views(),
        *_musicbrainz_vertex_views(),
        *_musicbrainz_edge_views(),
        *_collection_views(),
        *_credit_views(),
        *_counter_views(),
        # Last, because it reads all of it: the fill writes the tables above and
        # its counter bodies read `graph.part_of`, which is one of the views.
        ("graph.bootstrap_fill function", _bootstrap_fill_function()),
    ]


_GRAPH_STATEMENTS: list[tuple[str, str]] = _build_graph_statements()


# ── The catalog property graph (SQL/PGQ, PostgreSQL 19) ──────────────────────
# `CREATE PROPERTY GRAPH` re-presents the graph schema's views as one named
# graph a `GRAPH_TABLE` query can pattern-match over. It is a declaration, not
# a materialization: every vertex and edge is read from the view underneath it
# at query time, so the graph costs nothing to hold and nothing to refresh.
#
# The statement is applied only on a PostgreSQL 19 server and only when the
# `SCHEMA_PROPERTY_GRAPH` switch is enabled, so production on 18 is untouched
# and the cutover is a configuration change. See `_property_graph_skip_reason`.

PROPERTY_GRAPH_SWITCH = "SCHEMA_PROPERTY_GRAPH"
PROPERTY_GRAPH_SCHEMA = "graph"
PROPERTY_GRAPH_RELATION = "catalog"
PROPERTY_GRAPH_NAME = f"{PROPERTY_GRAPH_SCHEMA}.{PROPERTY_GRAPH_RELATION}"
PROPERTY_GRAPH_STATEMENT_NAME = f"{PROPERTY_GRAPH_NAME} property graph"

# SQL/PGQ landed in PostgreSQL 19. 190000 is `server_version_num` for 19beta1
# onward, which is what the advisory integration tier runs.
PROPERTY_GRAPH_MINIMUM_SERVER_VERSION = 190000

# The shared label the sixteen `mb_rel_<source>_<target>` edge relations carry in
# addition to their own. SQL/PGQ allows one label across several element tables
# only when every one of them exposes the same property names and types, which
# these sixteen do: each projects the same eight columns of
# `musicbrainz.relationships`. The shared label is what lets a query ask for any
# MusicBrainz relationship without spelling out all sixteen endpoint pairs.
MUSICBRAINZ_RELATIONSHIP_LABEL = "mb_related"


class _PropertyGraphVertex(NamedTuple):
    """One vertex element table: its label, its key, and its properties.

    `view` is the alias and the label. `relation` is the graph-schema relation
    underneath it, which differs from the label only for the four labels that
    carry counters: those bind a `<label>_vertex` projection rather than the
    storage relation of the same name. See `_counter_views`.
    """

    view: str
    key: tuple[str, ...]
    # None means PROPERTIES ALL COLUMNS.
    properties: tuple[str, ...] | None = None
    relation: str | None = None

    @property
    def element(self) -> str:
        """Return the graph-schema relation this element table reads."""
        return self.relation or self.view


class _PropertyGraphEdge(NamedTuple):
    """One edge element table, with the vertex aliases its endpoints resolve to."""

    view: str
    key: tuple[str, ...]
    source_key: tuple[str, ...]
    source: str
    source_columns: tuple[str, ...]
    destination_key: tuple[str, ...]
    destination: str
    destination_columns: tuple[str, ...]
    properties: tuple[str, ...] | None = None
    extra_labels: tuple[str, ...] = ()

    @property
    def element(self) -> str:
        """Return the graph-schema relation this element table reads.

        No edge label binds a relation of a different name, so this is always
        the label itself. It exists so a caller can walk vertices and edges
        together without knowing which kind it is holding.
        """
        return self.view


def _property_graph_vertices() -> tuple[_PropertyGraphVertex, ...]:
    """Return every vertex element table of `graph.catalog`.

    Every key is `text`, `uuid`, or `bigint` — never `character varying`.
    PostgreSQL 19 beta 3 looks the equality operator up against the referenced
    column's own type and `varchar` registers none of its own, so a `character
    varying` vertex key makes every edge that points at it unresolvable. The
    phase 0 declaration worked around that with four appended `<entity>_key`
    restatements; the relations now publish `text` keys directly and the
    workaround is retired. Labels and property names are unchanged by that: only
    the key types moved.

    The explicit property lists that remain are the two casts SQL/PGQ still
    forces. A property name must have one data type across the whole graph, and
    `discogs_label_id` is `bigint` on the MusicBrainz side and `text` on the
    Discogs side, while `release_id` is `text` on every graph relation except
    `graph.collected` and `graph.wants`, which read `releases.data_id` through a
    join and publish it as `character varying`. Each is unified on `text`.

    Four labels bind a relation of a different name. `genre`, `style`, `label`,
    and `artist` read a `<label>_vertex` projection that joins the relation
    holding their rows to the relation holding their counters, because Neo4j
    carries those counters as node properties of exactly those four labels and
    exact parity is the point: `MATCH (g IS genre) COLUMNS (g.release_count)`
    reads as the Cypher it replaces. Attaching the counter relation to the same
    label as a second element table is not an option — SQL/PGQ admits one
    element table per label unless every table exposes an identical property
    set, and PostgreSQL 19 beta 3 refuses the pair with `mismatching number of
    properties in definition of label "genre"`. A view is one element table.

    The projection is free when it is not read: the counter relation is unique
    on the join column, so the planner removes the LEFT JOIN outright for a
    query that names no counter, and the pilot two-hop plans identically over
    the projection and over the storage relation alone.

    `release_degree` is the one counter that stays a label of its own; see
    `_counter_views` for the measurement behind that.
    """
    return (
        _PropertyGraphVertex("artist", ("artist_id",), relation="artist_vertex"),
        _PropertyGraphVertex("label", ("label_id",), relation="label_vertex"),
        _PropertyGraphVertex("master", ("master_id",)),
        _PropertyGraphVertex("release", ("release_id",)),
        _PropertyGraphVertex("genre", ("name",), relation="genre_vertex"),
        _PropertyGraphVertex("style", ("name",), relation="style_vertex"),
        _PropertyGraphVertex("person", ("name",)),
        _PropertyGraphVertex("company", ("company_id",)),
        _PropertyGraphVertex("medium", ("medium_id",)),
        _PropertyGraphVertex("media_family", ("name",)),
        _PropertyGraphVertex("app_user", ("user_id",)),
        _PropertyGraphVertex("catalog_item", ("item_id",)),
        _PropertyGraphVertex("mb_artist", ("mbid",)),
        _PropertyGraphVertex(
            "mb_label",
            ("mbid",),
            (
                "mbid",
                "name",
                "type",
                "label_code",
                "begin_date",
                "end_date",
                "ended",
                "area",
                "disambiguation",
                "discogs_label_id::text AS discogs_label_id",
                "updated_at",
            ),
        ),
        _PropertyGraphVertex("mb_release", ("mbid",)),
        _PropertyGraphVertex("mb_release_group", ("mbid",)),
        # The one counter that is not a property of the label it describes. Its
        # live half is a pair of lateral counts no unique key makes removable,
        # so folding it onto `release` would double the plan of every traversal
        # that binds a release. The counter relations behind the other four are
        # loader-owned storage and are deliberately NOT declared as labels of
        # their own: every property they carry is reachable on the Neo4j label,
        # so a second label would be surface with no query behind it.
        _PropertyGraphVertex("release_degree", ("release_id",)),
    )


def _musicbrainz_relationship_edges() -> list[_PropertyGraphEdge]:
    """Return the sixteen MusicBrainz relationship edge tables.

    Each is keyed on the surrogate `relationship_id` — `musicbrainz.relationships`
    has one row per relationship and both endpoint joins are to a unique mbid, so
    the id stays unique through the view.
    """
    edges: list[_PropertyGraphEdge] = []
    for _source_type, _source_table, source_name in _MUSICBRAINZ_GRAPH_ENTITIES:
        for _target_type, _target_table, target_name in _MUSICBRAINZ_GRAPH_ENTITIES:
            edges.append(
                _PropertyGraphEdge(
                    view=f"mb_rel_{source_name}_{target_name}",
                    key=("relationship_id",),
                    source_key=("source_mbid",),
                    source=f"mb_{source_name}",
                    source_columns=("mbid",),
                    destination_key=("target_mbid",),
                    destination=f"mb_{target_name}",
                    destination_columns=("mbid",),
                    extra_labels=(MUSICBRAINZ_RELATIONSHIP_LABEL,),
                )
            )
    return edges


def _property_graph_edges() -> tuple[_PropertyGraphEdge, ...]:
    """Return every edge element table of `graph.catalog`.

    Every key is the column set docs/architecture.md publishes for that
    relation, and every endpoint now resolves to the published key column of its
    vertex rather than to an appended restatement of it. `graph.collected` and
    `graph.wants` carry the one remaining cast: their `release_id` comes through
    a join on `releases.data_id` and is `character varying`, which is a legal
    endpoint type but the wrong property type, so it is published as `text`.
    """
    return (
        _PropertyGraphEdge(
            "by_artist", ("release_id", "artist_id"), ("release_id",), "release", ("release_id",), ("artist_id",), "artist", ("artist_id",)
        ),
        _PropertyGraphEdge(
            "on_label", ("release_id", "label_id"), ("release_id",), "release", ("release_id",), ("label_id",), "label", ("label_id",)
        ),
        _PropertyGraphEdge(
            "derived_from", ("release_id", "master_id"), ("release_id",), "release", ("release_id",), ("master_id",), "master", ("master_id",)
        ),
        _PropertyGraphEdge(
            "in_genre", ("release_id", "genre_name"), ("release_id",), "release", ("release_id",), ("genre_name",), "genre", ("name",)
        ),
        _PropertyGraphEdge(
            "in_style", ("release_id", "style_name"), ("release_id",), "release", ("release_id",), ("style_name",), "style", ("name",)
        ),
        _PropertyGraphEdge(
            "master_by_artist", ("master_id", "artist_id"), ("master_id",), "master", ("master_id",), ("artist_id",), "artist", ("artist_id",)
        ),
        _PropertyGraphEdge(
            "master_in_genre", ("master_id", "genre_name"), ("master_id",), "master", ("master_id",), ("genre_name",), "genre", ("name",)
        ),
        _PropertyGraphEdge(
            "master_in_style", ("master_id", "style_name"), ("master_id",), "master", ("master_id",), ("style_name",), "style", ("name",)
        ),
        _PropertyGraphEdge("part_of", ("style_name", "genre_name"), ("style_name",), "style", ("name",), ("genre_name",), "genre", ("name",)),
        _PropertyGraphEdge(
            "member_of",
            ("member_artist_id", "group_artist_id"),
            ("member_artist_id",),
            "artist",
            ("artist_id",),
            ("group_artist_id",),
            "artist",
            ("artist_id",),
        ),
        _PropertyGraphEdge(
            "alias_of", ("alias_artist_id", "artist_id"), ("alias_artist_id",), "artist", ("artist_id",), ("artist_id",), "artist", ("artist_id",)
        ),
        _PropertyGraphEdge(
            "sublabel_of", ("sublabel_id", "parent_label_id"), ("sublabel_id",), "label", ("label_id",), ("parent_label_id",), "label", ("label_id",)
        ),
        _PropertyGraphEdge(
            "credited_on",
            ("person_name", "release_id", "role"),
            ("person_name",),
            "person",
            ("name",),
            ("release_id",),
            "release",
            ("release_id",),
        ),
        _PropertyGraphEdge("same_as", ("person_name", "artist_id"), ("person_name",), "person", ("name",), ("artist_id",), "artist", ("artist_id",)),
        _PropertyGraphEdge(
            "credited_to",
            ("release_id", "company_id", "role", "source"),
            ("release_id",),
            "release",
            ("release_id",),
            ("company_id",),
            "company",
            ("company_id",),
        ),
        _PropertyGraphEdge(
            "issued_on",
            ("release_id", "medium_id", "source"),
            ("release_id",),
            "release",
            ("release_id",),
            ("medium_id",),
            "medium",
            ("medium_id",),
        ),
        _PropertyGraphEdge(
            "in_family", ("medium_id", "family_name"), ("medium_id",), "medium", ("medium_id",), ("family_name",), "media_family", ("name",)
        ),
        # The two genre aggregates the owner asked for. They have no Neo4j
        # counterpart: `graphinator` never wrote an artist-to-genre edge, and
        # catalog-api answers the question today by walking `by_artist` into
        # `in_genre` and counting. A keyed pair with the count on it replaces a
        # two-hop expansion with one index read.
        _PropertyGraphEdge(
            "artist_genre", ("artist_id", "genre_name"), ("artist_id",), "artist", ("artist_id",), ("genre_name",), "genre", ("name",)
        ),
        _PropertyGraphEdge("label_genre", ("label_id", "genre_name"), ("label_id",), "label", ("label_id",), ("genre_name",), "genre", ("name",)),
        _PropertyGraphEdge(
            "collected",
            ("collection_id",),
            ("user_id",),
            "app_user",
            ("user_id",),
            ("release_id",),
            "release",
            ("release_id",),
            ("collection_id", "user_id", "release_id::text AS release_id", "instance_id", "folder_id", "condition", "rating", "date_added"),
        ),
        _PropertyGraphEdge(
            "wants",
            ("wantlist_id",),
            ("user_id",),
            "app_user",
            ("user_id",),
            ("release_id",),
            "release",
            ("release_id",),
            ("wantlist_id", "user_id", "release_id::text AS release_id", "rating", "date_added"),
        ),
        _PropertyGraphEdge("owns", ("owned_copy_id",), ("user_id",), "app_user", ("user_id",), ("item_id",), "catalog_item", ("item_id",)),
        *_musicbrainz_relationship_edges(),
    )


def _columns(names: Iterable[str]) -> str:
    """Return NAMES as a parenthesized SQL column list."""
    return "(" + ", ".join(names) + ")"


def _labels_and_properties(view: str, properties: tuple[str, ...] | None, extra_labels: tuple[str, ...] = ()) -> str:
    """Return the LABEL and PROPERTIES clauses for one element table.

    The label is the view name verbatim. That is the whole point of the naming
    rule ADR 0012 records and docs/architecture.md restates: `:User` is projected
    as `graph.app_user` and the overloaded `[:BY]`, `[:ON]`, and `[:IS]` types as
    `by_artist`, `on_label`, `in_genre`, and `in_style`, so no label here needs
    quoting and none collides with a SQL reserved word.
    """
    rendered = "PROPERTIES ALL COLUMNS" if properties is None else "PROPERTIES (" + ", ".join(properties) + ")"
    clauses = [f"LABEL {view} {rendered}"]
    clauses.extend(f"LABEL {label} {rendered}" for label in extra_labels)
    return " ".join(clauses)


def _property_graph_statement() -> str:
    """Render CREATE PROPERTY GRAPH graph.catalog over the graph schema views."""
    vertices = [
        f"        {PROPERTY_GRAPH_SCHEMA}.{vertex.element} AS {vertex.view} KEY {_columns(vertex.key)}\n"
        f"            {_labels_and_properties(vertex.view, vertex.properties)}"
        for vertex in _property_graph_vertices()
    ]
    edges = [
        f"        {PROPERTY_GRAPH_SCHEMA}.{edge.view} AS {edge.view} KEY {_columns(edge.key)}\n"
        f"            SOURCE KEY {_columns(edge.source_key)} REFERENCES {edge.source} {_columns(edge.source_columns)}\n"
        f"            DESTINATION KEY {_columns(edge.destination_key)} REFERENCES {edge.destination} {_columns(edge.destination_columns)}\n"
        f"            {_labels_and_properties(edge.view, edge.properties, edge.extra_labels)}"
        for edge in _property_graph_edges()
    ]
    return (
        f"CREATE PROPERTY GRAPH {PROPERTY_GRAPH_NAME}\n"
        "    VERTEX TABLES (\n" + ",\n".join(vertices) + "\n    )\n"
        "    EDGE TABLES (\n" + ",\n".join(edges) + "\n    )"
    )


# The (name, statement) pair, in the same shape as every entry of
# `_schema_statements()`. It is deliberately not yielded from there: the
# statement is conditional on the server and on an operator switch, and
# `_schema_statements()` is the unconditional schema every supported engine gets.
PROPERTY_GRAPH_STATEMENT: tuple[str, str] = (PROPERTY_GRAPH_STATEMENT_NAME, _property_graph_statement())


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
    # Last: every view in the graph schema reads a table declared above.
    yield from _GRAPH_STATEMENTS


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


# The spellings that turn the switch on. `enabled` is the documented value; the
# rest are the usual truthy spellings an operator is likely to reach for, so a
# deployment that writes `true` does not silently get the default.
_PROPERTY_GRAPH_ENABLED_VALUES = frozenset({"1", "enable", "enabled", "on", "true", "yes"})


def property_graph_enabled() -> bool:
    """Return whether the SCHEMA_PROPERTY_GRAPH switch is on. Defaults to off."""
    return os.environ.get(PROPERTY_GRAPH_SWITCH, "").strip().lower() in _PROPERTY_GRAPH_ENABLED_VALUES


def _property_graph_skip_reason(*, enabled: bool, server_version_num: int | None, already_exists: bool) -> str | None:
    """Return why `graph.catalog` is not being created, or None to create it.

    The three gates are ordered by cost. The switch is read from the environment
    and settles the common case without a round trip; the server version is one
    query; the catalog check is the last one and is what makes a second apply a
    no-op. `CREATE PROPERTY GRAPH` has no `IF NOT EXISTS` spelling and nothing in
    this schema ever drops a relation a consumer may be reading, so an existing
    relation of that name is left exactly as it is.
    """
    if not enabled:
        return f"{PROPERTY_GRAPH_SWITCH} is not enabled"
    if server_version_num is None or server_version_num < PROPERTY_GRAPH_MINIMUM_SERVER_VERSION:
        return f"server_version_num {server_version_num} is below {PROPERTY_GRAPH_MINIMUM_SERVER_VERSION}"
    if already_exists:
        return f"{PROPERTY_GRAPH_NAME} already exists"
    return None


# A property graph is a relation, so an existing one shows up in `pg_class` under
# its own relkind. Checking `pg_class` rather than a version-specific catalog view
# also catches a table or view squatting the name, which is the conservative
# answer: this schema never drops what it did not just create.
_PROPERTY_GRAPH_EXISTS_QUERY = """
SELECT EXISTS (
    SELECT 1
    FROM pg_class AS relation
    JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = %s AND relation.relname = %s
)
"""

_SERVER_VERSION_QUERY = "SELECT current_setting('server_version_num')::int"


async def _server_version_num(cursor: Any) -> int | None:
    """Return the connected server's `server_version_num`, or None if unreadable."""
    try:
        await cursor.execute(_SERVER_VERSION_QUERY)
        row = await cursor.fetchone()
    except Exception as error:
        logger.error("❌ Could not read server_version_num: %s", error)
        return None
    return None if row is None else int(row[0])


async def _property_graph_exists(cursor: Any) -> bool:
    """Return whether a relation named `catalog` already exists in schema `graph`."""
    await cursor.execute(_PROPERTY_GRAPH_EXISTS_QUERY, (PROPERTY_GRAPH_SCHEMA, PROPERTY_GRAPH_RELATION))
    row = await cursor.fetchone()
    return bool(row is not None and row[0])


async def _apply_property_graph(cursor: Any) -> int:
    """Create `graph.catalog` when the server and the switch both allow it.

    Returns the number of failed statements, so the caller adds it to the same
    count every other schema statement contributes to. A skip is not a failure:
    running on PostgreSQL 18, or with the switch off, is the supported default
    and logs one line saying which gate closed.
    """
    enabled = property_graph_enabled()
    server_version_num = await _server_version_num(cursor) if enabled else None
    already_exists = (
        await _property_graph_exists(cursor)
        if enabled and server_version_num is not None and server_version_num >= PROPERTY_GRAPH_MINIMUM_SERVER_VERSION
        else False
    )
    reason = _property_graph_skip_reason(enabled=enabled, server_version_num=server_version_num, already_exists=already_exists)
    if reason is not None:
        logger.info("⏭️  Skipped %s: %s", PROPERTY_GRAPH_NAME, reason)
        return 0
    _success, failures = await _execute_schema_statements(cursor, [PROPERTY_GRAPH_STATEMENT])
    return failures


async def create_postgres_schema(pool: Any) -> int:
    """Create all PostgreSQL tables and indexes.

    Safe to call on every startup; all statements use IF NOT EXISTS so
    subsequent calls are no-ops for already-created schema objects. The catalog
    property graph is applied after them, and only when the server is
    PostgreSQL 19 or later and the SCHEMA_PROPERTY_GRAPH switch is enabled; see
    `_apply_property_graph`.

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
            # Last, and only when the server and the operator both allow it.
            failure_count += await _apply_property_graph(cursor)

    total = len(statements)
    logger.info(f"✅ PostgreSQL schema creation complete: {success_count} succeeded, {failure_count} failed (total: {total})")
    return failure_count
