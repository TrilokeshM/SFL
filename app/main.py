from __future__ import annotations
import json
import os
import shutil
import tempfile
import time
import uuid
from typing import Dict

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from app.config import settings
from app.zip_analyzer import safe_extract, build_evidence
from app.skill_engine import suggest_skills, generate_questions, evaluate_outcomes, evaluate_answers
from app.proctoring import (
    start_session, get_session, record_event, end_session, CONSENT_TEXT,
)
from app.schemas import (
    AnalyzeSubmissionResponse, SubmissionMetadata, EvaluationReport,
    VivaStartRequest, VivaStartResponse, VivaEvent, VivaEventAck, VivaEndRequest, AnsweredQuestion, CombinedReport,
)

app = FastAPI(title="Project Submission AI Analyzer", version="1.0.0")

# ---- in-memory store for analyzed submissions (no DB, per spec) ----
_submissions: Dict[str, dict] = {}


def _load_catalog() -> list:
    path = settings.SKILL_CATALOG_PATH
    if not os.path.isabs(path):
        path = os.path.join(os.getcwd(), path)
    if not os.path.exists(path):
        raise HTTPException(status_code=500, detail=f"Skill catalog not found at {path}")
    with open(path, "r") as f:
        return json.load(f)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/analyze-submission", response_model=AnalyzeSubmissionResponse)
async def analyze_submission(
    project_title: str = Form(...),
    project_outcomes: str = Form(...),
    zip_file: UploadFile = File(...),
    project_description: str = Form(""),
    questions_per_skill: int = Form(settings.DEFAULT_QUESTIONS_PER_SKILL),
):
    start = time.time()

    if not project_title.strip():
        raise HTTPException(status_code=400, detail="project_title cannot be empty.")
    if not project_outcomes.strip():
        raise HTTPException(status_code=400, detail="project_outcomes cannot be empty.")
    if questions_per_skill < 1:
        raise HTTPException(status_code=400, detail="questions_per_skill must be at least 1.")
    if not zip_file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="zip_file must be a .zip archive.")

    catalog = _load_catalog()

    workdir = tempfile.mkdtemp(prefix="submission_")
    zip_path = os.path.join(workdir, "upload.zip")
    try:
        contents = await zip_file.read()
        if len(contents) == 0:
            raise HTTPException(status_code=400, detail="Uploaded ZIP file is empty.")
        if len(contents) > settings.MAX_ZIP_SIZE_MB * 1024 * 1024:
            raise HTTPException(status_code=400, detail=f"ZIP exceeds {settings.MAX_ZIP_SIZE_MB}MB limit.")
        with open(zip_path, "wb") as f:
            f.write(contents)

        extract_dir = os.path.join(workdir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        extract_start = time.time()
        file_paths = safe_extract(zip_path, extract_dir)
        evidence = build_evidence(extract_dir, file_paths)
        extraction_time_ms = int((time.time() - extract_start) * 1000)

        total_tokens = 0

        try:
            suggested, tokens = await suggest_skills(evidence, catalog)
            total_tokens += tokens
            if not suggested:
                raise HTTPException(
                    status_code=422,
                    detail="No skills from the catalog could be matched to this codebase. "
                           "Ensure your ZIP contains relevant source code files."
                )

            questions_per_skill = 5 if len(suggested) == 1 else 2
            questions, tokens = await generate_questions(evidence, suggested, questions_per_skill)
            total_tokens += tokens

            summary, tokens = await evaluate_outcomes(evidence, project_title, project_description, project_outcomes)
            total_tokens += tokens
        except Exception as e:
            if isinstance(e, HTTPException):
                raise
            raise HTTPException(status_code=502, detail="LLM service failure. Please try again later.")

        submission_id = f"sub-{uuid.uuid4().hex[:12]}"
        evaluation_report = EvaluationReport(skills=questions, summary=summary)
        metadata = SubmissionMetadata(
            files_analyzed=evidence.files_analyzed,
            extraction_time_ms=extraction_time_ms,
            model_tokens_used=total_tokens,
        )
        processing_time_ms = int((time.time() - start) * 1000)

        _submissions[submission_id] = {
            "project_title": project_title,
            "project_description": project_description,
            "project_outcomes": project_outcomes,
            "suggested_skills": suggested,
            "evaluation_report": evaluation_report,
            "metadata": metadata,
        }

        return AnalyzeSubmissionResponse(
            submission_id=submission_id,
            project_title=project_title,
            suggested_skills=suggested,
            evaluation_report=evaluation_report,
            metadata=metadata,
            processing_time_ms=processing_time_ms,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@app.post("/viva-session/start", response_model=VivaStartResponse)
async def viva_start(payload: VivaStartRequest):
    submission = _submissions.get(payload.submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Unknown submission_id. Run /analyze-submission first.")
    if not payload.consent_acknowledged:
        raise HTTPException(status_code=400, detail="Student must acknowledge the consent notice before starting.")

    questions = submission["evaluation_report"].skills
    session = start_session(payload.submission_id, questions)

    thresholds = {
        "gaze_off_screen_low_s": settings.GAZE_OFF_LOW_S,
        "gaze_off_screen_medium_s": settings.GAZE_OFF_MEDIUM_S,
        "face_not_detected_low_s": settings.FACE_NOT_DETECTED_LOW_S,
        "face_not_detected_medium_s": settings.FACE_NOT_DETECTED_MEDIUM_S,
        "connection_timeout_s": settings.CONNECTION_TIMEOUT_S,
    }

    return VivaStartResponse(
        session_id=session.session_id,
        consent_text=CONSENT_TEXT,
        questions=questions,
        thresholds=thresholds,
    )


@app.post("/viva-session/event", response_model=VivaEventAck)
async def viva_event(event: VivaEvent):
    session = get_session(event.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown or expired session_id.")
    if session.ended:
        raise HTTPException(status_code=400, detail="This session has already ended.")
    if session.connection_lost_flagged:
        raise HTTPException(status_code=408, detail="Proctoring connection dropped due to inactivity. Session terminated.")

    severity = record_event(session, event.event_type, event.timestamp, event.duration_ms, event.confidence)
    return VivaEventAck(
        accepted=True,
        session_id=event.session_id,
        event_type=event.event_type,
        severity=severity,
    )


@app.post("/viva-session/end")
async def viva_end(request: VivaEndRequest):
    session = get_session(request.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown or expired session_id.")
    if session.ended:
        raise HTTPException(status_code=400, detail="This session has already ended.")
    if session.connection_lost_flagged:
        raise HTTPException(status_code=408, detail="Proctoring connection dropped due to inactivity. Session terminated.")

    proctoring_report = end_session(session)
    submission = _submissions.get(session.submission_id, {})

    # Evaluate the real-time viva answers (async — does not block event loop)
    answers_dicts = [ans.model_dump() for ans in request.answers]
    viva_summary = await evaluate_answers(
        project_title=submission.get("project_title", "Unknown"),
        project_description=submission.get("project_description", ""),
        project_outcomes=submission.get("project_outcomes", ""),
        answers=answers_dicts
    )
    
    # Update the submission's evaluation report with the new viva summary
    if "evaluation_report" in submission and submission["evaluation_report"]:
        submission["evaluation_report"].summary = viva_summary
        
        ans_lookup = {ans.question: ans.answer for ans in request.answers}
        for skill in submission["evaluation_report"].skills:
            for q in skill.questions:
                if q.question in ans_lookup:
                    q.answer = ans_lookup[q.question]

    combined = CombinedReport(
        project_title=submission.get("project_title", "Unknown"),
        suggested_skills=submission.get("suggested_skills", []),
        evaluation_report=submission.get("evaluation_report"),
        proctoring_report=proctoring_report,
        metadata=submission.get("metadata"),
        processing_time_ms=0,
    )
    return JSONResponse(content=json.loads(combined.model_dump_json()))


@app.get("/report/{submission_id}", response_model=None)
async def get_combined_report(submission_id: str):
    """Convenience endpoint: fetch the stored analysis for a submission_id
    (useful for the UI to re-render after a page refresh)."""
    submission = _submissions.get(submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Unknown submission_id.")
    return JSONResponse(content=json.loads(
        AnalyzeSubmissionResponse(
            submission_id=submission_id,
            project_title=submission["project_title"],
            suggested_skills=submission["suggested_skills"],
            evaluation_report=submission["evaluation_report"],
            metadata=submission["metadata"],
            processing_time_ms=0,
        ).model_dump_json()
    ))


# ---- Serve the simple frontend ----
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
