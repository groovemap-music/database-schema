"""Validate the persistence compatibility contract without touching a database."""

import json
import tomllib
from pathlib import Path

from groovemap_schema import __version__
from groovemap_schema.postgres import (
    _BOOTSTRAP_FILL_ORDER,
    _GRAPH_STATEMENTS,
    _PATH_RELATIONS,
    _VERTEX_KIND_NAMES,
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

# Four labels carry counters Neo4j carries as node properties of the same node,
# so each binds a view joining its storage relation to its counter relation
# rather than the storage relation itself. The counters recorded here are the
# parity claim, and every one is checked against the relation that publishes it.
counters = graph["counter_properties"]
assert counters["kind"] == "additive"
assert counters["availability"] == "unconditional"
vertices_by_label = {vertex.view: vertex for vertex in _property_graph_vertices()}
for label, recorded in counters["labels"].items():
    vertex = vertices_by_label[label]
    element = f"{PROPERTY_GRAPH_SCHEMA}.{vertex.element}"
    assert recorded["element_table"] == element, label
    assert vertex.element != vertex.view, label
    body = view_statements[element]
    assert f"{PROPERTY_GRAPH_SCHEMA}.{recorded['counters'].removeprefix(f'{PROPERTY_GRAPH_SCHEMA}.')}" in table_statements, label
    assert f"LEFT JOIN {recorded['counters']} AS" in body, label
    for column in recorded["properties"]:
        assert f"AS {column}" in body, f"{label}.{column}"

# `graph.release_degree` is the one counter that stays a label of its own, and
# the counter relations behind the other four bind no label at all.
assert "release_degree" in vertices_by_label
assert not set(vertices_by_label) & {recorded["counters"].removeprefix("graph.") for recorded in counters["labels"].values()}

# The property graph binds every relation except the nine that hold rows or
# counters for a label that binds a projection over them, plus the loader-written
# half of release degree.
element_relations = {element.element for element in (*_property_graph_vertices(), *_property_graph_edges())}
storage_only = set(declared_shapes) - element_relations
assert storage_only == {
    "artist",
    "artist_degree",
    "artist_member_of",
    "genre",
    "genre_stats",
    "label",
    "label_stats",
    "release_degree_base",
    "style",
    "style_stats",
    "vertex_degree",
}, storage_only

# The cross-provenance MEMBER_OF union. It is a table the path functions read
# rather than a relationship type Neo4j carries, so it binds no label, and it is
# the one relation with a refresh owner distinct from the loader that writes the
# rows it reads: nothing writes it a row at a time, so the contract has to name
# who rebuilds it and on what.
union = graph["member_of_union"]
assert union["kind"] == "additive"
assert union["availability"] == "unconditional"
assert union["relation"] == f"{PROPERTY_GRAPH_SCHEMA}.artist_member_of"
assert union["function"] == f"{PROPERTY_GRAPH_SCHEMA}.refresh_artist_member_of"
assert union["function"] in graph["functions"]
assert union["refresh_owner"] in OWNERS
assert union["refresh_owner"] == "discogs-sql-loader"
assert union["refresh_latch"] == "extraction_complete"
assert union["relation"].removeprefix(f"{PROPERTY_GRAPH_SCHEMA}.") in storage_only
assert declared_shapes[union["relation"].removeprefix(f"{PROPERTY_GRAPH_SCHEMA}.")] == "table"
assert relations["artist_member_of"]["owner"] == union["refresh_owner"]
# Both provenances are named, and both are read out of the relation body rather
# than restated: a source the union stopped reading fails this check.
union_body = dict(_GRAPH_STATEMENTS)[f"{PROPERTY_GRAPH_SCHEMA}.refresh_artist_member_of function"]
for source in union["sources"]:
    assert f"FROM {source} AS" in union_body or f"JOIN {source} AS" in union_body, source
assert union["key"] == ["member_artist_id", "group_artist_id", "source"]
assert f"PRIMARY KEY ({', '.join(union['key'])})" in table_statements[union["relation"]]
assert f"ON {union['relation']} {union['reverse_index']}" in "\n".join(statement for name, statement in _GRAPH_STATEMENTS if name.endswith(" index"))

# The per-vertex degree that orders frontier expansion. It is derived from ten
# relations rather than written a row at a time, so — like the MEMBER_OF union —
# the contract has to name who rebuilds it and on what latch. Every source and
# every vertex kind is read back out of the relation list rather than restated,
# so a path relation added to or dropped from the traversal surface fails this
# check instead of leaving the contract describing a surface that moved.
degree = graph["vertex_degree"]
assert degree["kind"] == "additive"
assert degree["availability"] == "unconditional"
assert degree["relation"] == f"{PROPERTY_GRAPH_SCHEMA}.vertex_degree"
assert degree["function"] == f"{PROPERTY_GRAPH_SCHEMA}.refresh_vertex_degree"
assert degree["function"] in graph["functions"]
assert degree["refresh_owner"] in OWNERS
assert degree["refresh_owner"] == "discogs-sql-loader"
assert degree["refresh_latch"] == "extraction_complete"
assert degree["refresh_latch"] == union["refresh_latch"]
assert degree["relation"].removeprefix(f"{PROPERTY_GRAPH_SCHEMA}.") in storage_only
assert declared_shapes[degree["relation"].removeprefix(f"{PROPERTY_GRAPH_SCHEMA}.")] == "table"
assert relations["vertex_degree"]["owner"] == degree["refresh_owner"]
assert degree["key"] == ["kind", "key"]
assert f"PRIMARY KEY ({', '.join(degree['key'])})" in table_statements[degree["relation"]]
# The ten relations it sums, both directions of each, and nothing else.
assert degree["sources"] == [f"{PROPERTY_GRAPH_SCHEMA}.{relation}" for relation, _sk, _sc, _tk, _tc in _PATH_RELATIONS]
assert len(degree["sources"]) == 10
# The union is one of them, which is why the refresh runs after the union's.
assert f"{PROPERTY_GRAPH_SCHEMA}.artist_member_of" in degree["sources"]
degree_body = dict(_GRAPH_STATEMENTS)[f"{PROPERTY_GRAPH_SCHEMA}.refresh_vertex_degree function"]
for source in degree["sources"]:
    assert degree_body.count(f"FROM {source}\n") == 2, source
assert degree["vertex_kinds"] == _VERTEX_KIND_NAMES
assert {kind for _r, source, _sc, target, _tc in _PATH_RELATIONS for kind in (source, target)} == set(degree["vertex_kinds"])
# The one judgement call the relation makes, recorded rather than inferred: the
# union keys on `source`, so a membership both provenances assert is two rows
# and an expansion of that vertex really does scan both.
assert degree["counts_a_dual_provenance_membership"] == "twice"

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

# The bootstrap fill is recorded as the one declared object that writes a graph
# row, and as writing none of them authoritatively. What it fills is read back
# from the fill order rather than restated, so a relation that becomes a table
# without being added to the fill fails this check rather than shipping as a
# table the bootstrap silently leaves empty.
bootstrap = graph["bootstrap"]
assert bootstrap["kind"] == "additive"
assert bootstrap["availability"] == "unconditional"
assert bootstrap["authority"] == "none"
assert bootstrap["function"] == "graph.bootstrap_fill"
assert bootstrap["function"] in graph["functions"]
assert bootstrap["fills"] == sorted(_BOOTSTRAP_FILL_ORDER)
assert set(bootstrap["fills"]) == {relation for relation, shape in declared_shapes.items() if shape == "table"}
assert "NOT authoritative" in bootstrap["note"]

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
