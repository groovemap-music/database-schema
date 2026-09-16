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
# below is read back from the statement list rather than restated, so a view
# added or removed in postgres.py fails this check instead of silently making the
# contract a stale description of the schema consumers read.
graph = contract["graph_schema"]
statement_names = [name for name, _ in _GRAPH_STATEMENTS]
view_statements = {name.removesuffix(" view"): body for name, body in _GRAPH_STATEMENTS if name.endswith(" view")}

assert graph["schema"] == PROPERTY_GRAPH_SCHEMA
assert graph["kind"] == "additive"
assert graph["availability"] == "unconditional"
assert graph["introduced_in_contract_version"] == contract["version"]
assert graph["views"] == len(view_statements)
assert graph["vertex_views"] == len(_property_graph_vertices())
assert graph["edge_views"] == len(_property_graph_edges())
assert graph["vertex_views"] + graph["edge_views"] == graph["views"]
assert graph["functions"] == sorted(name.removesuffix(" function") for name in statement_names if name.endswith(" function"))

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

# The four appended `<entity>_key` columns are exactly the vertex keys that are a
# restatement rather than a published id, and they land on every engine.
appended = graph["appended_key_columns"]
assert appended["kind"] == "additive"
assert appended["availability"] == "unconditional"
assert appended["type"] == "text"
assert sorted(appended["columns"]) == sorted(
    f"{PROPERTY_GRAPH_SCHEMA}.{vertex.view}.{column}" for vertex in _property_graph_vertices() for column in vertex.key if column.endswith("_key")
)
for qualified in appended["columns"]:
    view, _, column = qualified.rpartition(".")
    assert column in view_statements[view], qualified

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
