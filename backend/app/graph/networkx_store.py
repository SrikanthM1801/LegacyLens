"""NetworkX-backed GraphStore, persisted as JSON (data/graph.json)."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import networkx as nx

from app.config import get_settings
from app.models import Edge, Entity, EntityType, GraphStats, RelationType

_ENTITY, _ARTIFACT = "entity", "artifact"


def _enum_value(v: Any) -> Any:
    return getattr(v, "value", v)


def _dedupe(*lists: list[str]) -> list[str]:
    """Order-preserving union."""
    seen: dict[str, None] = {}
    for lst in lists:
        for item in lst:
            seen.setdefault(item, None)
    return list(seen)


class NetworkXGraphStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else get_settings().graph_path
        self._g = nx.MultiDiGraph()  # one edge per (source, target, relation); key = relation

    # --- nodes ---------------------------------------------------------------
    def add_entity(self, entity: Entity) -> Entity:
        existing = self.get_entity(entity.id)
        if existing:
            entity = existing.model_copy(update={
                "aliases": _dedupe(existing.aliases, entity.aliases),
                "artifact_ids": _dedupe(existing.artifact_ids, entity.artifact_ids),
                "attributes": {**entity.attributes, **existing.attributes},
                "description": existing.description or entity.description,
            })
        self._g.add_node(entity.id, kind=_ENTITY, data=entity.model_dump(mode="json"))
        return entity

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        node = self._g.nodes.get(entity_id)
        if not node or node["kind"] != _ENTITY:
            return None
        return Entity.model_validate(node["data"])

    def find_entity(self, name: str, type: Optional[EntityType] = None) -> Optional[Entity]:
        wanted = name.strip().lower()
        for e in self.entities(type):
            if wanted == e.name.lower() or wanted in (a.lower() for a in e.aliases):
                return e
        return None

    def entities(self, type: Optional[EntityType] = None) -> list[Entity]:
        want = _enum_value(type)
        out = [
            Entity.model_validate(d["data"])
            for _, d in self._g.nodes(data=True)
            if d["kind"] == _ENTITY and (want is None or d["data"]["type"] == want)
        ]
        return sorted(out, key=lambda e: e.id)

    def add_artifact_node(self, artifact_id: str, type: str, title: str, timestamp: Optional[datetime] = None) -> None:
        self._g.add_node(
            artifact_id,
            kind=_ARTIFACT,
            data={"type": _enum_value(type), "title": title, "timestamp": timestamp.isoformat() if timestamp else None},
        )

    def has_node(self, node_id: str) -> bool:
        return node_id in self._g

    # --- edges ---------------------------------------------------------------
    def add_edge(self, edge: Edge) -> Edge:
        for end in (edge.source, edge.target):
            if end not in self._g:
                raise ValueError(f"add_edge: unknown node '{end}' (add it before linking)")
        rel = _enum_value(edge.relation)
        if self._g.has_edge(edge.source, edge.target, key=rel):
            old = Edge.model_validate(self._g.edges[edge.source, edge.target, rel]["data"])
            edge = old.model_copy(update={
                "weight": old.weight + edge.weight,
                "evidence_artifact_ids": _dedupe(old.evidence_artifact_ids, edge.evidence_artifact_ids),
                "attributes": {**edge.attributes, **old.attributes},
            })
        self._g.add_edge(edge.source, edge.target, key=rel, data=edge.model_dump(mode="json"))
        return edge

    def edges(self, source=None, target=None, relation=None) -> list[Edge]:
        rel = _enum_value(relation)
        out = [
            Edge.model_validate(d["data"])
            for u, v, k, d in self._g.edges(keys=True, data=True)
            if (source is None or u == source) and (target is None or v == target) and (rel is None or k == rel)
        ]
        return sorted(out, key=lambda e: (e.source, e.target, e.relation))

    def neighbors(self, node_id: str, relation=None, direction: str = "both") -> list[str]:
        if direction not in ("out", "in", "both"):
            raise ValueError("direction must be 'out', 'in' or 'both'")
        if node_id not in self._g:
            return []
        rel = _enum_value(relation)
        found: set[str] = set()
        if direction in ("out", "both"):
            found |= {v for _, v, k in self._g.out_edges(node_id, keys=True) if rel is None or k == rel}
        if direction in ("in", "both"):
            found |= {u for u, _, k in self._g.in_edges(node_id, keys=True) if rel is None or k == rel}
        return sorted(found)

    # --- whole graph ---------------------------------------------------------
    def stats(self) -> GraphStats:
        by_type: dict[str, int] = {}
        for _, d in self._g.nodes(data=True):
            t = d["data"]["type"]
            key = t if d["kind"] == _ENTITY else f"artifact:{t}"
            by_type[key] = by_type.get(key, 0) + 1
        by_rel: dict[str, int] = {}
        for _, _, k in self._g.edges(keys=True):
            by_rel[k] = by_rel.get(k, 0) + 1
        return GraphStats(
            node_count=self._g.number_of_nodes(),
            edge_count=self._g.number_of_edges(),
            nodes_by_type=dict(sorted(by_type.items())),
            edges_by_relation=dict(sorted(by_rel.items())),
        )

    def clear(self) -> None:
        self._g.clear()

    def to_json(self) -> dict[str, Any]:
        nodes = []
        for nid, d in self._g.nodes(data=True):
            # Copy: never hand out (or mutate) the dicts the graph stores internally.
            if d["kind"] == _ENTITY:
                nodes.append({**d["data"], "kind": _ENTITY})
            else:
                nodes.append({"id": nid, "kind": _ARTIFACT, **d["data"]})
        edges = [e.model_dump(mode="json") for e in self.edges()]
        return {"nodes": sorted(nodes, key=lambda n: n["id"]), "edges": edges}

    def save(self, path: Optional[Path] = None) -> None:
        target = Path(path) if path else self._path
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, target)  # atomic: a crash never leaves a half-written graph

    def load(self, path: Optional[Path] = None) -> bool:
        source = Path(path) if path else self._path
        self._g.clear()
        if not source.exists():
            return False
        data = json.loads(source.read_text(encoding="utf-8"))
        for n in data["nodes"]:
            if n.get("kind") == _ARTIFACT:
                self._g.add_node(n["id"], kind=_ARTIFACT, data={k: n[k] for k in ("type", "title", "timestamp")})
            else:
                self._g.add_node(n["id"], kind=_ENTITY, data={k: v for k, v in n.items() if k != "kind"})
        for e in data["edges"]:
            edge = Edge.model_validate(e)
            self._g.add_edge(edge.source, edge.target, key=_enum_value(edge.relation), data=edge.model_dump(mode="json"))
        return True
