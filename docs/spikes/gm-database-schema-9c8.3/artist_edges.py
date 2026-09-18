"""Emit the Discogs artist-to-artist path edges the sibling spike's generator already produces.

`_PATH_REL_TYPES` in ``api/queries/neo4j_queries.py`` is
``BY|ON|IS|ALIAS_OF|MEMBER_OF|DERIVED_FROM``. Eight of the ten relations behind
those six types are materialized by ``gm-database-schema-9c8.1/materialize.sql``.
The two that are not are ``alias_of`` and ``member_of``, and the reason is worth
stating precisely, because it decides what this file has to do.

``gm-database-schema-9c8.1/generate.py`` does generate Discogs artist-to-artist
relationships: ``iter_mb_relationships`` types 15% of its stream from
``DISCOGS_RELATIONSHIP_TYPES = ("ALIAS_OF", "MEMBER_OF")``. It writes them to the
**Neo4j** target and drops them from the **PostgreSQL** target, because in the
shipped schema those edges are derived from the Discogs artist documents rather
than from ``musicbrainz.relationships`` — and the generator emits artist
documents with no ``aliases`` or ``groups`` block for them to be derived from.

So the sibling spike's catalog is asymmetric exactly on the edges this spike
traverses: Neo4j has them, PostgreSQL does not. Measuring the two engines across
that asymmetry would not be a comparison. This file closes it from the
PostgreSQL side, by replaying the same stream and writing the blocks the shipped
views read, so both engines end up holding the same artist-to-artist edges.

Nothing in ``gm-database-schema-9c8.1/`` is modified; ``generate.py`` is imported
and its iterator is walked.

    uv run python artist_edges.py --scale synthetic > artist-edges.csv

Columns: ``owner_id,element_id,block``, where ``block`` is the key the row has to
be written under in the owner's document for the shipped view to derive the edge:

* ``ALIAS_OF``  ``(s)-[:ALIAS_OF]->(t)`` — ``graph.alias_of`` reads
  ``data -> 'aliases'`` and yields ``(alias_artist_id = element, artist_id = owner)``,
  so ``s`` goes in ``t``'s ``aliases``.
* ``MEMBER_OF`` ``(s)-[:MEMBER_OF]->(t)`` — ``graph.member_of`` reads
  ``data -> 'groups'`` and yields ``(member_artist_id = owner, group_artist_id = element)``,
  so ``t`` goes in ``s``'s ``groups``.

``members`` is deliberately never written. It is the reciprocal of ``groups`` and
the shipped view already collapses the pair with ``UNION``; writing both would
exercise the view's deduplication rather than the traversal.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final


if TYPE_CHECKING:
    from collections.abc import Iterator

SEED: Final = 20260917
_GENERATOR: Final = Path(__file__).resolve().parent.parent / "gm-database-schema-9c8.1" / "generate.py"


def _load_generator() -> object:
    """Import the sibling spike's generator by path, without copying any of it."""
    spec = importlib.util.spec_from_file_location("gm9c81_generate", _GENERATOR)
    if spec is None or spec.loader is None:  # pragma: no cover - a missing sibling is a broken checkout
        message = f"cannot import the sibling spike generator at {_GENERATOR}"
        raise SystemExit(message)
    module = importlib.util.module_from_spec(spec)
    # `generate.py` declares dataclasses, and `dataclasses` resolves a field's
    # type through `sys.modules[cls.__module__]`. A module executed without
    # being registered there first is not importable by its own decorators.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def rows(scale_name: str) -> Iterator[tuple[str, str, str]]:
    """Yield ``(owner_id, element_id, block)`` for every Discogs artist-to-artist path edge."""
    generator = _load_generator()
    scale = generator.SCALES[scale_name]  # type: ignore[attr-defined]
    for relationship in generator.iter_mb_relationships(SEED, scale):  # type: ignore[attr-defined]
        if relationship.source != "discogs":
            continue
        source = str(relationship.source_discogs_id)
        target = str(relationship.target_discogs_id)
        if relationship.relationship_type == "ALIAS_OF":
            yield target, source, "aliases"
        elif relationship.relationship_type == "MEMBER_OF":
            yield source, target, "groups"


def main() -> int:
    """Write the CSV for one scale to stdout."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", default="synthetic")
    arguments = parser.parse_args()

    writer = csv.writer(sys.stdout, lineterminator="\n")
    count = 0
    for row in rows(arguments.scale):
        writer.writerow(row)
        count += 1
    sys.stderr.write(f"{count} rows\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
