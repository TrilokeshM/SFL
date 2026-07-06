from __future__ import annotations
from datetime import datetime
from typing import List, Optional, Literal
from pydantic import BaseModel, Field


# ---------- Skill catalog ----------

class SkillCatalogEntry(BaseModel):
    skill_id: str
    skill_name: str


class SuggestedSkill(BaseModel):
    skill_id: str
    skill_name: str
    confidence: float = Field(ge=0, le=1)
    rationale: str


# ---------- Questions ----------

class Question(BaseModel):
    type: Literal["conceptual", "codebase_specific"]
    question: str
    references: List[str] = Field(default_factory=list)  # file paths / symbols cited
    answer: Optional[str] = None


class SkillQuestions(BaseModel):
    skill_name: str
    questions: List[Question]


# ---------- Outcome evaluation / summary ----------

class OutcomeEvaluation(BaseModel):
    outcome: str
    status: Literal["met", "partial", "not_met", "not_verifiable"]
    evidence: str
    gap: Optional[str] = None


class EvaluationSummary(BaseModel):
    overall_alignment: Literal["strong", "partial", "weak"]
    alignment_score: Optional[float] = Field(default=None, ge=0, le=1)
    narrative: str
    outcome_evaluation: List[OutcomeEvaluation]
    strengths: List[str] = Field(default_factory=list)
    gaps: List[str] = Field(default_factory=list)


class EvaluationReport(BaseModel):
    skills: List[SkillQuestions]
    summary: EvaluationSummary


class SubmissionMetadata(BaseModel):
    model_config = {"protected_namespaces": ()}

    files_analyzed: int
    extraction_time_ms: int
    model_tokens_used: int = 0


class AnalyzeSubmissionResponse(BaseModel):
    submission_id: str
    project_title: str
    suggested_skills: List[SuggestedSkill]
    evaluation_report: EvaluationReport
    metadata: SubmissionMetadata
    processing_time_ms: int


# ---------- Viva / proctoring ----------

class VivaStartRequest(BaseModel):
    submission_id: str
    consent_acknowledged: bool



class AnsweredQuestion(BaseModel):
    model_config = {"extra": "ignore"}

    question: str
    answer: str = ""
    skill_name: Optional[str] = None

class VivaEndRequest(BaseModel):
    session_id: str
    answers: List[AnsweredQuestion] = []

class VivaStartResponse(BaseModel):
    session_id: str
    consent_text: str
    questions: List[SkillQuestions]
    thresholds: dict


EventType = Literal[
    "interview_started",
    "id_verified",
    "id_failed",
    "snapshot_captured",
    "gaze_off_screen",
    "multiple_faces_detected",
    "face_not_detected",
    "tab_switched",
    "fullscreen_exited",
    "paste_attempted",
    "screenshot_detected",
    "heartbeat"
]


class VivaEvent(BaseModel):
    session_id: str
    event_type: EventType
    timestamp: datetime
    duration_ms: Optional[int] = None
    confidence: Optional[float] = Field(default=None, ge=0, le=1)


class VivaEventAck(BaseModel):
    accepted: bool
    session_id: str
    event_type: str
    severity: Optional[Literal["low", "medium", "high"]] = None


class ProctoringFlag(BaseModel):
    type: str
    timestamp: datetime
    duration_ms: Optional[int] = None
    severity: Literal["low", "medium", "high"]
    note: Optional[str] = None


class ProctoringReport(BaseModel):
    session_id: str
    id_check: Literal["id_verified", "id_failed", "not_performed"]
    integrity_score: float = Field(ge=0, le=1)
    risk_level: Literal["low", "medium", "high"]
    flag_summary: dict
    flags: List[ProctoringFlag]
    narrative: str


class CombinedReport(BaseModel):
    project_title: str
    suggested_skills: List[SuggestedSkill]
    evaluation_report: EvaluationReport
    proctoring_report: ProctoringReport
    metadata: SubmissionMetadata
    processing_time_ms: int
