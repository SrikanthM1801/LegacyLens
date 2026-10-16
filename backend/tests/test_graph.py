import json
from datetime import datetime, timezone

import pytest

from app.graph import NetworkXGraphStore
from app.models import Edge, Entity, EntityType, RelationType


@pytest.fixture
def g(tmp_path):
    g = NetworkXGraphStore(tmp_path / "graph.json")
    g.add_entity(Entity(id="ravi", type=EntityType.PERSON, name="Ravi Kumar", aliases=["Ravi"]))
    g.add_entity(Entity(id="sys_pg", type=EntityType.SYSTEM, name="Payment Gateway", aliases=["PG", "gateway"]))
    g.add_entity(Entity(id="inc_142", type=EntityType.INCIDENT, name="INC-142"))
    g.add_artifact_node("art_1", "ticket", "INC-142 timeouts", datetime(2025, 11, 3, tzinfo=timezone.utc))
    return g


def test_entity_merge_unions_fields_and_keeps_first_description(g):
    g.add_entity(Entity(id="sys_pg", type=EntityType.SYSTEM, name="Payment Gateway", aliases=["pg-adapter", "PG"],
                        description="Talks to the acquirer", artifact_ids=["art_1"], attributes={"criticality": "high"}))
    g.add_entity(Entity(id="sys_pg", type=EntityType.SYSTEM, name="Payment Gateway", description="ignored"))
    e = g.get_entity("sys_pg")
    assert e.aliases == ["PG", "gateway", "pg-adapter"] and e.artifact_ids == ["art_1"]
    assert e.description == "Talks to the acquirer" and e.attributes == {"criticality": "high"}


def test_find_entity_by_name_alias_and_type(g):
    assert g.find_entity("pg").id == "sys_pg"
    assert g.find_entity("  RAVI ").id == "ravi"
    assert g.find_entity("pg", EntityType.PERSON) is None
    assert g.find_entity("nobody") is None
    assert [e.id for e in g.entities(EntityType.SYSTEM)] == ["sys_pg"]


def test_get_entity_ignores_artifact_nodes(g):
    assert g.get_entity("art_1") is None and g.has_node("art_1")


def test_edges_accumulate_weight_and_evidence(g):
    g.add_edge(Edge(source="ravi", target="inc_142", relation=RelationType.RESOLVED, weight=1, evidence_artifact_ids=["a1"]))
    e = g.add_edge(Edge(source="ravi", target="inc_142", relation=RelationType.RESOLVED, weight=2, evidence_artifact_ids=["a1", "a2"]))
    assert e.weight == 3 and e.evidence_artifact_ids == ["a1", "a2"]
    assert len(g.edges()) == 1
    # a different relation between the same nodes is a separate edge
    g.add_edge(Edge(source="ravi", target="inc_142", relation=RelationType.PARTICIPATED_IN))
    assert len(g.edges(source="ravi", target="inc_142")) == 2
    assert len(g.edges(relation=RelationType.RESOLVED)) == 1


def test_add_edge_requires_existing_nodes(g):
    with pytest.raises(ValueError, match="unknown node"):
        g.add_edge(Edge(source="ravi", target="ghost", relation=RelationType.OWNS))


def test_neighbors_directions(g):
    g.add_edge(Edge(source="ravi", target="inc_142", relation=RelationType.RESOLVED))
    g.add_edge(Edge(source="inc_142", target="sys_pg", relation=RelationType.AFFECTS))
    assert g.neighbors("inc_142") == ["ravi", "sys_pg"]
    assert g.neighbors("inc_142", direction="out") == ["sys_pg"]
    assert g.neighbors("inc_142", direction="in") == ["ravi"]
    assert g.neighbors("inc_142", relation=RelationType.AFFECTS) == ["sys_pg"]
    assert g.neighbors("missing") == []
    with pytest.raises(ValueError):
        g.neighbors("ravi", direction="sideways")


def test_stats(g):
    g.add_edge(Edge(source="ravi", target="inc_142", relation=RelationType.RESOLVED))
    s = g.stats()
    assert s.node_count == 4 and s.edge_count == 1
    assert s.nodes_by_type == {"artifact:ticket": 1, "incident": 1, "person": 1, "system": 1}
    assert s.edges_by_relation == {"RESOLVED": 1}


def test_save_load_round_trip_is_lossless_and_stable(g, tmp_path):
    g.add_edge(Edge(source="ravi", target="inc_142", relation=RelationType.RESOLVED, weight=5, evidence_artifact_ids=["art_1"]))
    g.add_edge(Edge(source="art_1", target="inc_142", relation=RelationType.MENTIONS))
    g.save()
    first = (tmp_path / "graph.json").read_text()
    h = NetworkXGraphStore(tmp_path / "graph.json")
    assert h.load() is True
    assert h.to_json() == g.to_json() and h.stats() == g.stats()
    assert h.find_entity("PG").id == "sys_pg" and h.has_node("art_1")
    h.save()
    assert (tmp_path / "graph.json").read_text() == first
    assert not list(tmp_path.glob("*.tmp"))
    kinds = {n["id"]: n["kind"] for n in json.loads(first)["nodes"]}
    assert kinds["art_1"] == "artifact" and kinds["ravi"] == "entity"


def test_load_missing_file_and_clear(g, tmp_path):
    h = NetworkXGraphStore(tmp_path / "nope.json")
    assert h.load() is False and h.stats().node_count == 0
    g.clear()
    assert g.stats().node_count == 0
