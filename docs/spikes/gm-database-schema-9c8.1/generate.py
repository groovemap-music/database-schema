#!/usr/bin/env python3
"""Deterministic catalog generator for the gm-database-schema-9c8.1 spike.

Throwaway measurement scaffolding. Nothing here ships, nothing here is imported
by `groovemap_schema`, and no product code reads it. It exists so the numbers in
`../gm-database-schema-9c8.1-graph-table-performance.md` can be reproduced, and
so the sibling spike gm-database-schema-9c8.3 can rebuild the same graph for its
shortest-path prototypes without re-deriving the distribution choices.

Two emit targets read the SAME record stream in the SAME order, so the Discogs
documents loaded into PostgreSQL and the nodes and relationships imported into
Neo4j describe one catalog rather than two catalogs that merely share a size:

    --target postgres   CSV ready for `\\copy ... WITH (FORMAT csv)`
    --target neo4j      node and relationship CSV for `neo4j-admin database import`

Determinism does not come from `random`. Every attribute is derived from a
SplitMix64 stream keyed on (seed, salt, entity id), so an entity's attributes are
a pure function of its id: the generator holds no entity table in memory, the two
targets cannot drift even if one is run months after the other, and a single
table can be regenerated on its own without replaying the ones before it. At the
synthetic scale that matters — a million release documents are streamed straight
to stdout and never collected.

Invocation is documented in ./README.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, TextIO


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence


# ---------------------------------------------------------------------------
# Deterministic stream
# ---------------------------------------------------------------------------

_MASK: Final = (1 << 64) - 1
_GOLDEN: Final = 0x9E3779B97F4A7C15


def _mix(value: int) -> int:
    """Return the SplitMix64 finalizer of VALUE.

    Chosen over `random.Random` because it is seekable: keying on (seed, salt,
    id) makes every attribute addressable without replaying the sequence that
    would precede it, which is what lets `--table releases` run without first
    generating a hundred thousand artists.
    """
    value = (value ^ (value >> 30)) * 0xBF58476D1CE4E5B9 & _MASK
    value = (value ^ (value >> 27)) * 0x94D049BB133111EB & _MASK
    return value ^ (value >> 31)


class Stream:
    """A deterministic 64-bit draw sequence keyed on a seed, a salt, and an id."""

    __slots__ = ("_state",)

    def __init__(self, seed: int, salt: int, index: int) -> None:
        self._state = _mix(seed & _MASK) ^ _mix((salt * _GOLDEN) & _MASK) ^ _mix(index & _MASK)

    def draw(self) -> int:
        """Return the next 64-bit value and advance the stream."""
        self._state = (self._state + _GOLDEN) & _MASK
        return _mix(self._state)

    def below(self, bound: int) -> int:
        """Return a value in [0, BOUND)."""
        return self.draw() % bound

    def fraction(self) -> float:
        """Return a value in [0, 1)."""
        return (self.draw() >> 11) / float(1 << 53)

    def pick(self, choices: Sequence[str]) -> str:
        """Return one element of CHOICES."""
        return choices[self.below(len(choices))]

    def weighted(self, weights: Sequence[float]) -> int:
        """Return an index into WEIGHTS, drawn in proportion to them."""
        target = self.fraction() * sum(weights)
        cumulative = 0.0
        for index, weight in enumerate(weights):
            cumulative += weight
            if target < cumulative:
                return index
        return len(weights) - 1

    def popular(self, count: int, exponent: float) -> int:
        """Return an id in [1, COUNT] skewed toward low ids.

        Discogs artist and label degree is heavy-tailed: a few thousand artists
        carry a large share of all releases. Uniform assignment would make the
        two-hop collaborator expansion uniformly cheap and hide exactly the cost
        this spike is measuring, so low ids are made deliberately popular.
        """
        return 1 + int(count * (self.fraction() ** exponent))

    def sample(self, count: int, size: int, exponent: float) -> list[int]:
        """Return SIZE distinct ids in [1, COUNT], skewed toward low ids."""
        drawn: list[int] = []
        for _ in range(size * 3):
            if len(drawn) == size:
                break
            candidate = self.popular(count, exponent)
            if candidate not in drawn:
                drawn.append(candidate)
        return drawn

    def token(self) -> str:
        """Return a 64-hex stand-in for the loader's SHA-256 change-detection hash."""
        return f"{self.draw():016x}{self.draw():016x}{self.draw():016x}{self.draw():016x}"

    def uuid(self) -> str:
        """Return a deterministic RFC-4122-shaped UUID string."""
        high = self.draw()
        low = self.draw()
        raw = f"{high:016x}{low:016x}"
        return f"{raw[0:8]}-{raw[8:12]}-4{raw[13:16]}-a{raw[17:20]}-{raw[20:32]}"


# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

# Fifteen Discogs top-level genres, verbatim. A short genre vocabulary is the
# point rather than a simplification: `graph.genre` is a SELECT DISTINCT over
# every genre element of every release and master, so a small distinct set over
# a huge unnest is precisely the shape that makes that view expensive.
GENRES: Final = (
    "Blues",
    "Brass & Military",
    "Children's",
    "Classical",
    "Electronic",
    "Folk, World, & Country",
    "Funk / Soul",
    "Hip Hop",
    "Jazz",
    "Latin",
    "Non-Music",
    "Pop",
    "Reggae",
    "Rock",
    "Stage & Screen",
)

STYLE_STEMS: Final = (
    "Abstract",
    "Acid",
    "Acoustic",
    "Alternative",
    "Ambient",
    "Avantgarde",
    "Ballad",
    "Baroque",
    "Bop",
    "Breakbeat",
    "Britpop",
    "Chanson",
    "Chiptune",
    "Contemporary",
    "Cosmic",
    "Dancehall",
    "Darkwave",
    "Deep",
    "Disco",
    "Downtempo",
    "Dub",
    "Electro",
    "Experimental",
    "Field Recording",
    "Free Improvisation",
    "Fusion",
    "Garage",
    "Gospel",
    "Grime",
    "Hard",
    "Honky Tonk",
    "House",
    "Indie",
    "Industrial",
    "Krautrock",
    "Lo-Fi",
    "Minimal",
    "Modal",
    "Neofolk",
    "New Wave",
    "Noise",
    "Post",
    "Progressive",
    "Psychedelic",
    "Punk",
    "Rocksteady",
    "Shoegaze",
    "Ska",
    "Soul-Jazz",
    "Spoken Word",
    "Swing",
    "Synth-pop",
    "Techno",
    "Trance",
    "Tribal",
)

STYLE_TAILS: Final = ("Rock", "Jazz", "House", "Techno", "Pop", "Folk", "Soul", "Funk")

NAME_FIRST: Final = (
    "Amber",
    "Basalt",
    "Cinder",
    "Delta",
    "Ember",
    "Fathom",
    "Glass",
    "Harbour",
    "Iron",
    "Juniper",
    "Kestrel",
    "Lantern",
    "Marble",
    "Nectar",
    "Onyx",
    "Pallas",
    "Quarry",
    "Rivet",
    "Saffron",
    "Tundra",
    "Umber",
    "Vellum",
    "Willow",
    "Xenon",
    "Yarrow",
    "Zephyr",
)

NAME_SECOND: Final = (
    "Assembly",
    "Bureau",
    "Circuit",
    "Division",
    "Ensemble",
    "Foundry",
    "Gallery",
    "Highway",
    "Institute",
    "Junction",
    "Kiosk",
    "Laboratory",
    "Machine",
    "Network",
    "Orchestra",
    "Parade",
    "Quartet",
    "Registry",
    "Society",
    "Transmission",
    "Union",
    "Vanguard",
    "Workshop",
)

TITLE_HEAD: Final = (
    "Aftermath",
    "Blueprint",
    "Cartography",
    "Dispatches",
    "Echoes",
    "Fieldwork",
    "Groundwork",
    "Horizons",
    "Interlude",
    "Jetstream",
    "Kinetics",
    "Longitude",
    "Meridian",
    "Nightshift",
    "Overtones",
    "Passages",
    "Quicksilver",
    "Resonance",
    "Sediment",
    "Threshold",
)

TITLE_TAIL: Final = (
    "Vol. 1",
    "Vol. 2",
    "Remixes",
    "Reissue",
    "Sessions",
    "Live",
    "EP",
    "LP",
    "Anthology",
    "Demos",
)

COUNTRIES: Final = ("US", "UK", "Germany", "Japan", "France", "Netherlands", "Italy", "Canada", "Sweden", "Brazil")

CREDIT_ROLES: Final = (
    "Producer",
    "Engineer",
    "Mixed By",
    "Mastered By",
    "Written-By",
    "Photography By",
    "Design",
    "Bass",
    "Drums",
    "Guitar",
)

MEDIA: Final = (("vinyl-lp-12", "vinyl"), ("cd", "cd"), ("cassette", "cassette"), ("file-flac", "digital"), ("vinyl-7", "vinyl"))

MB_ARTIST_TYPES: Final = ("Person", "Group", "Orchestra", "Choir")

# The Cypher this spike ports matches `(:Artist)-[r]->(:Artist) WHERE r.source =
# 'musicbrainz'` — any type, filtered on the edge property. The `discogs` rows
# exist so that filter has something to exclude and is not free.
MB_RELATIONSHIP_TYPES: Final = (
    "MEMBER_OF",
    "COLLABORATED_WITH",
    "SUPPORTING_MUSICIAN",
    "FOUNDER_OF",
    "SIBLING_OF",
    "TRIBUTE_TO",
    "VOICE_ACTOR_FOR",
)

DISCOGS_RELATIONSHIP_TYPES: Final = ("ALIAS_OF", "MEMBER_OF")

MB_ATTRIBUTES: Final = ("original", "guest", "additional", "co", "solo")


# Salts keep the per-entity streams independent: artist 7 and label 7 share an
# id but not a draw sequence.
_SALT_ARTIST: Final = 1
_SALT_LABEL: Final = 2
_SALT_MASTER: Final = 3
_SALT_RELEASE: Final = 4
_SALT_MB_ARTIST: Final = 5
_SALT_MB_RELATIONSHIP: Final = 6

# Popularity exponents. Above 1.0 the distribution leans on low ids; artists lean
# harder than labels because a prolific artist is the case that makes a two-hop
# expansion expensive.
_ARTIST_EXPONENT: Final = 2.4
_LABEL_EXPONENT: Final = 1.8
_MASTER_EXPONENT: Final = 1.3

# Cardinality weights, indexed from one. The means they imply are quoted in
# ./README.md and are what puts the synthetic scale above five million edges.
_ARTISTS_PER_RELEASE: Final = (0.40, 0.35, 0.18, 0.07)
_LABELS_PER_RELEASE: Final = (0.72, 0.23, 0.05)
_GENRES_PER_RELEASE: Final = (0.45, 0.40, 0.15)
_STYLES_PER_RELEASE: Final = (0.25, 0.35, 0.28, 0.12)
_ARTISTS_PER_MASTER: Final = (0.55, 0.30, 0.15)
_CREDITS_PER_RELEASE: Final = (0.55, 0.30, 0.15)

_MASTER_PRESENT: Final = 0.70


@dataclass(frozen=True)
class Scale:
    """One named catalog size."""

    name: str
    releases: int
    artists: int
    labels: int
    masters: int
    mb_artists: int
    mb_relationships: int


# `fixture` is the small tier: large enough that a plan is a plan rather than a
# sequential scan of nothing, small enough to load and measure in seconds.
# `synthetic` satisfies the bead's floor of one million releases and five million
# edges; the measured edge counts it actually produces are recorded in the spike
# document rather than asserted here.
SCALES: Final[dict[str, Scale]] = {
    "fixture": Scale("fixture", releases=5_000, artists=1_500, labels=300, masters=1_800, mb_artists=800, mb_relationships=4_000),
    "synthetic": Scale("synthetic", releases=1_000_000, artists=120_000, labels=20_000, masters=250_000, mb_artists=60_000, mb_relationships=300_000),
}

DEFAULT_SEED: Final = 20260917


# ---------------------------------------------------------------------------
# Entity attributes — pure functions of (seed, id)
# ---------------------------------------------------------------------------


def artist_name(seed: int, artist_id: int) -> str:
    """Return the name of Discogs artist ARTIST_ID."""
    stream = Stream(seed, _SALT_ARTIST, artist_id)
    return f"{stream.pick(NAME_FIRST)} {stream.pick(NAME_SECOND)}"


def label_name(seed: int, label_id: int) -> str:
    """Return the name of Discogs label LABEL_ID."""
    stream = Stream(seed, _SALT_LABEL, label_id)
    return f"{stream.pick(NAME_FIRST)} {stream.pick(NAME_SECOND)} Records"


def _tags(stream: Stream, weights: Sequence[float]) -> tuple[list[str], list[str]]:
    """Return the genre and style lists for one document."""
    genres = [GENRES[stream.below(len(GENRES))] for _ in range(1 + stream.weighted(weights))]
    styles = [f"{stream.pick(STYLE_STEMS)} {stream.pick(STYLE_TAILS)}" for _ in range(1 + stream.weighted(_STYLES_PER_RELEASE))]
    return sorted(set(genres)), sorted(set(styles))


def mb_artist_mbid(seed: int, mb_index: int) -> str:
    """Return the MBID of the MB artist at MB_INDEX."""
    return Stream(seed, _SALT_MB_ARTIST, mb_index).uuid()


def mb_artist_discogs_id(seed: int, mb_index: int, artists: int) -> int:
    """Return the Discogs artist id the MB artist at MB_INDEX is linked to.

    The MB catalog covers the popular end of the Discogs catalog, which is where
    an enrichment backfill actually reaches, so the link target is drawn from the
    same skewed distribution the release documents use.
    """
    stream = Stream(seed, _SALT_MB_ARTIST, mb_index)
    stream.draw()
    stream.draw()
    return stream.popular(artists, _ARTIST_EXPONENT)


# ---------------------------------------------------------------------------
# Record streams
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Release:
    """One generated Discogs release, in the shape the graph views read."""

    release_id: int
    title: str
    year: int
    country: str
    artist_ids: list[int]
    label_ids: list[int]
    genres: list[str]
    styles: list[str]
    master_id: int | None
    credits: list[tuple[str, str]]
    medium_id: str
    family: str


def iter_releases(seed: int, scale: Scale) -> Iterator[Release]:
    """Yield every release of SCALE, in id order, holding one at a time."""
    for release_id in range(1, scale.releases + 1):
        stream = Stream(seed, _SALT_RELEASE, release_id)
        artist_ids = stream.sample(scale.artists, 1 + stream.weighted(_ARTISTS_PER_RELEASE), _ARTIST_EXPONENT)
        label_ids = stream.sample(scale.labels, 1 + stream.weighted(_LABELS_PER_RELEASE), _LABEL_EXPONENT)
        genres, styles = _tags(stream, _GENRES_PER_RELEASE)
        master_id = stream.popular(scale.masters, _MASTER_EXPONENT) if stream.fraction() < _MASTER_PRESENT else None
        credits = [
            (f"{stream.pick(NAME_FIRST)} {stream.pick(NAME_SECOND)}", stream.pick(CREDIT_ROLES))
            for _ in range(1 + stream.weighted(_CREDITS_PER_RELEASE))
        ]
        medium_id, family = MEDIA[stream.below(len(MEDIA))]
        yield Release(
            release_id=release_id,
            title=f"{stream.pick(TITLE_HEAD)} {stream.pick(TITLE_TAIL)}",
            year=1950 + stream.below(76),
            country=stream.pick(COUNTRIES),
            artist_ids=artist_ids,
            label_ids=label_ids,
            genres=genres,
            styles=styles,
            master_id=master_id,
            credits=credits,
            medium_id=medium_id,
            family=family,
        )


@dataclass(frozen=True)
class Master:
    """One generated Discogs master."""

    master_id: int
    title: str
    year: int
    artist_ids: list[int]
    genres: list[str]
    styles: list[str]


def iter_masters(seed: int, scale: Scale) -> Iterator[Master]:
    """Yield every master of SCALE, in id order."""
    for master_id in range(1, scale.masters + 1):
        stream = Stream(seed, _SALT_MASTER, master_id)
        artist_ids = stream.sample(scale.artists, 1 + stream.weighted(_ARTISTS_PER_MASTER), _ARTIST_EXPONENT)
        genres, styles = _tags(stream, _GENRES_PER_RELEASE)
        yield Master(
            master_id=master_id,
            title=f"{stream.pick(TITLE_HEAD)} {stream.pick(TITLE_TAIL)}",
            year=1950 + stream.below(76),
            artist_ids=artist_ids,
            genres=genres,
            styles=styles,
        )


@dataclass(frozen=True)
class MbRelationship:
    """One generated MusicBrainz relationship between two artists."""

    source_mbid: str
    target_mbid: str
    source_discogs_id: int
    target_discogs_id: int
    relationship_type: str
    begin_date: str
    end_date: str
    ended: bool
    attributes: str
    source: str


def iter_mb_relationships(seed: int, scale: Scale) -> Iterator[MbRelationship]:
    """Yield every MusicBrainz artist-to-artist relationship of SCALE.

    Self-loops are skipped: two MB artists can map to the same Discogs artist
    under the skewed link distribution, and an edge from an artist to itself is
    noise in a relationship listing rather than a case worth measuring.
    """
    for index in range(1, scale.mb_relationships + 1):
        stream = Stream(seed, _SALT_MB_RELATIONSHIP, index)
        source_index = stream.popular(scale.mb_artists, 1.6)
        target_index = stream.popular(scale.mb_artists, 1.6)
        if source_index == target_index:
            continue
        source_discogs = mb_artist_discogs_id(seed, source_index, scale.artists)
        target_discogs = mb_artist_discogs_id(seed, target_index, scale.artists)
        if source_discogs == target_discogs:
            continue
        is_musicbrainz = stream.fraction() < 0.85
        types = MB_RELATIONSHIP_TYPES if is_musicbrainz else DISCOGS_RELATIONSHIP_TYPES
        begin_year = 1950 + stream.below(70)
        ended = stream.fraction() < 0.35
        yield MbRelationship(
            source_mbid=mb_artist_mbid(seed, source_index),
            target_mbid=mb_artist_mbid(seed, target_index),
            source_discogs_id=source_discogs,
            target_discogs_id=target_discogs,
            relationship_type=stream.pick(types),
            begin_date=f"{begin_year}-01-01",
            end_date=f"{begin_year + 1 + stream.below(30)}-12-31" if ended else "",
            ended=ended,
            attributes=json.dumps([stream.pick(MB_ATTRIBUTES)]) if stream.fraction() < 0.4 else "[]",
            source="musicbrainz" if is_musicbrainz else "discogs",
        )


# ---------------------------------------------------------------------------
# PostgreSQL emit
# ---------------------------------------------------------------------------

_COMPACT: Final = (",", ":")


def _dumps(document: object) -> str:
    """Return DOCUMENT as compact JSON."""
    return json.dumps(document, separators=_COMPACT, ensure_ascii=False)


def _entity_refs(seed: int, ids: list[int], namer: Callable[[int, int], str]) -> list[dict[str, str]]:
    """Return the Discogs `[{id, name}]` reference block for IDS."""
    return [{"id": str(entity_id), "name": namer(seed, entity_id)} for entity_id in ids]


def write_postgres(target: str, seed: int, scale: Scale, handle: TextIO) -> int:
    """Write the CSV for one PostgreSQL table to HANDLE, returning the row count."""
    writer = csv.writer(handle, lineterminator="\n")
    rows = 0

    if target == "artists":
        for artist_id in range(1, scale.artists + 1):
            stream = Stream(seed, _SALT_ARTIST, artist_id)
            stream.draw()
            stream.draw()
            document = {"id": artist_id, "name": artist_name(seed, artist_id), "profile": "", "data_quality": "Correct"}
            writer.writerow([str(artist_id), stream.token(), _dumps(document)])
            rows += 1

    elif target == "labels":
        for label_id in range(1, scale.labels + 1):
            stream = Stream(seed, _SALT_LABEL, label_id)
            stream.draw()
            stream.draw()
            document = {"id": label_id, "name": label_name(seed, label_id), "profile": "", "data_quality": "Correct"}
            writer.writerow([str(label_id), stream.token(), _dumps(document)])
            rows += 1

    elif target == "masters":
        for master in iter_masters(seed, scale):
            document = {
                "id": master.master_id,
                "title": master.title,
                "year": str(master.year),
                "artists": _entity_refs(seed, master.artist_ids, artist_name),
                "genres": master.genres,
                "styles": master.styles,
            }
            writer.writerow([str(master.master_id), Stream(seed, _SALT_MASTER, master.master_id).token(), _dumps(document)])
            rows += 1

    elif target == "releases":
        for release in iter_releases(seed, scale):
            document: dict[str, object] = {
                "id": release.release_id,
                "title": release.title,
                "year": str(release.year),
                "country": release.country,
                "artists": _entity_refs(seed, release.artist_ids, artist_name),
                "labels": _entity_refs(seed, release.label_ids, label_name),
                "genres": release.genres,
                "styles": release.styles,
                "extraartists": [{"id": "0", "name": name, "role": role} for name, role in release.credits],
                "formats": [{"name": release.family, "qty": "1"}],
            }
            if release.master_id is not None:
                document["master_id"] = str(release.master_id)
            media = {"families": [release.family], "items": [{"medium": release.medium_id, "family": release.family, "qty": 1}]}
            writer.writerow([str(release.release_id), Stream(seed, _SALT_RELEASE, release.release_id).token(), _dumps(document), _dumps(media)])
            rows += 1

    elif target == "mb_artists":
        for mb_index in range(1, scale.mb_artists + 1):
            stream = Stream(seed, _SALT_MB_ARTIST, mb_index)
            mbid = mb_artist_mbid(seed, mb_index)
            discogs_id = mb_artist_discogs_id(seed, mb_index, scale.artists)
            stream.draw()
            stream.draw()
            stream.draw()
            name = artist_name(seed, discogs_id)
            writer.writerow(
                [
                    mbid,
                    name,
                    name,
                    stream.pick(MB_ARTIST_TYPES),
                    "",
                    f"{1950 + stream.below(70)}-01-01",
                    "",
                    "false",
                    stream.pick(COUNTRIES),
                    "",
                    "",
                    "",
                    str(discogs_id),
                ]
            )
            rows += 1

    elif target == "mb_relationships":
        # `musicbrainz.relationships` is MusicBrainz-only by construction — the
        # schema gives it no `source` column because every row in it has one
        # provenance. The Discogs-sourced artist-to-artist edges the stream also
        # yields belong to `graph.alias_of` and `graph.member_of`, derived from
        # the Discogs documents, so they are dropped here and kept only for the
        # Neo4j target, where both provenances share one relationship space and
        # the `r.source` filter in the ported Cypher has to separate them.
        for relationship in iter_mb_relationships(seed, scale):
            if relationship.source != "musicbrainz":
                continue
            writer.writerow(
                [
                    relationship.source_mbid,
                    relationship.target_mbid,
                    "artist",
                    "artist",
                    relationship.relationship_type,
                    relationship.begin_date,
                    relationship.end_date,
                    "true" if relationship.ended else "false",
                    relationship.attributes,
                ]
            )
            rows += 1

    else:
        message = f"unknown postgres table: {target}"
        raise SystemExit(message)

    return rows


# ---------------------------------------------------------------------------
# Neo4j emit
# ---------------------------------------------------------------------------

# Genre and style node ids are the tag names themselves, exactly as the graph
# views key `graph.genre` and `graph.style` on `name`. Collecting the distinct
# tag set is the one place the generator holds a set in memory; it is bounded by
# the vocabulary (fifteen genres, a few hundred styles), not by the catalog.


class _CsvWriter:
    """A row writer over one open file."""

    __slots__ = ("_writer",)

    def __init__(self, handle: TextIO) -> None:
        self._writer = csv.writer(handle, lineterminator="\n")

    def writerow(self, row: list[str]) -> None:
        """Write one CSV row."""
        self._writer.writerow(row)


class _CsvFiles:
    """A set of CSV writers that close together when the block exits.

    `neo4j-admin database import` wants one file per node label and per
    relationship kind, and the catalog is walked once, so every file has to stay
    open across the whole walk. Closing them by hand in the right order is the
    kind of bookkeeping that quietly leaves a buffer unflushed and truncates the
    last few thousand rows of an import.
    """

    def __init__(self, outdir: Path) -> None:
        self._outdir = outdir
        self._handles: list[TextIO] = []

    def __enter__(self) -> _CsvFiles:
        return self

    def __exit__(self, *_exception: object) -> None:
        for handle in reversed(self._handles):
            handle.close()

    def open(self, name: str, header: list[str]) -> _CsvWriter:
        """Return a writer over OUTDIR/NAME, with HEADER already written."""
        handle = (self._outdir / name).open("w", encoding="utf-8", newline="")
        self._handles.append(handle)
        writer = _CsvWriter(handle)
        writer.writerow(header)
        return writer


def write_neo4j(seed: int, scale: Scale, outdir: Path) -> dict[str, int]:
    """Write every node and relationship CSV for SCALE into OUTDIR.

    Every file is opened up front and the catalog is walked exactly once, so the
    release stream is consumed in the same order as the PostgreSQL target walks
    it and the two backends receive the identical catalog.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    genres: set[str] = set()
    styles: set[str] = set()

    with _CsvFiles(outdir) as files:
        artists = files.open("artists.csv", ["id:ID(Artist)", "name"])
        for artist_id in range(1, scale.artists + 1):
            artists.writerow([str(artist_id), artist_name(seed, artist_id)])
        counts["Artist"] = scale.artists

        labels = files.open("labels.csv", ["id:ID(Label)", "name"])
        for label_id in range(1, scale.labels + 1):
            labels.writerow([str(label_id), label_name(seed, label_id)])
        counts["Label"] = scale.labels

        masters = files.open("masters.csv", ["id:ID(Master)", "title", "year:int"])
        master_by_artist = files.open("master_by_artist.csv", [":START_ID(Master)", ":END_ID(Artist)"])
        master_in_genre = files.open("master_in_genre.csv", [":START_ID(Master)", ":END_ID(Genre)"])
        master_in_style = files.open("master_in_style.csv", [":START_ID(Master)", ":END_ID(Style)"])
        master_edges = 0
        for master in iter_masters(seed, scale):
            masters.writerow([str(master.master_id), master.title, str(master.year)])
            for artist_id in master.artist_ids:
                master_by_artist.writerow([str(master.master_id), str(artist_id)])
                master_edges += 1
            for genre in master.genres:
                genres.add(genre)
                master_in_genre.writerow([str(master.master_id), genre])
                master_edges += 1
            for style in master.styles:
                styles.add(style)
                master_in_style.writerow([str(master.master_id), style])
                master_edges += 1
        counts["Master"] = scale.masters
        counts["master-edges"] = master_edges

        releases = files.open("releases.csv", ["id:ID(Release)", "title", "year:int", "country"])
        by_artist = files.open("by_artist.csv", [":START_ID(Release)", ":END_ID(Artist)"])
        on_label = files.open("on_label.csv", [":START_ID(Release)", ":END_ID(Label)"])
        in_genre = files.open("in_genre.csv", [":START_ID(Release)", ":END_ID(Genre)"])
        in_style = files.open("in_style.csv", [":START_ID(Release)", ":END_ID(Style)"])
        derived_from = files.open("derived_from.csv", [":START_ID(Release)", ":END_ID(Master)"])
        release_edges = 0
        for release in iter_releases(seed, scale):
            releases.writerow([str(release.release_id), release.title, str(release.year), release.country])
            for artist_id in release.artist_ids:
                by_artist.writerow([str(release.release_id), str(artist_id)])
                release_edges += 1
            for label_id in release.label_ids:
                on_label.writerow([str(release.release_id), str(label_id)])
                release_edges += 1
            for genre in release.genres:
                genres.add(genre)
                in_genre.writerow([str(release.release_id), genre])
                release_edges += 1
            for style in release.styles:
                styles.add(style)
                in_style.writerow([str(release.release_id), style])
                release_edges += 1
            if release.master_id is not None:
                derived_from.writerow([str(release.release_id), str(release.master_id)])
                release_edges += 1
        counts["Release"] = scale.releases
        counts["release-edges"] = release_edges

        genre_nodes = files.open("genres.csv", ["name:ID(Genre)"])
        for genre in sorted(genres):
            genre_nodes.writerow([genre])
        counts["Genre"] = len(genres)

        style_nodes = files.open("styles.csv", ["name:ID(Style)"])
        for style in sorted(styles):
            style_nodes.writerow([style])
        counts["Style"] = len(styles)

        # The MusicBrainz listing is a relationship between Discogs `:Artist`
        # nodes carrying `source`, which is what the catalog-api Cypher matches.
        # Neo4j keeps no separate MB artist node for it, so the MB artist table
        # has no counterpart here — only the edges do.
        mb = files.open(
            "mb_relationships.csv",
            [":START_ID(Artist)", ":END_ID(Artist)", ":TYPE", "source", "begin_date", "end_date", "attributes"],
        )
        mb_edges = 0
        for relationship in iter_mb_relationships(seed, scale):
            mb.writerow(
                [
                    str(relationship.source_discogs_id),
                    str(relationship.target_discogs_id),
                    relationship.relationship_type,
                    relationship.source,
                    relationship.begin_date,
                    relationship.end_date,
                    relationship.attributes,
                ]
            )
            mb_edges += 1
        counts["mb-edges"] = mb_edges

    return counts


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

POSTGRES_TABLES: Final = ("artists", "labels", "masters", "releases", "mb_artists", "mb_relationships")


def main(argv: list[str] | None = None) -> int:
    """Run the generator."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", choices=("postgres", "neo4j"), required=True)
    parser.add_argument("--scale", choices=tuple(SCALES), required=True)
    parser.add_argument("--table", choices=POSTGRES_TABLES, help="postgres target only; the single table to stream to stdout")
    parser.add_argument("--outdir", type=Path, help="neo4j target only; the directory to write import CSV into")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    arguments = parser.parse_args(argv)

    scale = SCALES[arguments.scale]

    if arguments.target == "postgres":
        if arguments.table is None:
            parser.error("--target postgres requires --table")
        rows = write_postgres(arguments.table, arguments.seed, scale, sys.stdout)
        sys.stderr.write(f"{arguments.table}: {rows} rows at scale {scale.name} seed {arguments.seed}\n")
        return 0

    if arguments.outdir is None:
        parser.error("--target neo4j requires --outdir")
    counts = write_neo4j(arguments.seed, scale, arguments.outdir)
    for name, count in counts.items():
        sys.stderr.write(f"{name}: {count}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
