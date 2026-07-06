"""
Live viva proctoring: session lifecycle, integrity event ingestion, a
background watchdog that flags dropped connections, and final scoring.

Everything is kept in memory for the session's lifetime, per the spec
(no database). A session dict is deliberately simple — this is meant to
be read top-to-bottom, not to be a production-grade job queue.
"""
from __future__ import annotations
import asyncio
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.config import settings
from app.schemas import ProctoringFlag, ProctoringReport, SkillQuestions

CONSENT_TEXT = (
    "This session will be monitored for academic integrity. Your webcam feed is processed "
    "entirely on your device — no video or audio is ever sent to or stored on our servers. "
    "Only short, anonymous signals (e.g. 'looked away for 4 seconds', 'tab switched') are sent. "
    "By continuing, you agree to this monitoring for the duration of the viva."
)

FLAG_EVENT_TYPES = {
    "gaze_off_screen", "multiple_faces_detected", "face_not_detected",
    "tab_switched", "fullscreen_exited", "paste_attempted", "screenshot_detected",
}


class VivaSession:
    def __init__(self, session_id: str, submission_id: str, questions: List[SkillQuestions]):
        self.session_id = session_id
        self.submission_id = submission_id
        self.questions = questions
        self.id_check: str = "not_performed"
        self.flags: List[ProctoringFlag] = []
        self.started_at = datetime.now(timezone.utc)
        self.last_event_at = datetime.now(timezone.utc)
        self.ended = False
        self.connection_lost_flagged = False
        self.watchdog_task: Optional[asyncio.Task] = None


_sessions: Dict[str, VivaSession] = {}


def _severity_for_duration(event_type: str, duration_ms: Optional[int]) -> str:
    seconds = (duration_ms or 0) / 1000.0
    if event_type == "gaze_off_screen":
        low, medium = settings.GAZE_OFF_LOW_S, settings.GAZE_OFF_MEDIUM_S
    elif event_type == "face_not_detected":
        low, medium = settings.FACE_NOT_DETECTED_LOW_S, settings.FACE_NOT_DETECTED_MEDIUM_S
    else:
        # instantaneous events (tab switch, paste, fullscreen exit, multi-face, screenshot)
        return "medium"
    if seconds >= medium:
        return "high" if seconds >= medium * 1.5 else "medium"
    if seconds >= low:
        return "low"
    return "low"


async def _watchdog(session_id: str):
    """Polls a session; if no event arrives within CONNECTION_TIMEOUT_S, flags it once."""
    try:
        while True:
            await asyncio.sleep(settings.WATCHDOG_POLL_INTERVAL_S)
            session = _sessions.get(session_id)
            if session is None or session.ended:
                return
            elapsed = (datetime.now(timezone.utc) - session.last_event_at).total_seconds()
            if elapsed >= settings.CONNECTION_TIMEOUT_S and not session.connection_lost_flagged:
                session.flags.append(ProctoringFlag(
                    type="connection_lost",
                    timestamp=datetime.now(timezone.utc),
                    duration_ms=int(elapsed * 1000),
                    severity="high",
                    note="No proctoring events received — camera may have been disabled or connection dropped.",
                ))
                session.connection_lost_flagged = True
    except asyncio.CancelledError:
        return


def start_session(submission_id: str, questions: List[SkillQuestions]) -> VivaSession:
    session_id = f"sess-{uuid.uuid4().hex[:12]}"
    session = VivaSession(session_id, submission_id, questions)
    _sessions[session_id] = session
    try:
        session.watchdog_task = asyncio.create_task(_watchdog(session_id))
    except RuntimeError:
        # No running event loop (e.g. called from a sync unit test) —
        # the watchdog simply won't run; harmless outside the live server.
        session.watchdog_task = None
    return session


def get_session(session_id: str) -> Optional[VivaSession]:
    return _sessions.get(session_id)


def record_event(session: VivaSession, event_type: str, timestamp: datetime,
                  duration_ms: Optional[int], confidence: Optional[float]) -> Optional[str]:
    """Returns the assigned severity if this was a flaggable event, else None."""
    session.last_event_at = datetime.now(timezone.utc)
    session.connection_lost_flagged = False  # events are flowing again

    if event_type == "id_verified":
        session.id_check = "id_verified"
        return None
    if event_type == "id_failed":
        session.id_check = "id_failed"
        return None
    if event_type in ("interview_started", "snapshot_captured"):
        return None

    if event_type in FLAG_EVENT_TYPES:
        severity = _severity_for_duration(event_type, duration_ms)
        session.flags.append(ProctoringFlag(
            type=event_type,
            timestamp=timestamp,
            duration_ms=duration_ms,
            severity=severity,
        ))
        return severity

    return None


def end_session(session: VivaSession) -> ProctoringReport:
    session.ended = True
    if session.watchdog_task:
        session.watchdog_task.cancel()

    flag_summary: Dict[str, int] = {}
    for flag in session.flags:
        flag_summary[flag.type] = flag_summary.get(flag.type, 0) + 1

    score = 1.0
    for flag in session.flags:
        if flag.severity == "low":
            score -= settings.SEVERITY_WEIGHT_LOW
        elif flag.severity == "medium":
            score -= settings.SEVERITY_WEIGHT_MEDIUM
        else:
            score -= settings.SEVERITY_WEIGHT_HIGH
    if session.id_check == "id_failed":
        score -= 0.3
    score = max(0.0, min(1.0, round(score, 3)))

    if score >= settings.RISK_LOW_MIN_SCORE:
        risk = "low"
    elif score >= settings.RISK_MEDIUM_MIN_SCORE:
        risk = "medium"
    else:
        risk = "high"

    narrative = _build_narrative(session, flag_summary, risk)

    return ProctoringReport(
        session_id=session.session_id,
        id_check=session.id_check,
        integrity_score=score,
        risk_level=risk,
        flag_summary=flag_summary,
        flags=session.flags,
        narrative=narrative,
    )


def _build_narrative(session: VivaSession, flag_summary: Dict[str, int], risk: str) -> str:
    if not session.flags:
        return "The student remained on-screen and engaged for the entire session, with no integrity flags raised."
    total = sum(flag_summary.values())
    top = sorted(flag_summary.items(), key=lambda kv: -kv[1])[:2]
    top_desc = ", ".join(f"{count}x {name.replace('_', ' ')}" for name, count in top)
    if risk == "low":
        return (f"The student remained on-screen for the large majority of the session; "
                f"{total} minor event(s) were recorded ({top_desc}) with no strong evidence of external assistance.")
    if risk == "medium":
        return (f"Several integrity signals were recorded during the session ({top_desc}, "
                f"{total} total), warranting a manual review of the flagged moments alongside the recording notes.")
    return (f"The session raised significant integrity concerns ({top_desc}, {total} total flags"
            f"{', including a failed identity check' if session.id_check == 'id_failed' else ''}); "
            f"manual review by the mentor is strongly recommended before accepting this viva.")
