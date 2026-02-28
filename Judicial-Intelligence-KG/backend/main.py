import os

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware

try:
    from .routers.intake import router as intake_router
    from .routers.search import router as search_router
    from .routers.upload import router as graph_router
    from .services.file_ingest_service import FileIngestService
    from .services.extractor import GraphExtractor
    from .services.groq_keyword_service import GroqKeywordService
    from .services.graph_service import GraphService
    from .services.indiankanoon_service import IndianKanoonService
    from .services.job_store import JobStore
    from .services.upload_graph_service import UploadGraphService
except ImportError:
    from routers.intake import router as intake_router
    from routers.search import router as search_router
    from routers.upload import router as graph_router
    from services.file_ingest_service import FileIngestService
    from services.extractor import GraphExtractor
    from services.groq_keyword_service import GroqKeywordService
    from services.graph_service import GraphService
    from services.indiankanoon_service import IndianKanoonService
    from services.job_store import JobStore
    from services.upload_graph_service import UploadGraphService

app = FastAPI(title="Judicial Intelligence KG API")
graph_service = GraphService()
extractor = GraphExtractor(graph_service)
job_store = JobStore()
file_ingest_service = FileIngestService()
groq_keyword_service = GroqKeywordService()
indiankanoon_service = IndianKanoonService()
upload_graph_service = UploadGraphService(
    graph_service=graph_service,
    keyword_service=groq_keyword_service,
    search_service=indiankanoon_service,
)

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
app.include_router(intake_router)


@app.on_event("startup")
def startup_event() -> None:
    graph_service.connect()
    app.state.graph_service = graph_service
    app.state.extractor = extractor
    app.state.job_store = job_store
    app.state.file_ingest_service = file_ingest_service
    app.state.upload_graph_service = upload_graph_service
    app.state.indiankanoon_service = indiankanoon_service


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
