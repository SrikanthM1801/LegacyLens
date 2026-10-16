"""
GraphStore: the one interface the rest of the app uses for the knowledge graph.

Today there is a single NetworkX implementation persisted to JSON; a Cosmos DB
or graph-database version can replace it without touching callers. Node ids live
in one flat id space (see models.py): Entity.id or Artifact.id. People are
`Entity(type=person)` whose id equals `Person.id`.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Protocol

from app.models import Edge, Entity, EntityType, GraphStats, RelationType


class GraphStore(Protocol):
    # --- nodes ---------------------------------------------------------------
    def add_entity(self, entity: Entity) -> Entity:
        """Insert, or merge into the existing node with the same id (union of
        aliases / artifact_ids / attributes; first non-empty description wins)."""
        ...

    def get_entity(self, entity_id: str) -> Optional[Entity]: ...

    def find_entity(self, name: str, type: Optional[EntityType] = None) -> Optional[Entity]:
        """Case-insensitive lookup by name or alias."""
        ...

    def entities(self, type: Optional[EntityType] = None) -> list[Entity]: ...

    def add_artifact_node(
        self, artifact_id: str, type: str, title: str, timestamp: Optional[datetime] = None,
        url_or_path: Optional[str] = None,
    ) -> None:
        """Register an artifact as a (lightweight) node so edges can point at it."""
        ...

    def has_node(self, node_id: str) -> bool: ...

    def artifact_node(self, artifact_id: str) -> Optional[dict[str, Any]]:
        """{'type', 'title', 'timestamp', 'url_or_path'} of an artifact node, or None."""
        ...

    def artifact_timestamps(self) -> dict[str, datetime]:
        """{artifact_id: timestamp} for every artifact node that has one."""
        ...

    # --- edges ---------------------------------------------------------------
    def add_edge(self, edge: Edge) -> Edge:
        """Insert, or accumulate into an existing (source, target, relation) edge:
        weights add, evidence ids are unioned. Both endpoints must already exist."""
        ...

    def edges(
        self,
        source: Optional[str] = None,
        target: Optional[str] = None,
        relation: Optional[RelationType] = None,
    ) -> list[Edge]: ...

    def neighbors(self, node_id: str, relation: Optional[RelationType] = None, direction: str = "both") -> list[str]:
        """Adjacent node ids. direction: 'out' | 'in' | 'both'."""
        ...

    # --- whole graph ---------------------------------------------------------
    def stats(self) -> GraphStats: ...

    def clear(self) -> None: ...

    def to_json(self) -> dict[str, Any]:
        """{'nodes': [...], 'edges': [...]} for GET /graph, sorted for stable output."""
        ...

    def save(self, path: Optional[Path] = None) -> None: ...

    def load(self, path: Optional[Path] = None) -> bool:
        """Load from disk. Returns False (graph left empty) if the file is missing."""
        ...
