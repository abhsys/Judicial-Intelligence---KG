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
    fallback_case_query = """
    MATCH (c:Case {case_key: $case_key})
    OPTIONAL MATCH (c)-[r]->(n)
    WITH collect(DISTINCT c) + collect(DISTINCT n) AS raw_nodes,
         collect(DISTINCT r) AS raw_edges
    RETURN
      [node IN raw_nodes WHERE node IS NOT NULL | {
        id: elementId(node),
        labels: labels(node),
        properties: properties(node),
        score: CASE WHEN "Case" IN labels(node) THEN 100 ELSE null END
      }] AS nodes,
      [rel IN raw_edges WHERE rel IS NOT NULL | {
        id: elementId(rel),
        type: type(rel),
        source: elementId(startNode(rel)),
        target: elementId(endNode(rel)),
        weight: 1.0
      }] AS edges
    """

    if case_key and case_key.strip():
        query = """
        MATCH (c:Case {case_key: $case_key})
        OPTIONAL MATCH (c)-[:FILED_IN|HAS_PETITIONER|HAS_RESPONDENT]->(shared)<-[:FILED_IN|HAS_PETITIONER|HAS_RESPONDENT]-(related:Case)
        WHERE related <> c
        WITH c,
             related,
             count(DISTINCT shared) AS shared_strength
        ORDER BY shared_strength DESC, related.order_date DESC, related.case_key ASC
        WITH c,
             [item IN collect({
               case_id: elementId(related),
               strength: shared_strength
             }) WHERE item.case_id IS NOT NULL][..$limit_cases] AS related_meta
        WITH c,
             related_meta,
             [item IN related_meta | item.case_id] AS related_case_ids
        MATCH (case_node:Case)
        WHERE elementId(case_node) = elementId(c) OR elementId(case_node) IN related_case_ids
        OPTIONAL MATCH (case_node)-[r]->(n)
        WITH c,
             related_meta,
             collect(DISTINCT case_node) + collect(DISTINCT n) AS raw_nodes,
             collect(DISTINCT r) AS raw_edges
        WITH
          raw_nodes,
          raw_edges,
          elementId(c) AS selected_case_id,
          reduce(meta_map = {}, item IN related_meta | meta_map + {[item.case_id]: item.strength}) AS related_score_map
        RETURN
          [node IN raw_nodes WHERE node IS NOT NULL | {
            id: elementId(node),
            labels: labels(node),
            properties: properties(node),
            score: CASE
              WHEN "Case" IN labels(node) AND elementId(node) = selected_case_id THEN 100
              WHEN "Case" IN labels(node) THEN coalesce(related_score_map[elementId(node)], 1)
              ELSE null
            END
          }] AS nodes,
          [rel IN raw_edges WHERE rel IS NOT NULL | {
            id: elementId(rel),
            type: type(rel),
            source: elementId(startNode(rel)),
            target: elementId(endNode(rel)),
            weight: CASE
              WHEN type(rel) = "HAS_ORDER" THEN 0.9
              ELSE 1.3
            END
          }] AS edges
        """
        rows = graph_service.run_query(
            query, {"case_key": case_key.strip(), "limit_cases": limit_cases}
        )
        if rows:
            row = rows[0]
            nodes = row.get("nodes", []) or []
            case_nodes = [
                node for node in nodes if "Case" in (node.get("labels", []) or [])
            ]
            if len(case_nodes) <= 1:
                rows = graph_service.run_query(
                    fallback_case_query, {"case_key": case_key.strip()}
                )
        else:
            rows = graph_service.run_query(
                fallback_case_query, {"case_key": case_key.strip()}
            )
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
