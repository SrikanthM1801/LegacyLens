from functools import lru_cache

from app.graph.networkx_store import NetworkXGraphStore
from app.graph.store import GraphStore


@lru_cache
def get_graph() -> GraphStore:
    """Process-wide graph, loaded from disk if a saved one exists."""
    g = NetworkXGraphStore()
    g.load()
    return g


__all__ = ["GraphStore", "NetworkXGraphStore", "get_graph"]
