"""FastAPI Backend Server for AI Job Hunter

Provides REST endpoints and Server-Sent Events (SSE) log streaming for real-time
agent execution visibility, job vetting results, and Excel report download.
"""

import asyncio
import json
import logging
import os
import queue
import tempfile
import threading
import time
import uuid
from typing import Dict, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from job_hunter.models import JobSearchCriteria
from job_hunter.orchestrator import ResumeJobOrchestrator

app = FastAPI(title="AI Job Hunter API", version="2.0")

# CORS middleware for smooth frontend interaction
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store in-memory run session states
SESSIONS: Dict[str, dict] = {}


import sys

# Ensure stdout and stderr use utf-8 encoding on Windows to handle unicode emojis in logs
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

FRIENDLY_NAMES = {
    "job_hunter.orchestrator": "Pipeline",
    "job_hunter.agents.resume_analyzer": "Resume Analyzer",
    "job_hunter.agents.search_strategy": "Search Strategy",
    "job_hunter.agents.planner": "Job Planner",
    "job_hunter.agents.reflector": "Quality Evaluator",
    "job_hunter.agents.vetting": "Match Evaluator",
    "job_hunter.agents.api_agents": "API Searcher",
    "job_hunter.agents.apify_agent": "LinkedIn Agent",
    "job_hunter.agents.ats_agent": "Direct ATS",
    "job_hunter.llm": "AI Engine",
    "uvicorn": "Server",
    "uvicorn.access": "Server Access",
    "uvicorn.error": "Server Error",
}


class FriendlyConsoleFormatter(logging.Formatter):
    """Formats technical module names into clean, friendly titles for console output."""

    def format(self, record: logging.LogRecord) -> str:
        friendly_name = FRIENDLY_NAMES.get(
            record.name,
            record.name.split(".")[-1].replace("_", " ").title(),
        )
        orig_name = record.name
        record.name = friendly_name
        result = super().format(record)
        record.name = orig_name
        return result


# Configure standard console logging so users get clean, readable status updates
console_formatter = FriendlyConsoleFormatter(
    "[%(asctime)s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(console_formatter)
console_handler.setLevel(logging.INFO)

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
    root_logger.addHandler(console_handler)

jh_logger = logging.getLogger("job_hunter")
jh_logger.setLevel(logging.INFO)


class SessionLogHandler(logging.Handler):
    """Custom logging handler that pushes log records into a session-specific queue."""

    def __init__(self, log_queue: queue.Queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        try:
            msg = self.format(record)
            self.log_queue.put(msg)
        except Exception:
            pass


@app.post("/api/search")
async def start_job_search(
    resume: UploadFile = File(...),
    locations: str = Form(""),
    keywords: str = Form(""),
    remote_only: bool = Form(False),
    target_india_only: bool = Form(True),
    min_salary: Optional[int] = Form(None),
    max_evals: int = Form(15),
    posted_within_days: int = Form(1),
):
    """Starts the AI Job Search pipeline in a background thread and returns a session_id."""
    session_id = str(uuid.uuid4())
    
    # Save uploaded file to temp directory
    suffix = os.path.splitext(resume.filename)[1] or ".pdf"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await resume.read()
        tmp.write(content)
        tmp_path = tmp.name

    # Parse parameters
    loc_list = [l.strip() for l in locations.split(",") if l.strip()]
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()]

    criteria = JobSearchCriteria(
        keywords=kw_list,
        locations=loc_list,
        remote_only=remote_only,
        min_salary=min_salary if (min_salary and min_salary > 0) else None,
        target_india_only=target_india_only,
        posted_within_days=posted_within_days,
    )

    log_queue = queue.Queue()
    session_data = {
        "id": session_id,
        "status": "running",
        "log_queue": log_queue,
        "logs": [],
        "result": None,
        "error": None,
        "created_at": time.time(),
        "output_path": None,
    }
    SESSIONS[session_id] = session_data

    # Setup logger interceptor for SSE session queue
    handler = SessionLogHandler(log_queue)
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s [%(name)s]: %(message)s", datefmt="%H:%M:%S")
    )
    jh_logger.addHandler(handler)

    def run_pipeline():
        try:
            logging.info("🚀 [Session %s] Initializing ResumeJobOrchestrator pipeline...", session_id[:8])
            logging.info("📋 [Session %s] Params: Locations=%r | Keywords=%r | Remote Only=%s | India Only=%s | Max Evals=%d", 
                         session_id[:8], criteria.locations, criteria.keywords, criteria.remote_only, criteria.target_india_only, max_evals)
            output_dir = "outputs"
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, f"job_matches_{session_id[:8]}.xlsx")
            
            orchestrator = ResumeJobOrchestrator()
            res = orchestrator.run(
                resume_path=tmp_path,
                criteria=criteria,
                max_evals=max_evals,
                output_path=output_path,
            )
            session_data["result"] = res
            session_data["output_path"] = output_path
            session_data["status"] = "completed"
            logging.info("✅ [Session %s] Search pipeline completed successfully.", session_id[:8])
        except Exception as exc:
            import traceback
            err_msg = str(exc)
            tb = traceback.format_exc()
            session_data["error"] = err_msg
            session_data["status"] = "failed"
            logging.error("❌ [Session %s] Pipeline failure: %s\n%s", session_id[:8], err_msg, tb)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            jh_logger.removeHandler(handler)

    thread = threading.Thread(target=run_pipeline, daemon=True)
    thread.start()

    return {"session_id": session_id, "status": "running"}


@app.get("/api/stream-logs/{session_id}")
async def stream_logs(session_id: str):
    """Server-Sent Events (SSE) endpoint for real-time log streaming."""
    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail="Session not found")

    session = SESSIONS[session_id]
    log_queue: queue.Queue = session["log_queue"]

    async def event_generator():
        current_phase = "Initializing"
        idle_counter = 0
        while True:
            # Drain queue
            has_logs = False
            while not log_queue.empty():
                has_logs = True
                log_line = log_queue.get_nowait()
                session["logs"].append(log_line)

                # Heuristic phase detector
                if "Phase 4" in log_line:
                    current_phase = "Phase 4: Writing Excel Report"
                elif "Phase 3" in log_line:
                    current_phase = "Phase 3: LLM Vetting Jobs"
                elif "ReAct iteration" in log_line or "ReAct loop" in log_line:
                    current_phase = "Phase 2b: ReAct Quality Reflection"
                elif "Phase 2a" in log_line:
                    current_phase = "Phase 2a: LLM Source Selection"
                elif "Phase 2" in log_line:
                    current_phase = "Phase 2: Parallel Board Ingestion"
                elif "Phase 1.5" in log_line:
                    current_phase = "Phase 1.5: Generating Search Strategy"
                elif "Phase 1" in log_line:
                    current_phase = "Phase 1: Resume Analysis"

                payload = {
                    "log": log_line,
                    "phase": current_phase,
                    "status": session["status"],
                }
                yield f"data: {json.dumps(payload)}\n\n"

            if session["status"] in ("completed", "failed"):
                # Final notification payload
                payload = {
                    "log": f"--- RUN {session['status'].upper()} ---",
                    "phase": current_phase,
                    "status": session["status"],
                    "error": session.get("error"),
                }
                yield f"data: {json.dumps(payload)}\n\n"
                break

            if not has_logs:
                idle_counter += 1
                if idle_counter >= 20:  # 20 * 0.15s = 3s
                    idle_counter = 0
                    yield ": keep-alive\n\n"

            await asyncio.sleep(0.15)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/results/{session_id}")
async def get_results(session_id: str):
    """Retrieves structured Candidate Profile, metrics, and Vetted Job listings."""
    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail="Session not found")

    session = SESSIONS[session_id]
    if session["status"] == "running":
        return {"status": "running"}

    if session["status"] == "failed":
        return {"status": "failed", "error": session.get("error")}

    res = session["result"]
    profile = res["profile"]
    jobs = res["jobs"]

    profile_dict = {
        "summary": profile.summary,
        "skills": profile.skills,
        "seniority": profile.seniority,
        "job_titles": profile.job_titles,
        "search_queries": profile.search_queries,
        "activated_sources": profile.activated_sources,
        "react_iterations": profile.react_iterations,
    }

    jobs_list = [
        {
            "title": j.title,
            "company": j.company,
            "location": j.location,
            "url": j.url,
            "source": j.source,
            "description": j.description,
            "salary": j.salary,
            "posted_at": getattr(j, "posted_at", ""),
            "posted_timestamp": getattr(j, "posted_timestamp", 0.0),
            "fit_score": j.fit_score,
            "fit_decision": j.fit_decision,
            "fit_reasons": j.fit_reasons,
            "gaps_identified": j.gaps_identified,
        }
        for j in jobs
    ]

    return {
        "status": "completed",
        "profile": profile_dict,
        "metrics": res["metrics"],
        "jobs": jobs_list,
        "logs": session["logs"],
    }


@app.get("/api/download-excel/{session_id}")
async def download_excel(session_id: str):
    """Downloads the Excel report for a completed session."""
    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail="Session not found")

    session = SESSIONS[session_id]
    path = session.get("output_path")
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Excel file not found")

    return FileResponse(
        path,
        filename="Job_Vetting_Report.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.post("/api/tailor-application")
async def tailor_application(
    session_id: str = Form(...),
    job_index: int = Form(...),
):
    """Generates tailored cover letter, recruiter outreach message, and ATS summary for a target job."""
    from job_hunter.agents.tailor_agent import TailorAgent

    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail="Session not found")

    session = SESSIONS[session_id]
    result = session.get("result")
    if not result:
        raise HTTPException(status_code=400, detail="Search session not completed yet")

    profile = result.get("profile")
    jobs = result.get("jobs", [])

    if job_index < 0 or job_index >= len(jobs):
        raise HTTPException(status_code=404, detail="Job index out of range")

    job = jobs[job_index]
    agent = TailorAgent()
    package = agent.tailor(profile, job)

    return {
        "job_title": job.title,
        "company": job.company,
        "cover_letter": package.cover_letter,
        "outreach_message": package.outreach_message,
        "tailored_summary": package.tailored_summary,
        "key_highlights": package.key_highlights,
    }



# Mount static directory for frontend
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def root():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "AI Job Hunter API is running. Build static/index.html to view UI."}


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("server:app", host=host, port=port, reload=False)

