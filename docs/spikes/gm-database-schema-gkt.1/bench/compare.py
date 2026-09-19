"""Compare the two engines' results files, and check the answers agree.

Throwaway spike harness for gm-database-schema-gkt.1.

Two separate jobs, because they fail for different reasons and a reader needs to
be able to tell which one failed:

* the ANSWER check — did the PostgreSQL searches return what Neo4j returned? A
  failure here invalidates every timing beside it, and the Verdict is NO-GO
  regardless of latency.
* the LATENCY comparison — how the p50 and p95 of each case line up, and whether
  the Verdict gate is met.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .workloads import VERDICT_P95_MS


#: Variants that deliberately search a DIFFERENT graph and must not be compared
#: against Neo4j. `pf-vaat-no-is` and `pf-explore-no-is` run over an edge surface
#: with the IS class removed, which is the whole point of them; they answer a
#: different question and a shorter or longer distance from them is a property of
#: that graph, not a defect. The for-information section reports them beside the
#: real answers so the difference is visible rather than hidden.
DIFFERENT_GRAPH: frozenset[str] = frozenset({"pf-vaat-no-is", "pf-explore-no-is"})


@dataclass(frozen=True)
class Disagreement:
    case: str
    variant: str
    detail: str


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _by_case(payload: dict[str, Any], kind: str) -> dict[tuple[str, int, str], dict[str, Any]]:
    return {(r["case"], r["depth"], r["variant"]): r for r in payload["results"] if r["kind"] == kind}


def check_answers(pg: dict[str, Any], neo: dict[str, Any]) -> list[Disagreement]:
    """Every PostgreSQL variant against Neo4j, case by case.

    Shortest path compares the DISTANCE, not the path. `shortestPath` returns one
    shortest path and breaks ties arbitrarily; gm-database-schema-9c8.3 recorded
    two different correct four-hop answers for the same pair on two runs of the
    identical query. Comparing paths would report that as a failure.

    Explore compares the `(id, type, dist)` triples of the UNBOUNDED discovery
    set. The product query is `ORDER BY dist LIMIT 100` over a distance band that
    holds several hundred qualifying vertices, so the hundred that comes back is
    an arbitrary hundred on either engine.
    """
    out: list[Disagreement] = []
    neo_paths = _by_case(neo, "path")
    for (case, depth, variant), record in _by_case(pg, "path").items():
        baseline = neo_paths.get((case, depth, "cypher"))
        if variant in DIFFERENT_GRAPH or baseline is None or "answer" not in record or "answer" not in baseline:
            continue
        mine, theirs = record["answer"], baseline["answer"]
        if bool(mine["found"]) != bool(theirs["found"]):
            out.append(Disagreement(case, variant, f"found {mine['found']} vs neo4j {theirs['found']} at cap {depth}"))
        elif mine["found"] and mine["depth"] != theirs["depth"]:
            out.append(Disagreement(case, variant, f"depth {mine['depth']} vs neo4j {theirs['depth']} at cap {depth}"))

    neo_explore = _by_case(neo, "explore")
    for (case, depth, variant), record in _by_case(pg, "explore").items():
        baseline = neo_explore.get((case, depth, "cypher"))
        if variant in DIFFERENT_GRAPH or baseline is None or "answer" not in record or "answer" not in baseline:
            continue
        mine = {tuple(r) for r in record["answer"]["rows"]}
        theirs = {tuple(r) for r in baseline["answer"]["rows"]}
        if mine != theirs:
            only_mine, only_theirs = mine - theirs, theirs - mine
            out.append(
                Disagreement(
                    f"{case} *1..{depth}",
                    variant,
                    f"{len(mine)} rows vs neo4j {len(theirs)}; "
                    f"{len(only_mine)} only here, {len(only_theirs)} only on neo4j; "
                    f"examples here={sorted(only_mine)[:3]} neo4j={sorted(only_theirs)[:3]}",
                )
            )
    return out


def verdict(pg: dict[str, Any], neo: dict[str, Any]) -> dict[str, Any]:
    """The bead's gate, evaluated rather than asserted.

    GO if every shortest-path case INCLUDING THE MISS has p95 at or under one
    second AND explore `*1..3` has p95 at or under one second, at the large
    scale, with answers equal to Neo4j.

    The two halves are evaluated separately and then combined, because they are
    different functions with different implementations and a reader needs to know
    which half failed. A path variant carries the shortest-path half; the explore
    traversal carries the other half and is shared by all of them. A variant is
    GO only if it clears its own half and the explore half clears too.
    """
    disagreements = check_answers(pg, neo)
    gate = VERDICT_P95_MS

    path_worst: dict[str, dict[str, Any]] = {}
    explore_worst: float | None = None
    explore_over: list[dict[str, Any]] = []

    for record in pg["results"]:
        if not record.get("verdict_gate") or "p95_ms" not in record:
            continue
        p95 = float(record["p95_ms"])
        if record["kind"] == "path":
            slot = path_worst.setdefault(record["variant"], {"worst_p95_ms": 0.0, "over_gate": []})
            slot["worst_p95_ms"] = max(slot["worst_p95_ms"], p95)
            if p95 > gate:
                slot["over_gate"].append({"workload": record["workload"], "p95_ms": p95, "case": record["case"]})
        else:
            explore_worst = p95 if explore_worst is None else max(explore_worst, p95)
            if p95 > gate:
                explore_over.append({"workload": record["workload"], "p95_ms": p95})

    explore_go = explore_worst is not None and explore_worst <= gate
    for slot in path_worst.values():
        slot["worst_p95_ms"] = round(slot["worst_p95_ms"], 3)
        slot["over_gate"].sort(key=lambda r: -r["p95_ms"])
        slot["path_go"] = not slot["over_gate"]
        slot["go"] = bool(slot["path_go"] and explore_go and not disagreements)

    passing = sorted(v for v, slot in path_worst.items() if slot["go"])
    # The ceiling a NO-GO has to state: the worst p95 the best path variant
    # reached, and the case it reached it on.
    ceiling: dict[str, Any] | None = None
    if not passing and path_worst:
        best = min(path_worst.items(), key=lambda kv: kv[1]["worst_p95_ms"])
        ceiling = {
            "variant": best[0],
            "worst_p95_ms": best[1]["worst_p95_ms"],
            "cases_over_gate": [r["case"] for r in best[1]["over_gate"]],
        }

    return {
        "scale": pg["scale"],
        "mode": pg["mode"],
        "answers_match": not disagreements,
        "disagreements": [d.__dict__ for d in disagreements],
        "gate_p95_ms": gate,
        "path": path_worst,
        "explore": {
            "worst_p95_ms": None if explore_worst is None else round(explore_worst, 3),
            "over_gate": explore_over,
            "go": explore_go,
        },
        "variants_passing": passing,
        "measured_ceiling": ceiling,
        "go": bool(passing) and not disagreements,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres", type=Path, required=True)
    parser.add_argument("--neo4j", type=Path, required=True)
    parser.add_argument("--output", "-o", type=Path)
    args = parser.parse_args(argv)

    pg, neo = load(args.postgres), load(args.neo4j)
    result = verdict(pg, neo)
    text = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    sys.stdout.write(text + "\n")
    return 0 if result["answers_match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
