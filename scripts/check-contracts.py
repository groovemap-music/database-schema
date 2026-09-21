"""Validate the persistence compatibility contract without touching a database."""

import json
import tomllib
from pathlib import Path

from groovemap_schema import __version__
from groovemap_schema.postgres import (
    _BOOTSTRAP_FILL_ORDER,
    _GRAPH_STATEMENTS,
    _PATH_RELATIONS,
    _PATH_RELATIONSHIP_TYPES,
    _VERTEX_KIND_NAMES,
    EXPLORE_DEFAULT_HOPS,
    EXPLORE_DEFAULT_ROW_LIMIT,
    EXPLORE_MAX_HOPS,
    EXPLORE_MIN_HOPS,
    PATH_DEFAULT_DEPTH,
    PATH_MAX_DEPTH,
    PATH_MIN_DEPTH,
    PATH_SEEN_RELATION,
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

# Every declared relation is exposed by the property graph except the nine
# storage-only row/counter tables behind projected labels, plus the loader-written
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

# The shortest-path function. It is the one declared object that READS the
# traversal surface rather than writing it, so what the contract has to record
# is not an owner but what it walks, what it returns, and the state it needs —
# and every one of those is read back out of the rendered function rather than
# restated, so a relation dropped from the surface or a bound moved in the clamp
# fails this check instead of leaving the contract describing a function that
# moved underneath it.
path_function = graph["path_function"]
path_body = dict(_GRAPH_STATEMENTS)[f"{PROPERTY_GRAPH_SCHEMA}.find_shortest_path function"]
assert path_function["kind"] == "additive"
assert path_function["availability"] == "unconditional"
assert path_function["function"] == f"{PROPERTY_GRAPH_SCHEMA}.find_shortest_path"
assert path_function["function"] in graph["functions"]
assert path_function["declared_as"].startswith(f"{path_function['function']}(")
# The clamp `catalog-api` applies to the same argument, with the same bounds.
assert path_function["clamped_to"] == [PATH_MIN_DEPTH, PATH_MAX_DEPTH]
assert path_function["default_max_depth"] == PATH_DEFAULT_DEPTH
assert f"cap := greatest({PATH_MIN_DEPTH}, least(coalesce(max_depth, {PATH_DEFAULT_DEPTH}), {PATH_MAX_DEPTH}));" in path_body
assert f"max_depth int DEFAULT {PATH_DEFAULT_DEPTH}" in path_body
assert "RETURNS TABLE (found boolean, depth int, nodes text[], rels text[])" in path_body
# The same ten relations the degree sums, and both directions of each, because
# the surface is undirected and the degree that orders the walk is a sum over
# exactly what the walk traverses.
assert path_function["traverses"] == degree["sources"]
assert path_function["undirected"] is True
for source in path_function["traverses"]:
    assert path_body.count(f"FROM {source} AS edge\n") == 4, source
# It reads the degree to order its expansion, and writes nothing at all.
assert path_function["reads"] == [degree["relation"]]
assert path_function["writes"] == "nothing"
for relation in declared_shapes:
    for verb in ("INSERT INTO", "UPDATE", "DELETE FROM"):
        assert f"{verb} {PROPERTY_GRAPH_SCHEMA}.{relation}" not in path_body, f"{verb} {relation}"
# One request-scoped relation, and the three properties the spike is emphatic
# about: session-scoped, emptied by the commit, and no second frontier relation.
seen_set = path_function["seen_set"]
assert seen_set["relation"] == f"pg_temp.{PATH_SEEN_RELATION}"
assert seen_set["shape"] == "TEMPORARY table, ON COMMIT DELETE ROWS"
assert f"CREATE TEMPORARY TABLE {PATH_SEEN_RELATION} (" in path_body
assert ") ON COMMIT DELETE ROWS;" in path_body
assert path_body.count("CREATE TEMPORARY TABLE") == 1
assert f"PRIMARY KEY ({', '.join(seen_set['key'])})" in path_body
assert f"CREATE INDEX {PATH_SEEN_RELATION}_level ON pg_temp.{PATH_SEEN_RELATION} {seen_set['secondary_index']};" in path_body
# Every one of the six relationship types `rels[]` can report is named, and each
# is a type a path query traverses rather than a provenance.
assert set(_PATH_RELATIONSHIP_TYPES) == {relation.removeprefix(f"{PROPERTY_GRAPH_SCHEMA}.") for relation in path_function["traverses"]}
for relationship in set(_PATH_RELATIONSHIP_TYPES.values()):
    assert relationship in path_function["rels"], relationship
    assert f"'{relationship}'::text AS rel" in path_body, relationship
assert "edge.source" not in path_body

# The bounded Explore function walks the same surface with the same temporary
# relation, but it is a one-sided breadth-first walk and therefore does not read
# vertex_degree. Its row limit is a safety boundary: allowing NULL would restore
# the full three-hop traversal the spike measured at 21.4 seconds.
explore_function = graph["explore_function"]
explore_body = dict(_GRAPH_STATEMENTS)[f"{PROPERTY_GRAPH_SCHEMA}.explore_traversal function"]
assert explore_function["kind"] == "additive"
assert explore_function["availability"] == "unconditional"
assert explore_function["function"] == f"{PROPERTY_GRAPH_SCHEMA}.explore_traversal"
assert explore_function["function"] in graph["functions"]
assert explore_function["declared_as"].startswith(f"{explore_function['function']}(")
assert explore_function["clamped_to"] == [EXPLORE_MIN_HOPS, EXPLORE_MAX_HOPS]
assert explore_function["default_hops"] == EXPLORE_DEFAULT_HOPS
assert explore_function["default_row_limit"] == EXPLORE_DEFAULT_ROW_LIMIT
assert f"hops      int DEFAULT {EXPLORE_DEFAULT_HOPS}" in explore_body
assert f"row_limit int DEFAULT {EXPLORE_DEFAULT_ROW_LIMIT}" in explore_body
assert "RETURNS TABLE (id text, name text, type text, path_names text[], rel_types text[], dist int)" in explore_body
assert f"cap := greatest({EXPLORE_MIN_HOPS}, least(coalesce(hops, {EXPLORE_DEFAULT_HOPS}), {EXPLORE_MAX_HOPS}));" in explore_body
assert explore_function["traverses"] == degree["sources"]
assert explore_function["undirected"] is True
for source in explore_function["traverses"]:
    assert explore_body.count(f"FROM {source} AS edge\n") == 2, source
assert explore_function["seen_set"] == seen_set["relation"]
assert f"CREATE TEMPORARY TABLE {PATH_SEEN_RELATION} (" in explore_body
assert explore_body.count("CREATE TEMPORARY TABLE") == 1
assert explore_function["writes"] == "nothing"
for relation in declared_shapes:
    for verb in ("INSERT INTO", "UPDATE", "DELETE FROM"):
        assert f"{verb} {PROPERTY_GRAPH_SCHEMA}.{relation}" not in explore_body, f"{verb} {relation}"
assert explore_function["row_limit"] == {"mandatory": True, "minimum": 0, "null_rejected": True}
assert "IF row_limit IS NULL THEN" in explore_body
assert "IF row_limit < 0 THEN" in explore_body
assert "EXIT walk WHEN qualifying >= row_limit;" in explore_body
assert "LIMIT row_limit" in explore_body
assert explore_function["qualifying_vertex_kinds"] == ["artist", "label", "genre", "style"]
assert "WITH RECURSIVE back AS" in explore_body
assert "graph.vertex_degree" not in explore_body

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
