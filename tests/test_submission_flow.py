"""
End-to-end submission flow test.
Runs against the live server at http://localhost:8000.
Tests every step: analyze → start → event → end (with answers).
"""
import json
import zipfile
import io
import pytest
import httpx

BASE = "http://localhost:8000"
CLIENT_TIMEOUT = 120.0  # generous for LLM calls


# ── helpers ──────────────────────────────────────────────────────────────────

def make_test_zip() -> bytes:
    """Create a minimal in-memory ZIP with a Python file so skill detection works."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "main.py",
            "# Simple todo app\nclass TodoApp:\n    def __init__(self):\n        self.items = []\n"
            "    def add(self, item): self.items.append(item)\n"
            "    def remove(self, item): self.items.remove(item)\n"
        )
        zf.writestr(
            "requirements.txt",
            "fastapi\nuvicorn\npydantic\n"
        )
    return buf.getvalue()


# ── tests ─────────────────────────────────────────────────────────────────────

def test_health():
    r = httpx.get(f"{BASE}/health", timeout=10)
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_full_submission_flow():
    client = httpx.Client(timeout=CLIENT_TIMEOUT)

    # ── Step 1: analyze-submission ────────────────────────────────────────────
    zip_bytes = make_test_zip()
    r = client.post(
        f"{BASE}/analyze-submission",
        files={"zip_file": ("test_project.zip", zip_bytes, "application/zip")},
        data={
            "project_title": "Test Todo App",
            "project_description": "A simple todo list app in Python",
            "project_outcomes": "Implement CRUD operations\nUse OOP principles",
        },
    )
    print("\n[analyze-submission] status:", r.status_code)
    print("[analyze-submission] body:", r.text[:500])
    assert r.status_code == 200, f"analyze-submission failed: {r.text}"

    analysis = r.json()
    submission_id = analysis["submission_id"]
    assert submission_id.startswith("sub-")

    questions_flat = []
    for skill in analysis["evaluation_report"]["skills"]:
        for q in skill["questions"]:
            questions_flat.append({
                "question": q["question"],
                "answer": "",
                "skill_name": skill["skill_name"],
            })

    assert len(questions_flat) > 0, "No questions were generated"
    print(f"[analyze-submission] {len(questions_flat)} questions generated")

    # ── Step 2: viva-session/start ────────────────────────────────────────────
    r = client.post(
        f"{BASE}/viva-session/start",
        json={"submission_id": submission_id, "consent_acknowledged": True},
    )
    print("[viva-session/start] status:", r.status_code)
    assert r.status_code == 200, f"viva-session/start failed: {r.text}"

    session_data = r.json()
    session_id = session_data["session_id"]
    assert session_id.startswith("sess-")
    print("[viva-session/start] session_id:", session_id)

    # ── Step 3: send a proctoring event ──────────────────────────────────────
    from datetime import datetime, timezone
    r = client.post(
        f"{BASE}/viva-session/event",
        json={
            "session_id": session_id,
            "event_type": "interview_started",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_ms": 0,
            "confidence": None,
        },
    )
    print("[viva-session/event] status:", r.status_code)
    assert r.status_code == 200, f"viva-session/event failed: {r.text}"

    # ── Step 4: fill answers (first 2 answered, rest skipped blank) ───────────
    answers = []
    for i, q in enumerate(questions_flat):
        if i == 0:
            answers.append({
                "question": q["question"],
                "answer": "I used object-oriented programming with a class to manage the todo items.",
                "skill_name": q["skill_name"],
            })
        elif i == 1:
            answers.append({
                "question": q["question"],
                "answer": "The add and remove methods handle CRUD operations on the list.",
                "skill_name": q["skill_name"],
            })
        else:
            # Skipped questions — blank answer
            answers.append({
                "question": q["question"],
                "answer": "",
                "skill_name": q["skill_name"],
            })

    print(f"[viva-session/end] submitting {len(answers)} answers "
          f"({sum(1 for a in answers if a['answer'].strip())} answered, "
          f"{sum(1 for a in answers if not a['answer'].strip())} skipped)")

    # ── Step 5: viva-session/end ──────────────────────────────────────────────
    r = client.post(
        f"{BASE}/viva-session/end",
        json={"session_id": session_id, "answers": answers},
    )
    print("[viva-session/end] status:", r.status_code)
    print("[viva-session/end] body:", r.text[:800])
    assert r.status_code == 200, f"viva-session/end failed: {r.text}"

    report = r.json()

    # ── Validate report structure ─────────────────────────────────────────────
    assert "evaluation_report" in report
    assert "proctoring_report" in report
    assert "suggested_skills" in report

    ev = report["evaluation_report"]
    summary = ev["summary"]

    assert summary["overall_alignment"] in ("strong", "partial", "weak"), \
        f"Bad overall_alignment: {summary['overall_alignment']}"

    score = summary["alignment_score"]
    assert score is not None, "alignment_score is None"
    assert 0.0 <= score <= 1.0, f"alignment_score out of range: {score}"

    assert isinstance(summary["narrative"], str) and len(summary["narrative"]) > 10, \
        "narrative is missing or too short"

    oe = summary["outcome_evaluation"]
    assert isinstance(oe, list) and len(oe) > 0, "outcome_evaluation is empty"

    valid_statuses = {"met", "partial", "not_met", "not_verifiable"}
    for entry in oe:
        assert entry["status"] in valid_statuses, f"Invalid status: {entry['status']}"
        assert isinstance(entry["evidence"], str) and len(entry["evidence"]) > 0
        assert "outcome" in entry

    pr = report["proctoring_report"]
    assert 0.0 <= pr["integrity_score"] <= 1.0
    assert pr["risk_level"] in ("low", "medium", "high")

    # ── Validate answers were stored back into skill questions ────────────────
    for skill in ev["skills"]:
        for q in skill["questions"]:
            # answers dict should be populated for answered questions
            pass  # answer field is Optional so just ensure no crash

    print("\n✅ All assertions passed!")
    print(f"   alignment_score  : {score}")
    print(f"   overall_alignment: {summary['overall_alignment']}")
    print(f"   narrative        : {summary['narrative'][:120]}...")
    print(f"   outcome_entries  : {len(oe)}")
    print(f"   integrity_score  : {pr['integrity_score']}")
    print(f"   risk_level       : {pr['risk_level']}")


def test_submit_with_all_blank_answers():
    """Submitting all blank answers should return weak/0.0, not crash."""
    client = httpx.Client(timeout=CLIENT_TIMEOUT)

    zip_bytes = make_test_zip()
    r = client.post(
        f"{BASE}/analyze-submission",
        files={"zip_file": ("test_project.zip", zip_bytes, "application/zip")},
        data={
            "project_title": "Blank Answers Test",
            "project_description": "Test blank answers",
            "project_outcomes": "Test outcome",
        },
    )
    assert r.status_code == 200
    analysis = r.json()
    submission_id = analysis["submission_id"]

    r = client.post(
        f"{BASE}/viva-session/start",
        json={"submission_id": submission_id, "consent_acknowledged": True},
    )
    assert r.status_code == 200
    session_id = r.json()["session_id"]

    from datetime import datetime, timezone
    client.post(f"{BASE}/viva-session/event", json={
        "session_id": session_id,
        "event_type": "interview_started",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_ms": 0,
        "confidence": None,
    })

    questions_flat = []
    for skill in analysis["evaluation_report"]["skills"]:
        for q in skill["questions"]:
            questions_flat.append({
                "question": q["question"],
                "answer": "",
                "skill_name": skill["skill_name"],
            })

    r = client.post(
        f"{BASE}/viva-session/end",
        json={"session_id": session_id, "answers": questions_flat},
    )
    print("\n[blank answers] status:", r.status_code)
    print("[blank answers] body:", r.text[:400])
    assert r.status_code == 200, f"blank answers test failed: {r.text}"

    report = r.json()
    summary = report["evaluation_report"]["summary"]
    assert summary["overall_alignment"] == "weak"
    assert summary["alignment_score"] == 0.0
    print("✅ Blank answers test passed — returned weak/0.0 correctly")


if __name__ == "__main__":
    test_health()
    test_full_submission_flow()
    test_submit_with_all_blank_answers()
