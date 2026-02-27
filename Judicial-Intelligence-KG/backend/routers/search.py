from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

try:
    from ..services.live_metrics_service import LiveMetricsService
except ImportError:
    from services.live_metrics_service import LiveMetricsService

router = APIRouter(prefix="/api", tags=["search"])
live_metrics_service = LiveMetricsService()


def _normalize_value(value: str | None) -> str:
    return (value or "").strip()


def _build_filter_parameters(
    query: str | None, court: str | None, party: str | None
) -> dict[str, str]:
    return {
        "query": _normalize_value(query),
        "court": _normalize_value(court),
        "party": _normalize_value(party),
    }


FILTERS_CLAUSE = """
WHERE
    ($query = ""
        OR toLower(c.case_key) CONTAINS toLower($query)
        OR any(name IN petitioners WHERE toLower(name) CONTAINS toLower($query))
        OR any(name IN respondents WHERE toLower(name) CONTAINS toLower($query))
        OR any(name IN courts WHERE toLower(name) CONTAINS toLower($query)))
    AND ($court = "" OR any(name IN courts WHERE toLower(name) = toLower($court)))
    AND ($party = ""
        OR any(name IN petitioners WHERE toLower(name) CONTAINS toLower($party))
        OR any(name IN respondents WHERE toLower(name) CONTAINS toLower($party)))
"""


@router.get("/dashboard/summary")
def dashboard_summary(request: Request) -> dict[str, int]:
    graph_service = request.app.state.graph_service
    counts_query = """
    CALL {
      MATCH (c:Case)
      RETURN count(c) AS cases
    }
    CALL {
      MATCH (ct:Court)
      RETURN count(ct) AS courts
    }
    CALL {
      MATCH (p:Party)
      RETURN count(p) AS parties
    }
    CALL {
      MATCH (o:Order)
      RETURN count(o) AS orders
    }
    RETURN cases, courts, parties, orders
    """
    rel_query = """
    CALL {
      MATCH ()-[r1:FILED_IN]->()
      RETURN count(r1) AS filed_in
    }
    CALL {
      MATCH ()-[r2:HAS_PETITIONER]->()
      RETURN count(r2) AS has_petitioner
    }
    CALL {
      MATCH ()-[r3:HAS_RESPONDENT]->()
      RETURN count(r3) AS has_respondent
    }
    CALL {
      MATCH ()-[r4:HAS_ORDER]->()
      RETURN count(r4) AS has_order
    }
    RETURN filed_in, has_petitioner, has_respondent, has_order
    """
    counts = graph_service.run_query(counts_query)[0]
    rels = graph_service.run_query(rel_query)[0]
    return {
        "cases": int(counts["cases"]),
        "courts": int(counts["courts"]),
        "parties": int(counts["parties"]),
        "orders": int(counts["orders"]),
        "filed_in": int(rels["filed_in"]),
        "has_petitioner": int(rels["has_petitioner"]),
        "has_respondent": int(rels["has_respondent"]),
        "has_order": int(rels["has_order"]),
    }


@router.get("/dashboard/live-summary")
def dashboard_live_summary() -> dict[str, Any]:
    return live_metrics_service.fetch_summary()


@router.get("/cases")
def list_cases(
    request: Request,
    query: str | None = Query(default=None),
    court: str | None = Query(default=None),
    party: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=200),
) -> dict[str, Any]:
    graph_service = request.app.state.graph_service
    params = _build_filter_parameters(query, court, party)

    shared_match = f"""
    MATCH (c:Case)
    OPTIONAL MATCH (c)-[:FILED_IN]->(ct:Court)
    OPTIONAL MATCH (c)-[:HAS_PETITIONER]->(pet:Party)
    OPTIONAL MATCH (c)-[:HAS_RESPONDENT]->(resp:Party)
    WITH c,
         [name IN collect(DISTINCT ct.name) WHERE name IS NOT NULL AND trim(name) <> ""] AS courts,
         [name IN collect(DISTINCT pet.name) WHERE name IS NOT NULL AND trim(name) <> ""] AS petitioners,
         [name IN collect(DISTINCT resp.name) WHERE name IS NOT NULL AND trim(name) <> ""] AS respondents
    {FILTERS_CLAUSE}
    """

    data_query = (
        shared_match
        + """
    RETURN
      c.case_key AS case_key,
      c.case_reference AS case_reference,
      c.order_date AS order_date,
      c.order_document_url AS order_document_url,
      courts,
      petitioners,
      respondents
    ORDER BY c.order_date DESC, c.case_key ASC
    SKIP $offset
    LIMIT $limit
    """
    )
    count_query = shared_match + "RETURN count(c) AS total"

    data_params = {**params, "offset": offset, "limit": limit}
    rows = graph_service.run_query(data_query, data_params)
    total = int(graph_service.run_query(count_query, params)[0]["total"])

    return {
        "offset": offset,
        "limit": limit,
        "count": len(rows),
        "total": total,
        "data": rows,
    }


@router.get("/cases/{case_key}")
def case_details(case_key: str, request: Request) -> dict[str, Any]:
    graph_service = request.app.state.graph_service
    query = """
    MATCH (c:Case {case_key: $case_key})
    OPTIONAL MATCH (c)-[:FILED_IN]->(ct:Court)
    OPTIONAL MATCH (c)-[:HAS_PETITIONER]->(pet:Party)
    OPTIONAL MATCH (c)-[:HAS_RESPONDENT]->(resp:Party)
    OPTIONAL MATCH (c)-[:HAS_ORDER]->(o:Order)
    WITH c,
         [name IN collect(DISTINCT ct.name) WHERE name IS NOT NULL AND trim(name) <> ""] AS courts,
         [name IN collect(DISTINCT pet.name) WHERE name IS NOT NULL AND trim(name) <> ""] AS petitioners,
         [name IN collect(DISTINCT resp.name) WHERE name IS NOT NULL AND trim(name) <> ""] AS respondents,
         [order IN collect(DISTINCT o) WHERE order IS NOT NULL] AS orders
    RETURN
      c.case_key AS case_key,
      c.case_reference AS case_reference,
      c.order_date AS order_date,
      c.order_document_url AS order_document_url,
      courts,
      petitioners,
      respondents,
      [order IN orders | {
        order_key: order.order_key,
        order_date: order.order_date,
        document_url: order.document_url
      }] AS orders
    """
    rows = graph_service.run_query(query, {"case_key": case_key})
    if not rows:
        raise HTTPException(status_code=404, detail="Case not found.")
    return rows[0]


@router.get("/search")
def search(
    request: Request,
    q: str = Query(min_length=1),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, list[dict[str, Any]]]:
    graph_service = request.app.state.graph_service
    params = {"q": q.strip(), "limit": limit}

    case_rows = graph_service.run_query(
        """
        MATCH (c:Case)
        WHERE toLower(c.case_key) CONTAINS toLower($q)
        RETURN c.case_key AS value
        LIMIT $limit
        """,
        params,
    )
    court_rows = graph_service.run_query(
        """
        MATCH (ct:Court)
        WHERE toLower(ct.name) CONTAINS toLower($q)
        RETURN ct.name AS value
        LIMIT $limit
        """,
        params,
    )
    party_rows = graph_service.run_query(
        """
        MATCH (p:Party)
        WHERE toLower(p.name) CONTAINS toLower($q)
        RETURN p.name AS value
        LIMIT $limit
        """,
        params,
    )

    merged = (
        [{"type": "case", "value": row["value"]} for row in case_rows]
        + [{"type": "court", "value": row["value"]} for row in court_rows]
        + [{"type": "party", "value": row["value"]} for row in party_rows]
    )

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in merged:
        key = (item["type"], item["value"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    return {"results": deduped}
