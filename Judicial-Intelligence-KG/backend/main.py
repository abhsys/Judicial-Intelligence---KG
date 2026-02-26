import os

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

try:
    from .routers.search import router as search_router
    from .routers.upload import router as graph_router
    from .services.extractor import GraphExtractor
    from .services.graph_service import GraphService
    from .services.graph_service import GraphUnavailableError
except ImportError:
    from routers.search import router as search_router
    from routers.upload import router as graph_router
    from services.extractor import GraphExtractor
    from services.graph_service import GraphService
    from services.graph_service import GraphUnavailableError

app = FastAPI(title="Judicial Intelligence KG API")
graph_service = GraphService()
extractor = GraphExtractor(graph_service)

default_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
origins_env = os.getenv("CORS_ALLOW_ORIGINS", "")
configured_origins = [
    item.strip() for item in origins_env.split(",") if item.strip()
] or default_origins
allow_all_origins = "*" in configured_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if allow_all_origins else configured_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search_router)
app.include_router(graph_router)


@app.exception_handler(GraphUnavailableError)
async def graph_unavailable_handler(
    request: Request, exc: GraphUnavailableError
) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.on_event("startup")
def startup_event() -> None:
    graph_service.connect()
    app.state.graph_service = graph_service
    app.state.extractor = extractor


@app.on_event("shutdown")
def shutdown_event() -> None:
    graph_service.close()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/health")
def api_health() -> dict[str, str]:
    return health()


@app.get("/health/db")
def health_db() -> dict[str, str]:
    if not graph_service.health_check():
        raise HTTPException(status_code=503, detail="Neo4j is not reachable.")
    return {"status": "ok"}


@app.get("/api/health/db")
def api_health_db() -> dict[str, str]:
    return health_db()


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
