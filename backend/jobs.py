"""
Job management for the AuralMind mastering service.

This module contains classes and functions to create, execute and track
long-running mastering jobs. Jobs are executed asynchronously in a
thread pool so that HTTP requests remain non-blocking. Each job runs
the AuralMind script as a separate subprocess in a sandboxed working
directory.
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from .config import settings
from .schemas import JobSettings


@dataclass
class Job:
    """Represents a single mastering job."""

    id: str
    settings: JobSettings
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.utcnow())
    started_at: Optional[dt.datetime] = None
    finished_at: Optional[dt.datetime] = None
    status: str = "queued"  # queued, processing, completed, failed, cancelled
    progress: float = 0.0
    error: Optional[str] = None
    workdir: Path = field(default_factory=Path)
    target_path: Path = field(default_factory=Path)
    reference_path: Optional[Path] = None
    output_path: Path = field(default_factory=Path)
    report_path: Path = field(default_factory=Path)
    log_path: Path = field(default_factory=Path)
    process: Optional[subprocess.Popen] = None
    future: Optional[Future] = None


class JobManager:
    """Central registry and executor for mastering jobs."""

    def __init__(self) -> None:
        self.data_dir = Path(settings.DATA_DIR)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jobs: Dict[str, Job] = {}
        self.executor = ThreadPoolExecutor(max_workers=2)

    def create_job(self, settings_obj: JobSettings) -> Job:
        """Create a new job and register it in the registry."""
        job_id = uuid.uuid4().hex
        workdir = self.data_dir / job_id
        (workdir / "input").mkdir(parents=True, exist_ok=True)
        (workdir / "output").mkdir(parents=True, exist_ok=True)
        (workdir / "logs").mkdir(parents=True, exist_ok=True)

        job = Job(
            id=job_id,
            settings=settings_obj,
            workdir=workdir,
        )
        job.target_path = workdir / "input" / "target"
        job.reference_path = None
        job.output_path = workdir / "output" / "mastered.wav"
        job.report_path = workdir / "output" / "report.json"
        job.log_path = workdir / "logs" / "stdout.log"
        self.jobs[job_id] = job
        return job

    def run_job(self, job_id: str) -> None:
        """Schedule execution of the job in a background thread."""
        job = self.jobs[job_id]
        future = self.executor.submit(self._execute_job, job)
        job.future = future

    def _execute_job(self, job: Job) -> None:
        """Worker function executed in a background thread."""
        job.started_at = dt.datetime.utcnow()
        job.status = "processing"
        job.progress = 5.0
        script = settings.AURALMIND_SCRIPT_PATH
        cmd = [
            sys.executable,
            script,
            "--target",
            str(job.target_path),
            "--out",
            str(job.output_path),
        ]
        if job.reference_path:
            cmd.extend(["--reference", str(job.reference_path)])
        if job.settings.preset:
            cmd.extend(["--preset", job.settings.preset])
        if job.settings.enable_demucs is True:
            cmd.append("--stems")
        elif job.settings.enable_demucs is False:
            cmd.append("--no-stems")
        if job.settings.mono_sub is True:
            cmd.append("--mono-sub")
        elif job.settings.mono_sub is False:
            cmd.append("--no-mono-sub")
        if job.settings.dynamic_eq is True:
            cmd.append("--masking-eq")
        elif job.settings.dynamic_eq is False:
            cmd.append("--no-masking-eq")
        if job.settings.truepeak_limiter is False:
            cmd.append("--no-limiter")
        if job.settings.target_lufs is not None:
            cmd.extend(["--target-lufs", str(job.settings.target_lufs)])
        if job.settings.true_peak_ceiling is not None:
            cmd.extend(["--ceiling", str(job.settings.true_peak_ceiling)])
        if job.settings.warmth and job.settings.warmth > 0:
            warmth = max(0.0, min(float(job.settings.warmth), 100.0)) / 100.0
            cmd.extend(["--warmth", f"{warmth:.4f}"])

        if job.settings.section_aware_mastering is True:
            if job.settings.section_lift_mix is not None:
                cmd.extend(["--hooklift-mix", str(job.settings.section_lift_mix)])
        elif job.settings.section_aware_mastering is False:
            cmd.append("--no-hooklift")

        if job.settings.groove_transient_sculpting is False:
            cmd.extend(["--transient-mix", "0.0"])
        elif job.settings.groove_transient_boost_db is not None:
            cmd.extend(["--transient-boost", str(job.settings.groove_transient_boost_db)])

        out_subtype = "PCM_16" if int(job.settings.output_pcm_bits) == 16 else "PCM_24"
        cmd.extend(["--out-subtype", out_subtype])

        env = os.environ.copy()
        log_file = open(job.log_path, "w", encoding="utf-8")
        try:
            job.process = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=str(job.workdir),
                env=env,
            )
            while True:
                if job.process.poll() is not None:
                    break
                job.progress = min(job.progress + 5.0, 95.0)
                time.sleep(1.0)
            retcode = job.process.wait(timeout=0)
            if retcode == 0:
                job.status = "completed"
                job.progress = 100.0
            else:
                job.status = "failed"
                job.progress = 100.0
                job.error = f"Script exited with code {retcode}"
        except subprocess.TimeoutExpired:
            job.status = "failed"
            job.error = "Job timed out"
            if job.process:
                job.process.kill()
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            if job.process:
                job.process.kill()
        finally:
            job.finished_at = dt.datetime.utcnow()
            job.progress = max(job.progress, 100.0)
            log_file.close()

    def cancel_job(self, job_id: str) -> bool:
        """Attempt to cancel a running job."""
        job = self.jobs.get(job_id)
        if not job:
            return False
        if job.status not in {"queued", "processing"}:
            return False
        if job.process and job.process.poll() is None:
            try:
                job.process.kill()
                job.status = "cancelled"
                job.finished_at = dt.datetime.utcnow()
                job.progress = 100.0
                return True
            except Exception:
                return False
        job.status = "cancelled"
        job.finished_at = dt.datetime.utcnow()
        job.progress = 100.0
        return True


job_manager = JobManager()
