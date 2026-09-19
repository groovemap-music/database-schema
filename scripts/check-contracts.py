"""Validate the persistence compatibility contract without touching a database."""

import json
import tomllib
from pathlib import Path

from groovemap_schema import __version__
from groovemap_schema.postgres import (
    _GRAPH_STATEMENTS,
    PROPERTY_GRAPH_MINIMUM_SERVER_VERSION,
    PROPERTY_GRAPH_NAME,
    PROPERTY_GRAPH_SCHEMA,
    PROPERTY_GRAPH_SWITCH,
    _property_graph_edges,
    _property_graph_vertices,
)


ROOT = Path(__file__).resolve().parents[1]
contract_path = ROOT / "contracts" / "persistence" / "v1" / "compatibility.json"
contract = json.loads(contract_path.read_text())

with (ROOT / "pyproject.toml").open("rb") as source:
    version = tomllib.load(source)["project"]["version"]

assert contract["contract"] == "groovemap.persistence"
assert contract["version"] == 1
assert contract["compatibility"]["consumer_rollout"] == "expand, migrate consumers, then contract"
assert contract["application_runtime"]["package"] == "groovemap-runtime"
assert len(contract["application_runtime"]["tested_commit"]) == 40
assert contract["sources"] == [
    "src/groovemap_schema/neo4j.py",
    "src/groovemap_schema/postgres.py",
]

# The graph schema is recorded as an additive object of contract v1. Every count
# and every relation below is read back from the statement list rather than
# restated, so a relation added, removed, or reshaped in postgres.py fails this
# check instead of silently making the contract a stale description of the schema
# consumers read.
graph = contract["graph_schema"]
statement_names = [name for name, _ in _GRAPH_STATEMENTS]
view_statements = {name.removesuffix(" view"): body for name, body in _GRAPH_STATEMENTS if name.endswith(" view")}
table_statements = {name.removesuffix(" table"): body for name, body in _GRAPH_STATEMENTS if name.endswith(" table")}
declared_shapes = {
    **{name.removeprefix("graph."): "view" for name in view_statements},
    **{name.removeprefix("graph."): "table" for name in table_statements},
}

assert graph["schema"] == PROPERTY_GRAPH_SCHEMA
assert graph["kind"] == "additive"
assert graph["availability"] == "unconditional"
assert graph["introduced_in_contract_version"] == contract["version"]
assert graph["views"] == len(view_statements)
assert graph["tables"] == len(table_statements)
assert graph["relation_count"] == len(declared_shapes)
assert graph["vertex_relations"] == len(_property_graph_vertices())
assert graph["edge_relations"] == len(_property_graph_edges())
assert graph["functions"] == sorted(name.removesuffix(" function") for name in statement_names if name.endswith(" function"))

# Every relation carries its shape and the service that writes it. A consumer
# reading a relation needs both: the shape says whether an index is available,
# and the owner says who to chase when the relation is empty. The owner is the
# one thing here the statement list cannot prove, so it is checked for being a
# known service rather than for being correct.
OWNERS = {
    "catalog-api",
    "discogs-sql-loader",
    "discogs-sql-loader, catalog-api",
    "discogs-sql-loader, musicbrainz-sql-loader",
    "musicbrainz-sql-loader",
}
relations = graph["relations"]
assert set(relations) == set(declared_shapes), set(relations) ^ set(declared_shapes)
for relation, recorded in relations.items():
    assert recorded["shape"] == declared_shapes[relation], relation
    assert recorded["owner"] in OWNERS, relation

# `graph.release_degree` is the one relation the coverage spike records as split
# across two owners, and it must stay recorded as such: a loader-written base
# count plus a live count over the personal tables catalog-api writes.
assert relations["release_degree"]["owner"] == "discogs-sql-loader, catalog-api"
assert relations["release_degree"]["shape"] == "view"
assert relations["release_degree_base"]["shape"] == "table"

# The property graph binds every relation except the loader-written half of
# release degree, which it reaches through the view that sums it.
element_views = {element.view for element in (*_property_graph_vertices(), *_property_graph_edges())}
assert element_views == set(declared_shapes) - {"release_degree_base"}

# Appending a column is the only view change safe to ship on its own; the engine
# enforces the rest by refusing the replacement.
evolution = graph["view_evolution"]
assert evolution["rule"] == "append-only"
assert contract["compatibility"]["consumer_rollout"] in evolution["migration"]
assert evolution["forbidden"] == [
    "dropping a column",
    "renaming a view or a view column",
    "reordering columns",
    "retyping a column",
]

# Every vertex key is a published column now, not an appended restatement. The
# contract records the retirement; this is what proves it happened.
keys = graph["key_columns"]
assert keys["kind"] == "additive"
assert keys["availability"] == "unconditional"
assert keys["type"] == "text"
assert not [column for vertex in _property_graph_vertices() for column in vertex.key if column.endswith("_key")]
for body in (*view_statements.values(), *table_statements.values()):
    for retired in ("artist_key", "label_key", "master_key", "release_key"):
        assert retired not in body, retired

# The property graph is conditional on both the server version and the switch, so
# a consumer that assumes it exists is reading a contract this repository never made.
property_graph = contract["graph_schema"]["property_graph"]
assert property_graph["name"] == PROPERTY_GRAPH_NAME
assert property_graph["kind"] == "additive"
assert property_graph["availability"] == "conditional"
assert property_graph["conditions"] == [
    f"server_version_num >= {PROPERTY_GRAPH_MINIMUM_SERVER_VERSION}",
    f"the {PROPERTY_GRAPH_SWITCH} environment switch is enabled",
]
assert property_graph["vertex_tables"] == len(_property_graph_vertices())
assert property_graph["edge_tables"] == len(_property_graph_edges())
assert "must not assume it exists" in property_graph["consumer_rule"]

assert __version__ == version
