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


def _index(payload: dict[str, Any]) -> dict[tuple[str, str, int], dict[str, Any]]:
    return {(r["case"], r["variant"], r["depth"]): r for r in payload["results"]}


def _row(record: dict[str, Any] | None, key: str) -> float | None:
    if record is None or key not in record:
        return None
    return float(record[key])


def headline(pg: dict[str, Any], neo: dict[str, Any], stat: str) -> list[str]:
    pgi, neoi = _index(pg), _index(neo)
    label = {"p50_ms": "p50", "p95_ms": "p95"}[stat]
    lines = [
        f"| Case | d | `pf.find_path` {label} | `pf.find_path_deg` {label} | Neo4j {label} |",
        "| --- | --- | --- | --- | --- |",
    ]
    for case in CASE_ORDER:
        base = pgi.get((case, "pf-vaat", 10))
        deg = pgi.get((case, "pf-vaat-deg", 10))
        cyp = neoi.get((case, "cypher", 10))
        answer = (base or {}).get("answer") or {}
        found = answer.get("found")
        distance = "—" if not found else str(answer.get("depth"))
        lines.append(f"| `{case}` | {distance} | {_fmt(_row(base, stat))} | {_fmt(_row(deg, stat))} | **{_fmt(_row(cyp, stat))}** |")
    return lines


def work_table(pg: dict[str, Any], neo: dict[str, Any]) -> list[str]:
    """Latency is not evidence on its own; this is what the searches did."""
    pgi, neoi = _index(pg), _index(neo)
    lines = [
        "| Case | Vertices expanded | Touch probes | Seen rows | PostgreSQL buffers | Neo4j DbHits |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for case in CASE_ORDER:
        base = pgi.get((case, "pf-vaat", 10)) or {}
        cyp = neoi.get((case, "cypher", 10)) or {}
        work = (base.get("answer") or {}).get("work") or {}
        buffers = (base.get("accesses") or {}).get("total_accesses")
        hits = (cyp.get("accesses") or {}).get("total_accesses")
        lines.append(
            f"| `{case}` | {work.get('expanded', '—'):,} | {work.get('probes', '—'):,} | "
            f"{work.get('seen_rows', '—'):,} | {buffers if buffers is None else format(buffers, ',')} | "
            f"{hits if hits is None else format(hits, ',')} |"
            if isinstance(work.get("expanded"), int)
            else f"| `{case}` | — | — | — | — | — |"
        )
    return lines


def cap_sweep(pg: dict[str, Any], neo: dict[str, Any]) -> list[str]:
    pgi, neoi = _index(pg), _index(neo)
    lines = ["| Depth cap | `pf.find_path` p50 | `pf.find_path_deg` p50 | Neo4j p50 |", "| --- | --- | --- | --- |"]
    for cap in (1, 2, 3, 4, 6, 8, 10):
        lines.append(
            f"| {cap} | {_fmt(_row(pgi.get(('unreachable', 'pf-vaat', cap)), 'p50_ms'))} "
            f"| {_fmt(_row(pgi.get(('unreachable', 'pf-vaat-deg', cap)), 'p50_ms'))} "
            f"| **{_fmt(_row(neoi.get(('unreachable', 'cypher', cap)), 'p50_ms'))}** |"
        )
    return lines


def explore_table(pg: dict[str, Any], neo: dict[str, Any]) -> list[str]:
    pgi, neoi = _index(pg), _index(neo)
    lines = [
        "| Hops | `pf.explore` p50 | `pf.explore` p95 | Neo4j p50 | Neo4j DbHits | Rows |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for hops in (1, 2, 3):
        base = pgi.get(("explore-artist-5665", "pf-explore", hops))
        cyp = neoi.get(("explore-artist-5665", "cypher", hops))
        hits = ((cyp or {}).get("accesses") or {}).get("total_accesses")
        rows = ((base or {}).get("answer") or {}).get("row_count", "—")
        lines.append(
            f"| `*1..{hops}` | {_fmt(_row(base, 'p50_ms'))} | {_fmt(_row(base, 'p95_ms'))} "
            f"| **{_fmt(_row(cyp, 'p50_ms'))}** | {hits if hits is None else format(hits, ',')} | {rows} |"
        )
    return lines


def is_class_table(pg: dict[str, Any]) -> list[str]:
    pgi = _index(pg)
    lines = ["| Case | With `IS` p50 | Without `IS` p50 | Ratio |", "| --- | --- | --- | --- |"]
    for case in CASE_ORDER:
        with_is = _row(pgi.get((case, "pf-vaat", 10)), "p50_ms")
        without = _row(pgi.get((case, "pf-vaat-no-is", 10)), "p50_ms")
        ratio = "—" if not with_is or not without else f"{with_is / without:.2f}x"
        lines.append(f"| `{case}` | {_fmt(with_is)} | {_fmt(without)} | {ratio} |")
    for hops in (1, 2, 3):
        with_is = _row(pgi.get(("explore-artist-5665", "pf-explore", hops)), "p50_ms")
        without = _row(pgi.get(("explore-artist-5665", "pf-explore-no-is", hops)), "p50_ms")
        ratio = "—" if not with_is or not without else f"{with_is / without:.2f}x"
        lines.append(f"| `explore *1..{hops}` | {_fmt(with_is)} | {_fmt(without)} | {ratio} |")
    return lines


def hstore_table(pg: dict[str, Any]) -> list[str]:
    pgi = _index(pg)
    lines = ["| Case | Relation seen set p50 | hstore seen set p50 |", "| --- | --- | --- |"]
    for case in CASE_ORDER:
        base = _row(pgi.get((case, "pf-vaat", 10)), "p50_ms")
        hs_record = pgi.get((case, "pf-vaat-hstore", 10))
        hs = _row(hs_record, "p50_ms")
        shown = _fmt(hs) if hs is not None else f"**{(hs_record or {}).get('error', 'not measured')[:42]}**"
        lines.append(f"| `{case}` | {_fmt(base)} | {shown} |")
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
