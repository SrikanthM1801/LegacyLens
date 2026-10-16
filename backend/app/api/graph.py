"""GET /graph: nodes and edges for visualization."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.graph import get_graph
from app.models import GraphResponse

router = APIRouter(tags=["graph"])


@router.get("/graph", response_model=GraphResponse)
def graph(include_artifacts: bool = Query(False, description="Include artifact nodes and their edges")) -> GraphResponse:
    g = get_graph()
    data = g.to_json()
    nodes, edges = data["nodes"], data["edges"]
    if not include_artifacts:
        keep = {n["id"] for n in nodes if n["kind"] == "entity"}
        nodes = [n for n in nodes if n["id"] in keep]
        edges = [e for e in edges if e["source"] in keep and e["target"] in keep]
    return GraphResponse(nodes=nodes, edges=edges, stats=g.stats())
