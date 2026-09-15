"""Neo4j schema definitions: constraints and indexes for GrooveMap.

Single source of truth for all Neo4j constraints and indexes.
All statements use IF NOT EXISTS — safe to run on every startup; subsequent
runs are no-ops for already-created schema objects.

Ordering: constraints are listed first because each unique constraint implicitly
creates a backing range index on the constrained property. Standalone range/
fulltext indexes are listed after, so there is no property overlap and no risk
of conflicts between constraint-backed indexes and explicit range indexes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Iterable


logger = logging.getLogger(__name__)


# All schema statements in creation order.
# Constraints first (they implicitly create backing range indexes),
# then additional range indexes, then fulltext indexes.
SCHEMA_STATEMENTS: list[tuple[str, str]] = [
    # ── Unique constraints ────────────────────────────────────────────────────
    # Each constraint implicitly creates a backing range index on the property.
    # Do NOT add explicit range indexes for Artist.id, Label.id, Master.id,
    # Release.id, Genre.name, Style.name, Medium.id, or MediaFamily.name — they
    # would conflict.
    (
        "artist_id",
        "CREATE CONSTRAINT artist_id IF NOT EXISTS FOR (a:Artist) REQUIRE a.id IS UNIQUE",
    ),
    (
        "label_id",
        "CREATE CONSTRAINT label_id IF NOT EXISTS FOR (l:Label) REQUIRE l.id IS UNIQUE",
    ),
    (
        "master_id",
        "CREATE CONSTRAINT master_id IF NOT EXISTS FOR (m:Master) REQUIRE m.id IS UNIQUE",
    ),
    (
        "release_id",
        "CREATE CONSTRAINT release_id IF NOT EXISTS FOR (r:Release) REQUIRE r.id IS UNIQUE",
    ),
    (
        "genre_name",
        "CREATE CONSTRAINT genre_name IF NOT EXISTS FOR (g:Genre) REQUIRE g.name IS UNIQUE",
    ),
    (
        "style_name",
        "CREATE CONSTRAINT style_name IF NOT EXISTS FOR (s:Style) REQUIRE s.name IS UNIQUE",
    ),
    # Medium and MediaFamily back the canonical media taxonomy (ADR 0007):
    # (:Medium)-[:IN_FAMILY]->(:MediaFamily) and (:Release)-[:ISSUED_ON {qty, source}]->(:Medium).
    (
        "medium_id",
        "CREATE CONSTRAINT medium_id IF NOT EXISTS FOR (md:Medium) REQUIRE md.id IS UNIQUE",
    ),
    (
        "media_family_name",
        "CREATE CONSTRAINT media_family_name IF NOT EXISTS FOR (mf:MediaFamily) REQUIRE mf.name IS UNIQUE",
    ),
    (
        "user_id",
        "CREATE CONSTRAINT user_id IF NOT EXISTS FOR (u:User) REQUIRE u.id IS UNIQUE",
    ),
    (
        "person_name",
        "CREATE CONSTRAINT person_name IF NOT EXISTS FOR (p:Person) REQUIRE p.name IS UNIQUE",
    ),
    # Company backs the manufacturing-credit edge (ADR 0011):
    # (:Release)-[:CREDITED_TO {role, role_category, source}]->(:Company).
    (
        "company_id",
        "CREATE CONSTRAINT company_id IF NOT EXISTS FOR (c:Company) REQUIRE c.id IS UNIQUE",
    ),
    # ── Range indexes ─────────────────────────────────────────────────────────
    # gm_id indexes (ADR 0009: native identity and provider aliases). Nodes keep
    # their provider `id` and its uniqueness constraint above; `gm_id` is an
    # additive property set by the catalog-api projection job and is null until
    # projected, so it gets an explicit range index rather than a constraint.
    (
        "artist_gm_id",
        "CREATE INDEX artist_gm_id IF NOT EXISTS FOR (a:Artist) ON (a.gm_id)",
    ),
    (
        "label_gm_id",
        "CREATE INDEX label_gm_id IF NOT EXISTS FOR (l:Label) ON (l.gm_id)",
    ),
    (
        "master_gm_id",
        "CREATE INDEX master_gm_id IF NOT EXISTS FOR (m:Master) ON (m.gm_id)",
    ),
    (
        "release_gm_id",
        "CREATE INDEX release_gm_id IF NOT EXISTS FOR (r:Release) ON (r.gm_id)",
    ),
    # sha256 indexes retained for efficient MERGE operations during ingestion.
    (
        "artist_sha256",
        "CREATE INDEX artist_sha256 IF NOT EXISTS FOR (a:Artist) ON (a.sha256)",
    ),
    (
        "label_sha256",
        "CREATE INDEX label_sha256 IF NOT EXISTS FOR (l:Label) ON (l.sha256)",
    ),
    (
        "master_sha256",
        "CREATE INDEX master_sha256 IF NOT EXISTS FOR (m:Master) ON (m.sha256)",
    ),
    (
        "release_sha256",
        "CREATE INDEX release_sha256 IF NOT EXISTS FOR (r:Release) ON (r.sha256)",
    ),
    # Name range indexes used by explore for artist/label lookups by name.
    (
        "artist_name",
        "CREATE INDEX artist_name IF NOT EXISTS FOR (a:Artist) ON (a.name)",
    ),
    (
        "label_name",
        "CREATE INDEX label_name IF NOT EXISTS FOR (l:Label) ON (l.name)",
    ),
    # Year range indexes used by explore for temporal queries and insights
    # for anniversary lookups.
    (
        "release_year_index",
        "CREATE INDEX release_year_index IF NOT EXISTS FOR (r:Release) ON (r.year)",
    ),
    (
        "master_year_index",
        "CREATE INDEX master_year_index IF NOT EXISTS FOR (m:Master) ON (m.year)",
    ),
    # media_families is a list property (see ADR 0007) used for cheap filtering
    # of releases by media family without traversing ISSUED_ON edges.
    (
        "release_media_families_index",
        "CREATE INDEX release_media_families_index IF NOT EXISTS FOR (r:Release) ON (r.media_families)",
    ),
    # Release.country (ADR 0011): additive property written by the Discogs graph
    # enricher from the release's country, and by the MusicBrainz graph enricher
    # as mb_country on releases it matches. PostgreSQL already indexes
    # releases.data->>'country'; this closes the same gap in the graph.
    (
        "release_country",
        "CREATE INDEX release_country IF NOT EXISTS FOR (r:Release) ON (r.country)",
    ),
    # first_year indexes for genre-emergence queries.  Pre-computed by
    # graphinator after release import; allows emergence lookups with
    # ~100 DB hits instead of scanning all IS edges (~184M DB hits).
    (
        "genre_first_year_index",
        "CREATE INDEX genre_first_year_index IF NOT EXISTS FOR (g:Genre) ON (g.first_year)",
    ),
    (
        "style_first_year_index",
        "CREATE INDEX style_first_year_index IF NOT EXISTS FOR (s:Style) ON (s.first_year)",
    ),
    # Person credit_count index for fast "top credited people" leaderboard queries.
    (
        "person_credit_count",
        "CREATE INDEX person_credit_count IF NOT EXISTS FOR (p:Person) ON (p.credit_count)",
    ),
    # ── Fulltext indexes ──────────────────────────────────────────────────────
    # Used by explore for autocomplete and full-text search.
    (
        "artist_name_fulltext",
        "CREATE FULLTEXT INDEX artist_name_fulltext IF NOT EXISTS FOR (n:Artist) ON EACH [n.name]",
    ),
    (
        "release_title_fulltext",
        "CREATE FULLTEXT INDEX release_title_fulltext IF NOT EXISTS FOR (n:Release) ON EACH [n.title]",
    ),
    (
        "label_name_fulltext",
        "CREATE FULLTEXT INDEX label_name_fulltext IF NOT EXISTS FOR (n:Label) ON EACH [n.name]",
    ),
    (
        "genre_name_fulltext",
        "CREATE FULLTEXT INDEX genre_name_fulltext IF NOT EXISTS FOR (n:Genre) ON EACH [n.name]",
    ),
    (
        "style_name_fulltext",
        "CREATE FULLTEXT INDEX style_name_fulltext IF NOT EXISTS FOR (n:Style) ON EACH [n.name]",
    ),
    (
        "person_name_fulltext",
        "CREATE FULLTEXT INDEX person_name_fulltext IF NOT EXISTS FOR (n:Person) ON EACH [n.name]",
    ),
    # ── MusicBrainz MBID indexes ─────────────────────────────────────────────
    # Used by brainzgraphinator for efficient lookups when enriching nodes
    # with MusicBrainz metadata.
    (
        "artist_mbid",
        "CREATE INDEX artist_mbid IF NOT EXISTS FOR (a:Artist) ON (a.mbid)",
    ),
    (
        "label_mbid",
        "CREATE INDEX label_mbid IF NOT EXISTS FOR (l:Label) ON (l.mbid)",
    ),
    (
        "release_mbid",
        "CREATE INDEX release_mbid IF NOT EXISTS FOR (r:Release) ON (r.mbid)",
    ),
    (
        "master_mbid",
        "CREATE INDEX master_mbid IF NOT EXISTS FOR (m:Master) ON (m.mbid)",
    ),
]


async def _execute_schema_statements(session: Any, statements: Iterable[tuple[str, str]]) -> tuple[int, int]:
    """Execute every statement, returning success and failure counts."""
    success_count = 0
    failure_count = 0
    for name, cypher in statements:
        try:
            result = await session.run(cypher)
            await result.consume()
            logger.info("✅ Schema: %s", name)
            success_count += 1
        except Exception as error:
            logger.error("❌ Failed to create schema object '%s': %s", name, error)
            failure_count += 1
    return success_count, failure_count


async def create_neo4j_schema(driver: Any) -> int:
    """Create all Neo4j constraints and indexes.

    Safe to call on every startup. Every statement uses IF NOT EXISTS so
    subsequent calls are no-ops for already-created schema objects.

    Args:
        driver: An AsyncResilientNeo4jDriver instance (from common.neo4j_resilient).

    Returns:
        Number of failed schema statements (0 means all succeeded).
    """
    logger.info("🔧 Creating Neo4j schema (constraints and indexes)...")

    async with driver.session(database="neo4j") as session:
        success_count, failure_count = await _execute_schema_statements(session, SCHEMA_STATEMENTS)

    total = len(SCHEMA_STATEMENTS)
    logger.info(f"✅ Neo4j schema creation complete: {success_count} succeeded, {failure_count} failed (total: {total})")
    return failure_count
