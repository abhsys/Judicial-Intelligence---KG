from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/graph", tags=["graph"])


class BuildGraphRequest(BaseModel):
    raw_label: str | None = None


@router.post("/build")
def build_graph(
    request: Request, payload: BuildGraphRequest | None = None
) -> dict[str, int | str]:
    extractor = request.app.state.extractor
    return extractor.build_case_graph(raw_label=payload.raw_label if payload else None)


@router.get("/summary")
def graph_summary(request: Request) -> dict[str, int]:
    extractor = request.app.state.extractor
    return extractor.graph_summary()


@router.get("/labels")
def available_source_labels(request: Request, limit: int = 100) -> dict[str, Any]:
    graph_service = request.app.state.graph_service
    query = """
    CALL db.labels() YIELD label
    WHERE NOT label IN ["Case", "Court", "Party", "Order"]
    RETURN label
    ORDER BY label
    LIMIT $limit
    """
    rows = graph_service.run_query(query, {"limit": limit})
    return {"count": len(rows), "data": rows}


@router.get("/network")
def graph_network(
    request: Request,
    case_key: str | None = Query(default=None),
    limit_cases: int = Query(default=40, ge=1, le=200),
) -> dict[str, Any]:
    graph_service = request.app.state.graph_service

    if case_key and case_key.strip():
        query = """
        MATCH (c:Case {case_key: $case_key})
        OPTIONAL MATCH (c)-[r]->(n)
        WITH collect(DISTINCT c) + collect(DISTINCT n) AS raw_nodes,
             collect(DISTINCT r) AS raw_edges
        RETURN
          [node IN raw_nodes WHERE node IS NOT NULL | {
            id: elementId(node),
            labels: labels(node),
            properties: properties(node)
          }] AS nodes,
          [rel IN raw_edges WHERE rel IS NOT NULL | {
            id: elementId(rel),
            type: type(rel),
            source: elementId(startNode(rel)),
            target: elementId(endNode(rel))
          }] AS edges
        """
        rows = graph_service.run_query(query, {"case_key": case_key.strip()})
    else:
        query = """
        MATCH (c:Case)
        WITH c
        ORDER BY c.order_date DESC, c.case_key ASC
        LIMIT $limit_cases
        OPTIONAL MATCH (c)-[r]->(n)
        WITH collect(DISTINCT c) + collect(DISTINCT n) AS raw_nodes,
             collect(DISTINCT r) AS raw_edges
        RETURN
          [node IN raw_nodes WHERE node IS NOT NULL | {
            id: elementId(node),
            labels: labels(node),
            properties: properties(node)
          }] AS nodes,
          [rel IN raw_edges WHERE rel IS NOT NULL | {
            id: elementId(rel),
            type: type(rel),
            source: elementId(startNode(rel)),
            target: elementId(endNode(rel))
          }] AS edges
        """
        rows = graph_service.run_query(query, {"limit_cases": limit_cases})

    if not rows:
        return {"nodes": [], "edges": [], "node_count": 0, "edge_count": 0}

    graph = rows[0]
    nodes = graph.get("nodes", []) or []
    edges = graph.get("edges", []) or []
    return {
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
    }
