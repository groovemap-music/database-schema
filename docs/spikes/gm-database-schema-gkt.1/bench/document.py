"""The cross-mode tables the spike document carries, generated rather than merged by hand.

Throwaway spike harness for gm-database-schema-gkt.1.

`report.py` renders one scale in one mode, which is what a reader wants while a
run is in flight. The document's widest tables put local and cloud side by side,
and in the first two revisions those were assembled by hand from two generated
reports. That merge is where figures slipped — six cloud cells came from the
cap-sweep workload rather than the gated one, and one whole case row was dropped
— twice, for the same reason, after the underlying index bug had already been
fixed.

So the merge is generated too. Nothing in the document's Evidence or Verdict
sections is assembled by hand now: `report.py` emits the single-mode tables and
this emits the cross-mode ones, and both read the same results files.

    python -m bench.document --results docs/spikes/gm-database-schema-gkt.1/results
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .report import _fmt, _index, _row, iterations_note
from .workloads import CASES, MCP_BUDGET_S, TRANSACTION_TIMEOUT_S, VERDICT_P95_MS


if TYPE_CHECKING:
    from collections.abc import Iterable


CASE_ORDER = [c.name for c in CASES]


class Results:
    """The four result files of one scale, keyed by mode and engine.

    Loaded by the directory layout rather than by eight command-line paths,
    because the layout is a contract `results/README.md` documents and eight
    paths is eight chances to pass the wrong one.
    """

    def __init__(self, root: Path, scale: str) -> None:
        self.scale = scale
        self.modes: dict[str, dict[str, dict[str, Any]]] = {}
        for mode in ("local", "cloud"):
            directory = root / f"{scale}-{mode}"
            engines: dict[str, dict[str, Any]] = {}
            for engine in ("postgres", "neo4j"):
                path = directory / f"{engine}-{scale}-{mode}.json"
                if path.exists():
                    engines[engine] = _index(json.loads(path.read_text()))
            if engines:
                self.modes[mode] = engines

    def get(self, mode: str, engine: str, workload: str) -> dict[str, Any] | None:
        return self.modes.get(mode, {}).get(engine, {}).get(workload)

    def every(self, workloads: Iterable[tuple[str, str, str]]) -> list[dict[str, Any] | None]:
        return [self.get(*w) for w in workloads]


def _cell(record: dict[str, Any] | None, stat: str) -> str:
    return _fmt(_row(record, stat))


def path_table(res: Results, stat: str = "p95_ms") -> list[str]:
    """One row per case, local and cloud side by side.

    The workload names are spelled out in full. `path/{case}/pf-vaat` is the
    gated measurement at the product's cap; `path/{case}-cap10/pf-vaat` is the
    cap sweep and is a DIFFERENT row that this table must never show.
    """
    have_cloud = "cloud" in res.modes
    header = ["| Case | d | `find_path` local | `find_path_deg` local | Neo4j local"]
    rule = ["| --- | --- | --- | --- | ---"]
    if have_cloud:
        header.append(" | `find_path` cloud | `find_path_deg` cloud | Neo4j cloud")
        rule.append(" | --- | --- | ---")
    lines = ["".join(header) + " |", "".join(rule) + " |"]
    used: list[dict[str, Any] | None] = []
    for case in CASE_ORDER:
        wanted = [
            ("local", "postgres", f"path/{case}/pf-vaat"),
            ("local", "postgres", f"path/{case}/pf-vaat-deg"),
            ("local", "neo4j", f"path/{case}/neo4j"),
        ]
        if have_cloud:
            wanted += [
                ("cloud", "postgres", f"path/{case}/pf-vaat"),
                ("cloud", "postgres", f"path/{case}/pf-vaat-deg"),
                ("cloud", "neo4j", f"path/{case}/neo4j"),
            ]
        records = res.every(wanted)
        used += records
        answer = (records[0] or {}).get("answer") or {}
        distance = "—" if not answer.get("found") else str(answer.get("depth"))
        cells = [_cell(records[0], stat), _cell(records[1], stat), f"**{_cell(records[2], stat)}**"]
        if have_cloud:
            cells += [_cell(records[3], stat), _cell(records[4], stat), f"**{_cell(records[5], stat)}**"]
        lines.append(f"| `{case}` | {distance} | " + " | ".join(cells) + " |")
    lines.append(iterations_note(*used))
    return lines


def explore_table(res: Results) -> list[str]:
    have_cloud = "cloud" in res.modes
    header = "| Hops | `pf.explore` p50 local | p95 local | Neo4j local"
    rule = "| --- | --- | --- | ---"
    if have_cloud:
        header += " | p50 cloud | p95 cloud | Neo4j cloud"
        rule += " | --- | --- | ---"
    lines = [header + " | Neo4j DbHits |", rule + " | --- |"]
    used: list[dict[str, Any] | None] = []
    for hops in (1, 2, 3):
        pg_local = res.get("local", "postgres", f"explore/hops{hops}/pf-explore")
        neo_local = res.get("local", "neo4j", f"explore/hops{hops}/neo4j")
        used += [pg_local, neo_local]
        cells = [_cell(pg_local, "p50_ms"), _cell(pg_local, "p95_ms"), f"**{_cell(neo_local, 'p50_ms')}**"]
        if have_cloud:
            pg_cloud = res.get("cloud", "postgres", f"explore/hops{hops}/pf-explore")
            neo_cloud = res.get("cloud", "neo4j", f"explore/hops{hops}/neo4j")
            used += [pg_cloud, neo_cloud]
            cells += [_cell(pg_cloud, "p50_ms"), _cell(pg_cloud, "p95_ms"), f"**{_cell(neo_cloud, 'p50_ms')}**"]
        hits = ((neo_local or {}).get("accesses") or {}).get("total_accesses")
        cells.append("—" if hits is None else format(hits, ","))
        lines.append(f"| `*1..{hops}` | " + " | ".join(cells) + " |")
    lines.append(iterations_note(*used))
    return lines


def servable_table(res: Results) -> list[str]:
    """Maximum depth servable, read against the three budgets.

    The p95 is the WORST of the two modes for each case, which is the only
    reading that supports a recommendation: a depth is servable if it is
    servable on the slower machine, not on the faster one.
    """
    lines = [
        f"| Distance found | Worst p95 | Inside {TRANSACTION_TIMEOUT_S} s | Inside MCP {MCP_BUDGET_S} s | Inside the 1 s gate |",
        "| --- | --- | --- | --- | --- |",
    ]
    for case in CASE_ORDER:
        if case == "d1-artist-release":
            continue  # the same distance as d1-artist-artist; the table is by distance
        records = [res.get(mode, "postgres", f"path/{case}/pf-vaat") for mode in ("local", "cloud")]
        values = [_row(r, "p95_ms") for r in records if _row(r, "p95_ms") is not None]
        if not values:
            continue
        worst = max(values)
        answer = (records[0] or {}).get("answer") or {}
        distance = "no path" if not answer.get("found") else str(answer.get("depth"))
        gate = "**yes**" if worst <= VERDICT_P95_MS else "**no**"
        lines.append(
            f"| {distance} | {_fmt(worst)} ms | {'yes' if worst <= TRANSACTION_TIMEOUT_S * 1000 else 'no'} "
            f"| {'yes' if worst <= MCP_BUDGET_S * 1000 else 'no'} | {gate} |"
        )
    return lines


def render(root: Path) -> str:
    large, small = Results(root, "large"), Results(root, "small")
    out: list[str] = ["<!-- generated by bench/document.py; do not edit by hand -->", ""]
    out.append("## Shortest path at the large scale, p95, both modes\n")
    out += path_table(large)
    out.append("")
    out.append("## Shortest path at the large scale, median, both modes\n")
    out += path_table(large, "p50_ms")
    out.append("")
    out.append("## The small scale, p95, both modes\n")
    out += path_table(small)
    out.append("")
    out.append("## Bounded explore traversal, both modes\n")
    out += explore_table(large)
    out.append("")
    out.append("## Maximum depth servable inside the budget\n")
    out += servable_table(large)
    out.append("")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", "-o", type=Path)
    args = parser.parse_args(argv)
    text = render(args.results)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
