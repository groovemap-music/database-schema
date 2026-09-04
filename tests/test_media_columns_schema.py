"""Tests for the canonical media JSONB columns, GIN indexes, and rarity columns.

Covers ADR 0007 (canonical media taxonomy): `media JSONB` on every release-shaped
table (releases, musicbrainz.releases, user_collections, user_wantlists), a GIN
index on `media->'families'` for releases, musicbrainz.releases, and
user_collections, and the additive media-neutral rarity columns on
insights.release_rarity.
"""

from groovemap_schema.postgres import (
    _INSIGHTS_TABLES,
    _MUSICBRAINZ_INDEXES,
    _MUSICBRAINZ_TABLES,
    _SPECIFIC_INDEXES,
    _USER_TABLES,
)


class TestReleasesMediaColumn:
    """Discogs `releases` table (public schema)."""

    def test_media_column_added(self) -> None:
        stmts = dict(_SPECIFIC_INDEXES)
        assert "releases add media column" in stmts
        assert stmts["releases add media column"] == "ALTER TABLE releases ADD COLUMN IF NOT EXISTS media JSONB"

    def test_media_column_is_idempotent(self) -> None:
        stmts = dict(_SPECIFIC_INDEXES)
        assert "IF NOT EXISTS" in stmts["releases add media column"]

    def test_media_families_gin_index_defined(self) -> None:
        names = [name for name, _stmt in _SPECIFIC_INDEXES]
        assert "idx_releases_media_families" in names

    def test_media_families_gin_index_shape(self) -> None:
        stmt = dict(_SPECIFIC_INDEXES)["idx_releases_media_families"]
        assert "ON releases USING GIN" in stmt
        assert "media->'families'" in stmt
        assert "IF NOT EXISTS" in stmt

    def test_media_column_precedes_its_index(self) -> None:
        """The ALTER must run before the GIN index that reads the new column."""
        names = [name for name, _stmt in _SPECIFIC_INDEXES]
        assert names.index("releases add media column") < names.index("idx_releases_media_families")


class TestMusicBrainzReleasesMediaColumn:
    """musicbrainz.releases table."""

    def test_media_column_in_create_table(self) -> None:
        stmt = dict(_MUSICBRAINZ_TABLES)["musicbrainz.releases table"]
        assert "media JSONB" in stmt

    def test_media_column_migration_present(self) -> None:
        stmts = dict(_MUSICBRAINZ_TABLES)
        assert "musicbrainz.releases.media column" in stmts
        migration = stmts["musicbrainz.releases.media column"]
        assert migration == "ALTER TABLE musicbrainz.releases ADD COLUMN IF NOT EXISTS media JSONB"

    def test_media_families_gin_index_defined(self) -> None:
        names = [name for name, _stmt in _MUSICBRAINZ_INDEXES]
        assert "idx_mb_releases_media_families" in names

    def test_media_families_gin_index_shape(self) -> None:
        stmt = dict(_MUSICBRAINZ_INDEXES)["idx_mb_releases_media_families"]
        assert "ON musicbrainz.releases USING GIN" in stmt
        assert "media->'families'" in stmt
        assert "IF NOT EXISTS" in stmt

    def test_tables_run_before_indexes(self) -> None:
        """_MUSICBRAINZ_TABLES and _MUSICBRAINZ_INDEXES are executed as one
        concatenated list in table-then-index order, so the ALTER only needs to
        live anywhere in _MUSICBRAINZ_TABLES, not necessarily before the CREATE
        TABLE entry it augments."""
        table_names = [name for name, _stmt in _MUSICBRAINZ_TABLES]
        assert "musicbrainz.releases.media column" in table_names


class TestUserCollectionsMediaColumn:
    """user_collections table."""

    def test_media_column_in_create_table(self) -> None:
        stmt = dict(_USER_TABLES)["user_collections table"]
        assert "media        JSONB" in stmt

    def test_media_column_migration_present(self) -> None:
        stmts = dict(_USER_TABLES)
        assert "user_collections.media column" in stmts
        assert stmts["user_collections.media column"] == "ALTER TABLE user_collections ADD COLUMN IF NOT EXISTS media JSONB"

    def test_media_families_gin_index_defined(self) -> None:
        names = [name for name, _stmt in _USER_TABLES]
        assert "idx_user_collections_media_families" in names

    def test_media_families_gin_index_shape(self) -> None:
        stmt = dict(_USER_TABLES)["idx_user_collections_media_families"]
        assert "ON user_collections USING GIN" in stmt
        assert "media->'families'" in stmt
        assert "IF NOT EXISTS" in stmt

    def test_formats_column_retained(self) -> None:
        """The legacy `formats` array survives as provenance alongside `media`."""
        stmt = dict(_USER_TABLES)["user_collections table"]
        assert "formats      JSONB" in stmt

    def test_media_column_precedes_its_index(self) -> None:
        names = [name for name, _stmt in _USER_TABLES]
        assert names.index("user_collections table") < names.index("idx_user_collections_media_families")


class TestUserWantlistsMediaColumn:
    """user_wantlists table."""

    def test_media_column_in_create_table(self) -> None:
        stmt = dict(_USER_TABLES)["user_wantlists table"]
        assert "media      JSONB" in stmt

    def test_media_column_migration_present(self) -> None:
        stmts = dict(_USER_TABLES)
        assert "user_wantlists.media column" in stmts
        assert stmts["user_wantlists.media column"] == "ALTER TABLE user_wantlists ADD COLUMN IF NOT EXISTS media JSONB"

    def test_format_column_retained(self) -> None:
        """The legacy scalar `format` column survives as provenance alongside `media`."""
        stmt = dict(_USER_TABLES)["user_wantlists table"]
        assert "format     VARCHAR(255)" in stmt

    def test_no_media_families_gin_index_required(self) -> None:
        """Unlike releases, musicbrainz.releases, and user_collections, the
        wantlist table has no acceptance criterion for a GIN index."""
        names = [name for name, _stmt in _USER_TABLES]
        assert "idx_user_wantlists_media_families" not in names


class TestReleaseRarityMediaColumns:
    """insights.release_rarity gains media-neutral rarity signals."""

    def _insights_dict(self) -> dict[str, str]:
        return dict(_INSIGHTS_TABLES)

    def test_media_families_column_added(self) -> None:
        stmts = self._insights_dict()
        assert "insights.release_rarity add media_families" in stmts
        assert (
            stmts["insights.release_rarity add media_families"] == "ALTER TABLE insights.release_rarity ADD COLUMN IF NOT EXISTS media_families JSONB"
        )

    def test_family_signals_column_added(self) -> None:
        stmts = self._insights_dict()
        assert "insights.release_rarity add family_signals" in stmts
        assert (
            stmts["insights.release_rarity add family_signals"] == "ALTER TABLE insights.release_rarity ADD COLUMN IF NOT EXISTS family_signals JSONB"
        )

    def test_medium_rarity_column_added(self) -> None:
        stmts = self._insights_dict()
        assert "insights.release_rarity add medium_rarity" in stmts
        assert stmts["insights.release_rarity add medium_rarity"] == "ALTER TABLE insights.release_rarity ADD COLUMN IF NOT EXISTS medium_rarity REAL"

    def test_format_rarity_retained(self) -> None:
        """The descriptor-keyed signal is retained; media-neutral rarity is additive."""
        ddl = ""
        for name, stmt in _INSIGHTS_TABLES:
            if name == "insights.release_rarity table":
                ddl = stmt
                break
        assert "format_rarity" in ddl

    def test_all_media_migrations_are_idempotent(self) -> None:
        for name in (
            "insights.release_rarity add media_families",
            "insights.release_rarity add family_signals",
            "insights.release_rarity add medium_rarity",
        ):
            assert "IF NOT EXISTS" in self._insights_dict()[name]
