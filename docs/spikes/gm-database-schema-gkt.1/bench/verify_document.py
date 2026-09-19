"""Fail if the spike document's cross-mode tables have drifted from the results.

Throwaway spike harness for gm-database-schema-gkt.1.

`document.py` generates the tables that put local and cloud side by side. Nothing
stops someone pasting them in and then editing a cell, which is exactly what went
wrong twice: six cloud figures came from the cap-sweep workload rather than the
gated one, and one case row was dropped entirely.

So the invariant is checked rather than promised. Every table `document.py`
emits, except the ones the document deliberately does not carry, must appear in
the document VERBATIM. A table is matched by its whole block, not by its header:
the three path tables share a header row, and an earlier version of this check
keyed on the header and silently verified the same table three times while the
large-scale one went unchecked.

    python -m bench.verify_document --results <dir> --document <file>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .document import Results, explore_table, path_table, servable_table


def _block(lines: list[str]) -> str:
    """Flatten a generated table the same way the document side is flattened.

    `iterations_note` returns a string with its own leading and trailing
    newlines, so an element of `lines` can itself be several lines. Splitting
    here as well is what makes the two sides comparable; without it every table
    reports drift while every one of its rows is present.
    """
    flat: list[str] = []
    for line in lines:
        flat.extend(part.rstrip() for part in line.splitlines() if part.strip())
    return "\n".join(flat).strip()


def checks(root: Path) -> list[tuple[str, str]]:
    """The named tables the document must carry verbatim."""
    large, small = Results(root, "large"), Results(root, "small")
    return [
        ("shortest path, large scale, p95, both modes", _block(path_table(large))),
        ("shortest path, small scale, p95, both modes", _block(path_table(small))),
        ("bounded explore traversal, both modes", _block(explore_table(large))),
        ("maximum depth servable inside the budget", _block(servable_table(large))),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--document", type=Path, required=True)
    args = parser.parse_args(argv)

    document = args.document.read_text()
    # Blank lines between a table and its iterations note differ between the
    # generated file and the document's flow, so both sides are compared with
    # blank lines removed and trailing space stripped.
    flattened = "\n".join(line.rstrip() for line in document.splitlines() if line.strip())

    failures = 0
    for name, block in checks(args.results):
        if block in flattened:
            sys.stdout.write(f"ok       {name}\n")
            continue
        failures += 1
        sys.stdout.write(f"DRIFTED  {name}\n")
        generated = block.splitlines()
        present = [line for line in flattened.splitlines() if line.startswith("|")]
        for line in generated:
            if line.startswith("|") and line not in present:
                sys.stdout.write(f"           generated row absent from the document: {line}\n")
    if failures:
        sys.stdout.write(f"\n{failures} table(s) drifted. Regenerate with `python -m bench.document` and paste.\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
