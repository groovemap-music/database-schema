"""The measurement loop.

Throwaway spike harness for gm-database-schema-gkt.1. One engine, one scale, one
results file, which is the contract `compare.py` and `report.py` read.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .engines import Neo4jEngine, PostgresEngine
from .workloads import (
    MCP_BUDGET_S,
    SCALES,
    TRANSACTION_TIMEOUT_S,
    Workload,
    for_engine,
)


if TYPE_CHECKING:
    from .engines import Answer


def _log(message: str) -> None:
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


def percentiles(samples: list[float]) -> dict[str, float]:
    """p50 and p95 by nearest-rank, plus the spread either side of them.

    Nearest-rank rather than interpolation, because with 30 samples an
    interpolated p95 is a weighted average of the 28th and 29th values and does
    not correspond to any run that happened. Every number in this harness is a
    run that happened.
    """
    ordered = sorted(samples)
    n = len(ordered)
    return {
        "p50_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[min(n - 1, max(0, round(0.95 * n) - 1))], 3),
        "min_ms": round(ordered[0], 3),
        "max_ms": round(ordered[-1], 3),
        "mean_ms": round(statistics.fmean(ordered), 3),
        "stdev_ms": round(statistics.stdev(ordered), 3) if n > 1 else 0.0,
    }


def run_workload(engine: Any, workload: Workload) -> dict[str, Any]:
    record: dict[str, Any] = {
        "workload": workload.name,
        "kind": workload.kind,
        "case": workload.case,
        "variant": workload.variant,
        "depth": workload.depth,
        "requested_iterations": workload.iterations,
        "tags": list(workload.tags),
        "notes": workload.notes,
        "verdict_gate": workload.verdict,
    }

    # Warm-up runs are executed and discarded. They are not noise reduction; the
    # first execution of a PL/pgSQL body in a session plans every statement in it,
    # and a table that reports that plan cost as the workload's latency is
    # reporting a number no second request would ever see.
    try:
        engine.configure(workload)
        for _ in range(workload.warmup):
            engine.run_once(workload)
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record

    samples: list[float] = []
    errors = 0
    for _ in range(workload.iterations):
        try:
            samples.append(engine.run_once(workload))
        except Exception as exc:
            errors += 1
            record.setdefault("first_error", f"{type(exc).__name__}: {exc}")
    record["errors"] = errors
    record["iterations"] = len(samples)
    if not samples:
        record["error"] = record.get("first_error", "every iteration failed")
        return record
    record.update(percentiles(samples))
    record["samples_ms"] = [round(s, 3) for s in samples]

    try:
        answer: Answer = engine.answer(workload)
        record["answer"] = {
            "found": answer.found,
            "depth": answer.depth,
            "rows": sorted(answer.rows) if answer.rows else [],
            "row_count": len(answer.rows),
            "work": answer.work,
        }
    except Exception as exc:
        record["answer_error"] = f"{type(exc).__name__}: {exc}"

    try:
        record["accesses"] = engine.buffers(workload)
    except Exception as exc:
        record["accesses_error"] = f"{type(exc).__name__}: {exc}"

    return record


def capture_plans(engine: Any, workloads: list[Workload], outdir: Path) -> None:
    """Keep every plan behind every headline timing.

    gm-database-schema-9c8.3 was bounced by its reviewer for discarding the Neo4j
    access captures behind its table. These are written to `results/plans/` and
    committed.
    """
    plans = outdir / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    for workload in workloads:
        if "headline" not in workload.tags and "cap-sweep" not in workload.tags:
            continue
        target = plans / f"{engine.name}-{workload.name.replace('/', '-')}.txt"
        try:
            text = engine.plan(workload)
        except Exception as exc:
            text = f"capture failed: {type(exc).__name__}: {exc}"
        target.write_text(f"== {engine.name}  {workload.name}  depth {workload.depth}\n\n{text}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("postgres", "neo4j"), required=True)
    parser.add_argument("--scale", choices=tuple(SCALES), required=True)
    parser.add_argument("--mode", choices=("local", "cloud"), default="local")
    parser.add_argument("--dsn", default="postgresql://groovemap:spike-password@127.0.0.1:55434/groovemap")
    parser.add_argument("--neo4j-uri", default="bolt://127.0.0.1:57689")
    parser.add_argument("--neo4j-user", default="neo4j")
    parser.add_argument("--neo4j-password", default="spike-password")
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--tags", default="", help="comma-separated tag filter; empty means every workload")
    parser.add_argument("--calibration", type=Path, help="calibration.json for the host this ran on")
    args = parser.parse_args(argv)

    args.outdir.mkdir(parents=True, exist_ok=True)

    engine: Any
    if args.engine == "postgres":
        engine = PostgresEngine(args.dsn, TRANSACTION_TIMEOUT_S, args.scale)
        environment = {"version": engine.version(), "settings": engine.settings()}
    else:
        engine = Neo4jEngine(args.neo4j_uri, args.neo4j_user, args.neo4j_password, TRANSACTION_TIMEOUT_S, args.scale)
        environment = {"version": engine.version(), "counts": engine.counts()}

    workloads = for_engine(args.engine, args.scale)
    if args.tags:
        wanted = set(args.tags.split(","))
        workloads = [w for w in workloads if wanted & set(w.tags)]

    _log(f"{args.engine} / {args.scale} / {args.mode}: {len(workloads)} workloads")
    results: list[dict[str, Any]] = []
    for index, workload in enumerate(workloads, start=1):
        started = time.perf_counter()
        record = run_workload(engine, workload)
        results.append(record)
        shown = record.get("p50_ms", record.get("error", "?"))
        _log(f"  [{index:>3}/{len(workloads)}] {workload.name:<44} p50={shown} ({time.perf_counter() - started:.1f}s)")

    capture_plans(engine, workloads, args.outdir)

    payload = {
        "engine": args.engine,
        "scale": args.scale,
        "mode": args.mode,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "budgets": {"transaction_timeout_s": TRANSACTION_TIMEOUT_S, "mcp_s": MCP_BUDGET_S},
        "environment": environment,
        "calibration": json.loads(args.calibration.read_text()) if args.calibration and args.calibration.exists() else None,
        "results": results,
    }
    target = args.outdir / f"{args.engine}-{args.scale}-{args.mode}.json"
    target.write_text(json.dumps(payload, indent=2, default=str))
    _log(f"wrote {target}")
    engine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
