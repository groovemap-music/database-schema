"""Turn results files into the tables the spike document quotes.

Throwaway spike harness for gm-database-schema-gkt.1.

Markdown to stdout, not PNG charts. The db-alternatives harness this is adapted
from emits charts and leaves a human to transcribe its stdout tables into prose;
a spike document is read for its numbers and argued with, so the numbers are
generated in the form the document carries them and nothing is transcribed by
hand.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .compare import verdict
from .workloads import CASES, VERDICT_P95_MS


CASE_ORDER = [c.name for c in CASES]


def _fmt(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 10_000:
        return f"{value / 1000:,.1f} s"
    if value >= 1000:
        return f"{value:,.0f}"
    if value >= 10:
        return f"{value:.1f}"
    return f"{value:.2f}"


def _index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Results keyed by WORKLOAD NAME, which is the only unique key they have.

    An earlier revision keyed on ``(case, variant, depth)`` and that key is not
    unique: `path/unreachable/pf-vaat` and `path/unreachable-cap10/pf-vaat` are
    the same case, the same variant and the same depth — one is the gated
    measurement at the product's cap, the other is the top of the cap sweep — so
    the dict silently kept whichever was built last and five `unreachable`
    figures in the spike document were sourced from the cap-sweep run rather than
    from the run their table named. The two runs measure the identical statement,
    so the numbers were real and the difference was run-to-run noise, but a table
    must report the row it names.

    Workload names are assigned once in `workloads.py` and are unique by
    construction, so every lookup below names the exact row it wants.
    """
    index: dict[str, dict[str, Any]] = {}
    for record in payload["results"]:
        name = record["workload"]
        if name in index:  # pragma: no cover - a workloads.py bug, not a data one
            raise ValueError(f"duplicate workload name in results: {name}")
        index[name] = record
    return index


def _row(record: dict[str, Any] | None, key: str) -> float | None:
    if record is None or key not in record:
        return None
    return float(record[key])


def iterations_note(*records: dict[str, Any] | None) -> str:
    """One line naming the iteration count behind a table.

    Emitted under every table rather than stated once in prose, because the
    counts are not uniform — the Verdict rows run 30, and diagnostics that take
    seconds per iteration run fewer — and a reader comparing a p95 needs to know
    which it is without going to the results files.
    """
    counts: dict[int, list[str]] = {}
    for record in records:
        if record is None or "iterations" not in record:
            continue
        counts.setdefault(int(record["iterations"]), []).append(record["workload"])
    if not counts:
        return ""
    if len(counts) == 1:
        n = next(iter(counts))
        return f"\n*{n} timed iterations per row, after discarded warm-ups.*\n"
    parts = [f"{n} for {len(names)} row{'s' if len(names) != 1 else ''}" for n, names in sorted(counts.items())]
    return f"\n*Timed iterations per row, after discarded warm-ups: {', '.join(parts)}.*\n"


def headline(pg: dict[str, Any], neo: dict[str, Any], stat: str) -> list[str]:
    pgi, neoi = _index(pg), _index(neo)
    label = {"p50_ms": "p50", "p95_ms": "p95"}[stat]
    lines = [
        f"| Case | d | `pf.find_path` {label} | `pf.find_path_deg` {label} | Neo4j {label} |",
        "| --- | --- | --- | --- | --- |",
    ]
    used: list[dict[str, Any] | None] = []
    for case in CASE_ORDER:
        base_r = pgi.get(f"path/{case}/pf-vaat")
        deg = pgi.get(f"path/{case}/pf-vaat-deg")
        cyp = neoi.get(f"path/{case}/neo4j")
        used += [base_r, deg, cyp]
        answer = (base_r or {}).get("answer") or {}
        found = answer.get("found")
        distance = "—" if not found else str(answer.get("depth"))
        lines.append(f"| `{case}` | {distance} | {_fmt(_row(base_r, stat))} | {_fmt(_row(deg, stat))} | **{_fmt(_row(cyp, stat))}** |")
    lines.append(iterations_note(*used))
    return lines


def work_table(pg: dict[str, Any], neo: dict[str, Any]) -> list[str]:
    """Latency is not evidence on its own; this is what the searches did."""
    pgi, neoi = _index(pg), _index(neo)
    lines = [
        "| Case | Vertices expanded | Touch probes | Seen rows | PostgreSQL buffers | Neo4j DbHits |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for case in CASE_ORDER:
        base_r = pgi.get(f"path/{case}/pf-vaat") or {}
        cyp = neoi.get(f"path/{case}/neo4j") or {}
        work = (base_r.get("answer") or {}).get("work") or {}
        buffers = (base_r.get("accesses") or {}).get("total_accesses")
        hits = (cyp.get("accesses") or {}).get("total_accesses")
        if isinstance(work.get("expanded"), int):
            lines.append(
                f"| `{case}` | {work['expanded']:,} | {work.get('probes', 0):,} | "
                f"{work.get('seen_rows', 0):,} | {'—' if buffers is None else format(buffers, ',')} | "
                f"{'—' if hits is None else format(hits, ',')} |"
            )
        else:
            lines.append(f"| `{case}` | — | — | — | — | — |")
    return lines


def cap_sweep(pg: dict[str, Any], neo: dict[str, Any]) -> list[str]:
    pgi, neoi = _index(pg), _index(neo)
    lines = ["| Depth cap | `pf.find_path` p50 | `pf.find_path_deg` p50 | Neo4j p50 |", "| --- | --- | --- | --- |"]
    used: list[dict[str, Any] | None] = []
    for cap in (1, 2, 3, 4, 6, 8, 10):
        base_r = pgi.get(f"path/unreachable-cap{cap}/pf-vaat")
        deg = pgi.get(f"path/unreachable-cap{cap}/pf-vaat-deg")
        cyp = neoi.get(f"path/unreachable-cap{cap}/cypher")
        used += [base_r, deg, cyp]
        lines.append(f"| {cap} | {_fmt(_row(base_r, 'p50_ms'))} | {_fmt(_row(deg, 'p50_ms'))} | **{_fmt(_row(cyp, 'p50_ms'))}** |")
    lines.append(iterations_note(*used))
    return lines


def cap_cost(pg: dict[str, Any], neo: dict[str, Any]) -> list[str]:
    """A connected pair against caps below its own distance."""
    pgi, neoi = _index(pg), _index(neo)
    lines = [
        "| Cap | `pf.find_path` p50 / p95 | `pf.find_path_deg` p50 / p95 | Neo4j p50 / p95 | Answer |",
        "| --- | --- | --- | --- | --- |",
    ]
    used: list[dict[str, Any] | None] = []
    any_row = False
    for cap in (2, 3, 4, 6, 10):
        base_r = pgi.get(f"path/d6-cap{cap}/pf-vaat")
        deg = pgi.get(f"path/d6-cap{cap}/pf-vaat-deg")
        cyp = neoi.get(f"path/d6-cap{cap}/cypher")
        if base_r is None and deg is None and cyp is None:
            continue
        any_row = True
        used += [base_r, deg, cyp]
        found = ((base_r or {}).get("answer") or {}).get("found")
        answer = "found, d = 6" if found else "no path"

        def pair(rec: dict[str, Any] | None) -> str:
            return f"{_fmt(_row(rec, 'p50_ms'))} / {_fmt(_row(rec, 'p95_ms'))}"

        lines.append(f"| {cap} | {pair(base_r)} | {pair(deg)} | **{pair(cyp)}** | {answer} |")
    if not any_row:
        return []
    lines.append(iterations_note(*used))
    return lines


def explore_table(pg: dict[str, Any], neo: dict[str, Any]) -> list[str]:
    pgi, neoi = _index(pg), _index(neo)
    lines = [
        "| Hops | `pf.explore` p50 | `pf.explore` p95 | Neo4j p50 | Neo4j DbHits | Rows |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    used: list[dict[str, Any] | None] = []
    for hops in (1, 2, 3):
        base_r = pgi.get(f"explore/hops{hops}/pf-explore")
        cyp = neoi.get(f"explore/hops{hops}/neo4j")
        used += [base_r, cyp]
        hits = ((cyp or {}).get("accesses") or {}).get("total_accesses")
        rows = ((base_r or {}).get("answer") or {}).get("row_count", "—")
        lines.append(
            f"| `*1..{hops}` | {_fmt(_row(base_r, 'p50_ms'))} | {_fmt(_row(base_r, 'p95_ms'))} "
            f"| **{_fmt(_row(cyp, 'p50_ms'))}** | {'—' if hits is None else format(hits, ',')} | {rows} |"
        )
    lines.append(iterations_note(*used))
    return lines


def is_class_table(pg: dict[str, Any]) -> list[str]:
    pgi = _index(pg)
    lines = ["| Case | With `IS` p50 | Without `IS` p50 | Ratio |", "| --- | --- | --- | --- |"]
    used: list[dict[str, Any] | None] = []
    for case in CASE_ORDER:
        a, b = pgi.get(f"path/{case}/pf-vaat"), pgi.get(f"path/{case}/pf-vaat-no-is")
        used += [a, b]
        with_is, without = _row(a, "p50_ms"), _row(b, "p50_ms")
        ratio = "—" if not with_is or not without else f"{with_is / without:.2f}x"
        lines.append(f"| `{case}` | {_fmt(with_is)} | {_fmt(without)} | {ratio} |")
    for hops in (1, 2, 3):
        a, b = pgi.get(f"explore/hops{hops}/pf-explore"), pgi.get(f"explore/hops{hops}/pf-explore-no-is")
        used += [a, b]
        with_is, without = _row(a, "p50_ms"), _row(b, "p50_ms")
        ratio = "—" if not with_is or not without else f"{with_is / without:.2f}x"
        lines.append(f"| `explore *1..{hops}` | {_fmt(with_is)} | {_fmt(without)} | {ratio} |")
    lines.append(iterations_note(*used))
    return lines


def hstore_table(pg: dict[str, Any]) -> list[str]:
    pgi = _index(pg)
    lines = ["| Case | Relation seen set p50 | hstore seen set p50 |", "| --- | --- | --- |"]
    used: list[dict[str, Any] | None] = []
    for case in CASE_ORDER:
        base_r = pgi.get(f"path/{case}/pf-vaat")
        hs_record = pgi.get(f"path/{case}/pf-vaat-hstore")
        used += [base_r, hs_record]
        hs = _row(hs_record, "p50_ms")
        shown = _fmt(hs) if hs is not None else f"**{(hs_record or {}).get('error', 'not measured')[:42]}**"
        lines.append(f"| `{case}` | {_fmt(_row(base_r, 'p50_ms'))} | {shown} |")
    lines.append(iterations_note(*used))
    return lines


def machine(payload: dict[str, Any]) -> list[str]:
    cal = payload.get("calibration")
    if not cal:
        return ["No calibration was captured for this run."]
    system, marks = cal["system"], cal["benchmarks"]
    return [
        "| | |",
        "| --- | --- |",
        f"| Label | {cal.get('label', '—')} |",
        f"| Platform | {system['platform']} |",
        f"| CPU count | {system['cpu_count']} |",
        f"| RAM | {system['ram_gb']} GiB |",
        f"| SHA-256 single thread | {marks['cpu_single_thread']['ops_per_sec']:,.0f} ops/s |",
        f"| SHA-256 all cores | {marks['cpu_multi_thread']['ops_per_sec']:,.0f} ops/s |",
        f"| Memory read | {marks['memory_bandwidth']['read_mb_per_sec']:,.0f} MB/s |",
        f"| Disk sequential write | {marks['disk_sequential_write']['mb_per_sec']:,.0f} MB/s |",
        f"| Disk random read 4k | {marks['disk_random_read']['iops']:,.0f} IOPS |",
        f"| Python sort 1M floats | {marks['python_sort']['avg_sec']} s |",
    ]


def render(pg: dict[str, Any], neo: dict[str, Any]) -> str:
    out: list[str] = []
    scale, mode = pg["scale"], pg["mode"]
    out.append(f"## {scale} scale, {mode} mode\n")
    out.append("### The machine\n")
    out += machine(pg)
    out.append("")
    out.append(f"PostgreSQL: `{pg['environment'].get('version', '?')}`\n")
    out.append(f"Neo4j: `{neo['environment'].get('version', '?')}`\n")
    out.append("### Shortest path, median\n")
    out += headline(pg, neo, "p50_ms")
    out.append("")
    out.append(f"### Shortest path, p95 — the Verdict gate is {VERDICT_P95_MS:.0f} ms\n")
    out += headline(pg, neo, "p95_ms")
    out.append("")
    out.append("### Work done\n")
    out += work_table(pg, neo)
    out.append("")
    out.append("### Depth cap against the miss\n")
    out += cap_sweep(pg, neo)
    out.append("")
    cost = cap_cost(pg, neo)
    if cost:
        out.append("### What a depth cap costs on a pair that is actually connected\n")
        out += cost
        out.append("")
    out.append("### Bounded explore traversal\n")
    out += explore_table(pg, neo)
    out.append("")
    out.append("### The IS edge class, for information only\n")
    out += is_class_table(pg)
    out.append("")
    out.append("### The seen set as a relation against an hstore\n")
    out += hstore_table(pg)
    out.append("")
    out.append("### Verdict gate\n")
    out.append("```json")
    out.append(json.dumps(verdict(pg, neo), indent=2))
    out.append("```")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres", type=Path, required=True)
    parser.add_argument("--neo4j", type=Path, required=True)
    parser.add_argument("--output", "-o", type=Path)
    args = parser.parse_args(argv)
    text = render(json.loads(args.postgres.read_text()), json.loads(args.neo4j.read_text()))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
