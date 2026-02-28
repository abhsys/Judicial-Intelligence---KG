from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

router = APIRouter(prefix="/api/intake", tags=["intake"])
logger = logging.getLogger("intake_pipeline")


@router.post("https://caselinq.onrender.com/upload")
async def intake_upload(request: Request, file: UploadFile = File(...)) -> dict[str, str]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file must include a filename.")

    file_ingest_service = request.app.state.file_ingest_service
    job_store = request.app.state.job_store
    upload_graph_service = request.app.state.upload_graph_service

    content = await file.read()
    try:
        file_type = file_ingest_service.validate(file.filename, content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    file_hash = file_ingest_service.sha256(content)
    job = job_store.create()
    logger.info(
        "[Job %s] Upload accepted: filename='%s' type=%s size=%s bytes",
        job["job_id"],
        file.filename,
        file_type,
        len(content),
    )

    asyncio.create_task(
        _run_upload_pipeline(
            job_id=job["job_id"],
            filename=file.filename,
            file_type=file_type,
            file_hash=file_hash,
            content=content,
            file_ingest_service=file_ingest_service,
            upload_graph_service=upload_graph_service,
            job_store=job_store,
        )
    )
    return {"job_id": job["job_id"], "status": "queued"}


@router.get("/jobs/{job_id}")
def intake_job_status(job_id: str, request: Request) -> dict:
    job_store = request.app.state.job_store
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "progress": job["progress"],
        "stage": job["stage"],
        "stage_detail": job.get("stage_detail") or "",
        "upload_id": job.get("upload_id"),
        "keywords": job.get("keywords") or [],
        "indexed_results": job.get("indexed_results") or 0,
        "warnings": job.get("warnings") or [],
        "error": job.get("error"),
    }


@router.get("/jobs/{job_id}/graph")
def intake_job_graph(job_id: str, request: Request) -> dict:
    job_store = request.app.state.job_store
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.get("status") != "completed":
        raise HTTPException(status_code=409, detail="Job is not completed yet.")
    return job.get("graph") or {
        "nodes": [],
        "edges": [],
        "node_count": 0,
        "edge_count": 0,
        "upload_id": job.get("upload_id"),
    }


@router.get("/uploads/{upload_id}/details")
def intake_upload_details(upload_id: str, request: Request) -> dict:
    upload_graph_service = request.app.state.upload_graph_service
    details = upload_graph_service.fetch_upload_details(upload_id=upload_id)
    if not details:
        raise HTTPException(status_code=404, detail="Upload not found.")
    return details


async def _run_upload_pipeline(
    *,
    job_id: str,
    filename: str,
    file_type: str,
    file_hash: str,
    content: bytes,
    file_ingest_service,
    upload_graph_service,
    job_store,
) -> None:
    try:
        logger.info("[Job %s] Pipeline started", job_id)
        job_store.update(
            job_id,
            status="running",
            progress=8,
            stage="extracting_text",
            stage_detail="Reading and extracting text from your document.",
        )
        extracted_text = await asyncio.to_thread(
            file_ingest_service.extract_text, file_type, content
        )
        if not extracted_text:
            raise ValueError("No text could be extracted from this file.")
        logger.info(
            "[Job %s] Text extraction complete: approx_chars=%s",
            job_id,
            len(extracted_text),
        )

        job_store.update(
            job_id,
            progress=28,
            stage="preparing_analysis",
            stage_detail="Preparing legal context for AI keyword finalization.",
        )
        logger.info("[Job %s] Sending text to Groq for keyword finalization", job_id)

        def progress_callback(progress: int, stage: str) -> None:
            stage_labels = {
                "ensuring_constraints": "Preparing graph schema.",
                "finalizing_keywords": "AI is finalizing 6 legal keywords.",
                "saving_upload_node": "Saving upload metadata into graph.",
                "cross_keyword_matching": "AI is matching cases across all keywords.",
                "saving_selected_cases": "Linking selected cases into graph.",
                "building_graph_view": "Preparing final graph visualization.",
            }
            detail = stage_labels.get(stage, stage.replace("_", " ").capitalize())
            job_store.update(
                job_id,
                status="running",
                progress=max(0, min(99, int(progress))),
                stage=stage,
                stage_detail=detail,
            )

        result = await asyncio.to_thread(
            upload_graph_service.process_upload,
            filename=filename,
            file_type=file_type,
            file_hash=file_hash,
            extracted_text=extracted_text,
            progress_callback=progress_callback,
        )
        logger.info(
            "[Job %s] Search+ingest complete: keywords=%s indexed_results=%s",
            job_id,
            result.get("keywords") or [],
            int(result.get("indexed_results") or 0),
        )

        job_store.update(
            job_id,
            status="completed",
            progress=100,
            stage="completed",
            stage_detail="Upload processing completed. Graph is ready.",
            upload_id=result.get("upload_id"),
            keywords=result.get("keywords") or [],
            indexed_results=int(result.get("indexed_results") or 0),
            warnings=result.get("warnings") or [],
            graph=result.get("graph"),
            error=None,
        )
        graph = result.get("graph") or {}
        logger.info(
            "[Job %s] Completed successfully: nodes=%s edges=%s upload_id=%s",
            job_id,
            graph.get("node_count", 0),
            graph.get("edge_count", 0),
            result.get("upload_id"),
        )
    except Exception as exc:
        job_store.update(
            job_id,
            status="failed",
            progress=100,
            stage="failed",
            stage_detail="Upload processing failed.",
            error=str(exc),
        )
        logger.exception("[Job %s] Failed: %s", job_id, exc)
