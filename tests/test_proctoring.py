from datetime import datetime, timezone
import pytest
from pydantic import ValidationError

from app.schemas import VivaEvent
from app.proctoring import start_session, record_event, end_session


def test_event_schema_rejects_unknown_event_type():
    with pytest.raises(ValidationError):
        VivaEvent(
            session_id="sess-x",
            event_type="student_yawned",  # not in the allowed literal set
            timestamp=datetime.now(timezone.utc),
        )


def test_event_schema_accepts_valid_event():
    event = VivaEvent(
        session_id="sess-x",
        event_type="gaze_off_screen",
        timestamp=datetime.now(timezone.utc),
        duration_ms=4200,
        confidence=0.81,
    )
    assert event.event_type == "gaze_off_screen"


def test_clean_session_has_perfect_score_and_low_risk():
    session = start_session("sub-1", questions=[])
    record_event(session, "interview_started", datetime.now(timezone.utc), None, None)
    record_event(session, "id_verified", datetime.now(timezone.utc), None, None)
    report = end_session(session)
    assert report.integrity_score == 1.0
    assert report.risk_level == "low"
    assert report.flags == []


def test_flags_reduce_score_and_raise_risk():
    session = start_session("sub-2", questions=[])
    record_event(session, "interview_started", datetime.now(timezone.utc), None, None)
    record_event(session, "id_verified", datetime.now(timezone.utc), None, None)
    # simulate a long gaze-off event -> high severity
    record_event(session, "gaze_off_screen", datetime.now(timezone.utc), 15000, 0.9)
    record_event(session, "tab_switched", datetime.now(timezone.utc), None, None)
    report = end_session(session)
    assert report.integrity_score < 1.0
    assert report.flag_summary.get("gaze_off_screen") == 1
    assert report.flag_summary.get("tab_switched") == 1


def test_failed_id_check_is_reflected_in_report():
    session = start_session("sub-3", questions=[])
    record_event(session, "interview_started", datetime.now(timezone.utc), None, None)
    record_event(session, "id_failed", datetime.now(timezone.utc), None, None)
    report = end_session(session)
    assert report.id_check == "id_failed"
    assert report.integrity_score < 1.0
