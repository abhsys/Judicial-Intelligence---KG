from fastapi import FastAPI
from fastapi import HTTPException
import os

from services.extractor import GraphExtractor
from services.graph_service import GraphService

app = FastAPI(title="Judicial Intelligence KG API")
graph_service = GraphService()
extractor = GraphExtractor(graph_service)


@app.on_event("startup")
def startup_event() -> None:
    graph_service.connect()
    app.state.graph_service = graph_service


@app.on_event("shutdown")
def shutdown_event() -> None:
    graph_service.close()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/db")
def health_db() -> dict[str, str]:
    if not graph_service.health_check():
        raise HTTPException(status_code=503, detail="Neo4j is not reachable.")
    return {"status": "ok"}


@app.get("/cases")
def get_cases(limit: int = 10) -> dict[str, object]:
    label = "Case"
    rows = graph_service.get_nodes_by_label(label=label, limit=limit)
    return {"label": label, "count": len(rows), "data": rows}


@app.post("/graph/build")
def build_graph() -> dict[str, int | str]:
    return extractor.build_case_graph()


@app.get("/graph/summary")
def graph_summary() -> dict[str, int]:
    return extractor.graph_summary()
