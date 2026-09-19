"""What is measured, declared as data.

Throwaway spike harness for gm-database-schema-gkt.1.

The endpoint cases are gm-database-schema-9c8.3's, unchanged and hardcoded
rather than chosen by degree at run time, so the two spikes' tables can be read
against each other. That spike's README explains the rule that produced them:
every id comes from one exhaustive breadth-first search out of artist 5665, and
artist 55563 is the lowest-numbered degree-1 artist on the far rim of that
search, because from 5665 the whole component falls inside four levels and a
case at distance 5 or 6 has to start from the rim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final


#: The scales gm-database-schema-9c8.1's generator defines, under the names the
#: db-alternatives harness uses for the same idea.
SCALES: Final[dict[str, str]] = {"small": "fixture", "large": "synthetic"}

#: `find_shortest_path` clamps to this range and defaults to 6
#: (api/queries/neo4j_queries.py:92). `get_explore_traversal` clamps hops to
#: [1, 3] and defaults to 2 (api/queries/recommend_queries.py:410).
MIN_PATH_DEPTH: Final = 1
MAX_PATH_DEPTH: Final = 10
DEFAULT_PATH_DEPTH: Final = 6
EXPLORE_MIN_HOPS: Final = 1
EXPLORE_MAX_HOPS: Final = 3
EXPLORE_ROW_LIMIT: Final = 100

#: The budgets the spike measures against, in seconds. `find_shortest_path`
#: passes `timeout=120` to the Neo4j transaction; the MCP server builds its HTTP
#: client with `timeout=30.0` and that one budget covers both explore lookups and
#: the path call.
TRANSACTION_TIMEOUT_S: Final = 120
MCP_BUDGET_S: Final = 30

#: The Verdict gate from the bead: every shortest-path case including the miss at
#: or under this p95, and explore `*1..3` likewise, at the large scale.
VERDICT_P95_MS: Final = 1000.0


@dataclass(frozen=True)
class Endpoints:
    """One shortest-path case.

    ``kind`` is the one-character vertex discriminator the PostgreSQL side uses
    (``a`` artist, ``l`` label, ``r`` release, ``m`` master, ``g`` genre,
    ``s`` style); ``label`` is the Neo4j node label for the same vertex. They are
    carried together so an adapter never has to map one to the other.
    """

    name: str
    from_kind: str
    from_key: str
    from_label: str
    to_kind: str
    to_key: str
    to_label: str
    distance: int | None


#: Distances 1 through 6 plus the unreachable pair, artist 5665 as one endpoint
#: for all but the two rim cases. These are gm-database-schema-9c8.3's ids,
#: unchanged.
CASES_LARGE: Final[tuple[Endpoints, ...]] = (
    Endpoints("d1-artist-artist", "a", "5665", "Artist", "a", "9458", "Artist", 1),
    Endpoints("d1-artist-release", "a", "5665", "Artist", "r", "3638", "Release", 1),
    Endpoints("d2-artist-artist", "a", "5665", "Artist", "a", "1", "Artist", 2),
    Endpoints("d3-artist-artist", "a", "5665", "Artist", "a", "2", "Artist", 3),
    Endpoints("d4-artist-artist", "a", "5665", "Artist", "a", "9", "Artist", 4),
    Endpoints("d5-artist-artist", "a", "55563", "Artist", "a", "4814", "Artist", 5),
    Endpoints("d6-artist-artist", "a", "55563", "Artist", "a", "32509", "Artist", 6),
    Endpoints("unreachable", "a", "5665", "Artist", "a", "103111", "Artist", None),
)

#: The fixture scale needs its own ids and this is a finding rather than a
#: convenience. The ids above are synthetic-scale ids — artist 5665 of 120,000 —
#: and the fixture catalog has 1,500 artists, so at that scale every one of the
#: cases above is a lookup of a vertex that does not exist. A first run of this
#: harness measured exactly that: eight misses, all answered in under five
#: milliseconds, which would have gone into the document as "the small scale is
#: fast".
#:
#: These are derived by the SAME rule, stated and executed in
#: `sql/pick-endpoints.sql` and then written down here the way 9c8.3 wrote its
#: own down, so a rerun cannot silently measure a different vertex:
#:
#:   root      artist 1, the highest-degree artist (643), ties by lowest id
#:   dN        the lowest-numbered artist at distance N from the root
#:   rim root  artist 407, the lowest-numbered degree-1 artist at distance 4,
#:             which is the root's eccentricity — the fixture graph has nothing
#:             at distance 5 or 6 from the hub, exactly as the synthetic one has
#:             nothing past distance 4 from artist 5665
#:   miss      artist 950, the lowest-numbered artist carrying no edge at all.
#:             Fourteen of the 1,500 are isolated; every one of the other 1,486
#:             is reachable from the root, so there is no two-component pair to
#:             use and an isolated vertex is the only honest miss available.
CASES_SMALL: Final[tuple[Endpoints, ...]] = (
    Endpoints("d1-artist-artist", "a", "1", "Artist", "a", "2", "Artist", 1),
    Endpoints("d1-artist-release", "a", "1", "Artist", "r", "30", "Release", 1),
    Endpoints("d2-artist-artist", "a", "1", "Artist", "a", "3", "Artist", 2),
    Endpoints("d3-artist-artist", "a", "1", "Artist", "a", "33", "Artist", 3),
    Endpoints("d4-artist-artist", "a", "1", "Artist", "a", "82", "Artist", 4),
    Endpoints("d5-artist-artist", "a", "407", "Artist", "a", "61", "Artist", 5),
    Endpoints("d6-artist-artist", "a", "407", "Artist", "a", "389", "Artist", 6),
    Endpoints("unreachable", "a", "1", "Artist", "a", "950", "Artist", None),
)

CASES_BY_SCALE: Final[dict[str, tuple[Endpoints, ...]]] = {"small": CASES_SMALL, "large": CASES_LARGE}

#: Case names are identical across the two scales, so the report's rows line up
#: and a reader compares `d4-artist-artist` with `d4-artist-artist`. What differs
#: is which vertices those names point at, which is what the two tables above are.
CASES: Final[tuple[Endpoints, ...]] = CASES_LARGE

#: The explore start vertex. At the large scale it is gm-database-schema-9c8.1's
#: seed artist, which is 9c8.3's; at the fixture scale it is that catalog's
#: highest-degree artist, chosen by the same rule.
EXPLORE_START_BY_SCALE: Final[dict[str, tuple[str, str, str]]] = {
    "small": ("a", "1", "Artist"),
    "large": ("a", "5665", "Artist"),
}
EXPLORE_START: Final = EXPLORE_START_BY_SCALE["large"]


@dataclass(frozen=True)
class Workload:
    """One measured statement.

    ``iterations`` is at least 30 for every workload the Verdict rests on, which
    is what the bead asks for. It is lower only for the diagnostic variants that
    are reported for information and are known in advance to take seconds each:
    thirty iterations of a two-second search is a minute of wall clock spent
    sharpening a percentile that is already unambiguous, and the document says
    which rows those are.
    """

    name: str
    kind: str  # "path" | "explore"
    case: str
    variant: str  # which PostgreSQL function, or "cypher" on the Neo4j side
    depth: int
    iterations: int = 30
    warmup: int = 3
    #: Statement budget for ONE iteration. The default is the 120 s the product
    #: passes to the Neo4j transaction. It is lowered only for the diagnostic
    #: variants that are expected not to finish: the finding there is "did not
    #: finish", and waiting two minutes per iteration to write that down eight
    #: times is two hours spent confirming it.
    timeout_s: int = TRANSACTION_TIMEOUT_S
    scales: tuple[str, ...] = ("small", "large")
    verdict: bool = False  # does the Verdict gate read this row?
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


#: The PostgreSQL function behind each variant name. `pf.find_path` is the design
#: the bead asks about; `pf.find_path_deg` is the same search expanding cheap
#: vertices first, which needs `pf.degree`; `pf.find_path_no_is` is the same
#: search over an edge surface with the IS class removed, for information only;
#: `pf.find_path_hstore` keeps the seen set in a PL/pgSQL variable instead of a
#: relation.
PATH_VARIANTS: Final[dict[str, str]] = {
    "pf-vaat": "pf.find_path",
    "pf-vaat-deg": "pf.find_path_deg",
    "pf-vaat-no-is": "pf.find_path_no_is",
    "pf-vaat-hstore": "pf.find_path_hstore",
}
EXPLORE_VARIANTS: Final[dict[str, str]] = {
    "pf-explore": "pf.explore",
    "pf-explore-no-is": "pf.explore_no_is",
}


def _path_workloads() -> list[Workload]:
    out: list[Workload] = []
    for case in CASES:
        # The headline row: the design under test, at the product's own cap of 10,
        # which is also the MCP tool's default.
        out.append(
            Workload(
                name=f"path/{case.name}/pf-vaat",
                kind="path",
                case=case.name,
                variant="pf-vaat",
                depth=MAX_PATH_DEPTH,
                verdict=True,
                tags=("headline",),
            )
        )
        out.append(
            Workload(
                name=f"path/{case.name}/pf-vaat-deg",
                kind="path",
                case=case.name,
                variant="pf-vaat-deg",
                depth=MAX_PATH_DEPTH,
                verdict=True,
                tags=("headline", "degree"),
            )
        )
        out.append(
            Workload(
                name=f"path/{case.name}/neo4j",
                kind="path",
                case=case.name,
                variant="cypher",
                depth=MAX_PATH_DEPTH,
                verdict=True,
                tags=("headline", "baseline"),
            )
        )
        # For information: the same search over the surface without the IS class.
        out.append(
            Workload(
                name=f"path/{case.name}/pf-vaat-no-is",
                kind="path",
                case=case.name,
                variant="pf-vaat-no-is",
                depth=MAX_PATH_DEPTH,
                notes="for information only; a different graph, not a different algorithm",
                tags=("no-is",),
            )
        )
    # The depth cap only decides the work when there is nothing to find, so the
    # cap sweep runs on the unreachable pair alone.
    for cap in (1, 2, 3, 4, 6, 8, 10):
        for variant in ("pf-vaat", "pf-vaat-deg", "cypher"):
            out.append(
                Workload(
                    name=f"path/unreachable-cap{cap}/{variant}",
                    kind="path",
                    case="unreachable",
                    variant=variant,
                    depth=cap,
                    tags=("cap-sweep",),
                )
            )
    # What a depth cap actually buys. The sweep above runs on the unreachable
    # pair, where one endpoint is isolated and the cap never decides anything.
    # This one runs on a pair that IS connected, at distance 6, against caps
    # below that distance — which is the only situation in which lowering the
    # product's cap changes the cost of a request rather than only its answer.
    for cap in (2, 3, 4, 6, 10):
        for variant in ("pf-vaat", "pf-vaat-deg", "cypher"):
            out.append(
                Workload(
                    name=f"path/d6-cap{cap}/{variant}",
                    kind="path",
                    case="d6-artist-artist",
                    variant=variant,
                    depth=cap,
                    iterations=15,
                    tags=("cap-cost",),
                    notes="a connected pair against a cap below its distance",
                )
            )
    # The seen set held in a PL/pgSQL variable rather than a relation. Large scale
    # only, few iterations: it is a Recommendation input, not a Verdict input, and
    # it is expected to be unable to finish the hub cases at all.
    for case in CASES:
        out.append(
            Workload(
                name=f"path/{case.name}/pf-vaat-hstore",
                kind="path",
                case=case.name,
                variant="pf-vaat-hstore",
                depth=MAX_PATH_DEPTH,
                iterations=5,
                warmup=1,
                timeout_s=20,
                notes="seen set in a PL/pgSQL hstore; Recommendation input",
                tags=("hstore",),
            )
        )
    return out


def _explore_workloads() -> list[Workload]:
    out: list[Workload] = []
    for hops in (1, 2, 3):
        gate = hops == EXPLORE_MAX_HOPS
        out.append(
            Workload(
                name=f"explore/hops{hops}/pf-explore",
                kind="explore",
                case="explore-artist-5665",
                variant="pf-explore",
                depth=hops,
                verdict=gate,
                tags=("headline",),
            )
        )
        out.append(
            Workload(
                name=f"explore/hops{hops}/neo4j",
                kind="explore",
                case="explore-artist-5665",
                variant="cypher",
                depth=hops,
                iterations=30 if hops < EXPLORE_MAX_HOPS else 10,
                verdict=gate,
                notes="" if hops < EXPLORE_MAX_HOPS else "10 iterations; seconds each on the Neo4j side",
                tags=("headline", "baseline"),
            )
        )
        out.append(
            Workload(
                name=f"explore/hops{hops}/pf-explore-no-is",
                kind="explore",
                case="explore-artist-5665",
                variant="pf-explore-no-is",
                depth=hops,
                notes="for information only",
                tags=("no-is",),
            )
        )
    return out


WORKLOADS: Final[tuple[Workload, ...]] = tuple(_path_workloads() + _explore_workloads())


def cases_for(scale: str) -> dict[str, Endpoints]:
    """The endpoint table for one scale, keyed by case name."""
    return {c.name: c for c in CASES_BY_SCALE[scale]}


def explore_start_for(scale: str) -> tuple[str, str, str]:
    return EXPLORE_START_BY_SCALE[scale]


CASES_BY_NAME: Final[dict[str, Endpoints]] = {c.name: c for c in CASES}


def for_engine(engine: str, scale: str) -> list[Workload]:
    """The workloads one engine runs at one scale.

    A workload whose variant is ``cypher`` belongs to Neo4j and every other one
    belongs to PostgreSQL. Splitting here rather than inside each adapter keeps
    the two engines' iteration counts and depth caps in a single table that the
    report can quote.
    """
    want_cypher = engine == "neo4j"
    return [w for w in WORKLOADS if (w.variant == "cypher") == want_cypher and scale in w.scales]
