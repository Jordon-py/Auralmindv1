"""
FastAPI application exposing the AuralMind mastering API.

This module constructs the FastAPI app, configures CORS, defines API
routes and integrates with the job manager. Endpoints are namespaced
under `/api` and provide simple JSON responses for health checks, job
creation, status polling and retrieval of outputs and reports.
"""

from __future__ import annotations

import os
from collections import deque
import json
import logging
import mimetypes
from pathlib import Path
from typing import Optional
import dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import ValidationError

try:
    from .config import settings
    from .jobs import job_manager
    from .schemas import ErrorResponse, JobReportResponse, JobSettings, JobStatusResponse
except ImportError:  # pragma: no cover - supports `uvicorn main:app` from backend/
    from config import settings
    from jobs import job_manager
    from schemas import ErrorResponse, JobReportResponse, JobSettings, JobStatusResponse



logger = logging.getLogger("auralmind.api")

logger.info(
    dotenv.load_dotenv(".env")
)
app = FastAPI(title="AuralMind Mastering Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_origin_regex=os.getenv("ALLOWED_ORIGIN_REGEX"),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


@app.on_event("startup")
async def log_runtime_config() -> None:
    logger.info("CORS allow_origins=%s", settings.ALLOWED_ORIGINS)
    if settings.ALLOWED_ORIGIN_REGEX:
        logger.info("CORS allow_origin_regex=%s", settings.ALLOWED_ORIGIN_REGEX)


@app.get("/api/health")
async def health() -> dict:
    """Health check endpoint."""
    return {"status": "ok"}


def _validate_upload(file: UploadFile, max_mb: int) -> None:
    """Validate an uploaded file."""
    if not file:
        raise HTTPException(status_code=400, detail="Target file is required")
    allowed_exts = {".wav", ".mp3", ".aiff", ".flac", ".ogg"}
    ext = Path(file.filename or "").suffix.lower()
    if ext not in allowed_exts:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")


@app.post(
    "/api/jobs",
    response_model=JobStatusResponse,
    responses={400: {"model": ErrorResponse}},
)
async def create_job(
    background_tasks: BackgroundTasks,
    target: UploadFile = File(...),
    reference: Optional[UploadFile] = File(None),
    settings_json: Optional[str] = Form(None),
) -> JobStatusResponse:
    """Create a new mastering job."""
    _validate_upload(target, settings.MAX_UPLOAD_MB)
    if reference:
        _validate_upload(reference, settings.MAX_UPLOAD_MB)
    try:
        settings_data = json.loads(settings_json or "{}")
        job_settings = JobSettings(**settings_data)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid settings: {exc}")

    job = job_manager.create_job(job_settings)
    target_ext = Path(target.filename or "target").suffix or ".wav"
    job.target_path = job.workdir / "input" / f"target{target_ext}"
    with open(job.target_path, "wb") as out_f:
        content = await target.read()
        if len(content) > settings.MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Target file exceeds maximum allowed size")
        out_f.write(content)

    if reference:
        ref_ext = Path(reference.filename or "reference").suffix or ".wav"
        ref_path = job.workdir / "input" / f"reference{ref_ext}"
        job.reference_path = ref_path
        with open(ref_path, "wb") as ref_f:
            ref_content = await reference.read()
            if len(ref_content) > settings.MAX_UPLOAD_MB * 1024 * 1024:
                raise HTTPException(status_code=400, detail="Reference file exceeds maximum allowed size")
            ref_f.write(ref_content)

    background_tasks.add_task(job_manager.run_job, job.id)
    return JobStatusResponse(
        id=job.id,
        status=job.status,
        progress=job.progress,
        current_stage=job.current_stage,
        stage_detail=job.stage_detail,
        eta_seconds=job.eta_seconds,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error=job.error,
        settings=job.settings,
    )


@app.get(
    "/api/jobs/{job_id}",
    response_model=JobStatusResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_job_status(job_id: str) -> JobStatusResponse:
    """Retrieve the status of a given job."""
    job = job_manager.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse(
        id=job.id,
        status=job.status,
        progress=job.progress,
        current_stage=job.current_stage,
        stage_detail=job.stage_detail,
        eta_seconds=job.eta_seconds,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error=job.error,
        settings=job.settings,
    )


@app.get(
    "/api/jobs/{job_id}/download",
    responses={404: {"model": ErrorResponse}},
)
async def download_output(job_id: str) -> Response:
    """Download the mastered audio file for a completed job."""
    job = job_manager.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "completed":
        raise HTTPException(status_code=400, detail="Job is not complete")
    if not job.output_path.is_file():
        raise HTTPException(status_code=404, detail="Output file not found")
    mime, _ = mimetypes.guess_type(job.output_path.name)
    return FileResponse(job.output_path, media_type=mime or "application/octet-stream", filename=job.output_path.name)


@app.get(
    "/api/jobs/{job_id}/report",
    response_model=JobReportResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_report(job_id: str) -> JobReportResponse:
    """Retrieve the JSON report generated by the script."""
    job = job_manager.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "completed":
        raise HTTPException(status_code=400, detail="Job is not complete")
    if not job.report_path.is_file():
        raise HTTPException(status_code=404, detail="Report file not found")
    with open(job.report_path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            raise HTTPException(status_code=500, detail="Report is not valid JSON")
    return JobReportResponse(report=data)


@app.get(
    "/api/jobs/{job_id}/logs",
    responses={404: {"model": ErrorResponse}},
)
async def get_logs(job_id: str, lines: int = 50) -> Response:
    """Return the last few lines of the job's log file as plain text."""
    job = job_manager.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.log_path.is_file():
        # During early queued/processing states the log file may not exist yet.
        # Return an empty text response instead of a 404 to avoid noisy client errors.
        return Response(content="", media_type="text/plain")
    try:
        safe_lines = max(1, min(int(lines), 500))
        with open(job.log_path, "r", encoding="utf-8", errors="ignore") as f:
            tail = "".join(deque(f, maxlen=safe_lines))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unable to read log: {exc}")
    return Response(content=tail, media_type="text/plain")


@app.post(
    "/api/jobs/{job_id}/cancel",
    response_model=JobStatusResponse,
    responses={404: {"model": ErrorResponse}},
)
async def cancel_job(job_id: str) -> JobStatusResponse:
    """Attempt to cancel a running job."""
    job = job_manager.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    cancelled = job_manager.cancel_job(job_id)
    if not cancelled:
        raise HTTPException(status_code=400, detail="Unable to cancel job")
    return JobStatusResponse(
        id=job.id,
        status=job.status,
        progress=job.progress,
        current_stage=job.current_stage,
        stage_detail=job.stage_detail,
        eta_seconds=job.eta_seconds,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error=job.error,
        settings=job.settings,
    )
