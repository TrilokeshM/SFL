"""Quick end-to-end verification — run directly with python."""
import httpx, io, zipfile
from datetime import datetime, timezone

BASE = "http://localhost:8000"

def make_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("main.py",
            "class TodoApp:\n"
            "    def __init__(self):\n"
            "        self.items = []\n"
            "    def add(self, item):\n"
            "        self.items.append(item)\n"
            "    def remove(self, item):\n"
            "        self.items.remove(item)\n"
            "    def list_all(self):\n"
            "        return self.items\n"
        )
        zf.writestr("requirements.txt", "fastapi\nuvicorn\npydantic\n")
    return buf.getvalue()

c = httpx.Client(timeout=180)

print("=" * 60)
print("STEP 1: analyze-submission")
r = c.post(f"{BASE}/analyze-submission",
    files={"zip_file": ("project.zip", make_zip(), "application/zip")},
    data={
        "project_title": "Todo App",
        "project_description": "A Python todo list app using OOP",
        "project_outcomes": "1. Implement CRUD operations\n2. Use OOP with classes\n3. Handle errors gracefully",
    })
print(f"  Status : {r.status_code}")
assert r.status_code == 200, f"FAILED: {r.text}"
data = r.json()
sid = data["submission_id"]
qs = []
for sk in data["evaluation_report"]["skills"]:
    for q in sk["questions"]:
        qs.append({"question": q["question"], "answer": "", "skill_name": sk["skill_name"]})
print(f"  submission_id : {sid}")
print(f"  questions     : {len(qs)}")

print("\nSTEP 2: viva-session/start")
r = c.post(f"{BASE}/viva-session/start",
    json={"submission_id": sid, "consent_acknowledged": True})
print(f"  Status : {r.status_code}")
assert r.status_code == 200, f"FAILED: {r.text}"
ssid = r.json()["session_id"]
print(f"  session_id : {ssid}")

print("\nSTEP 3: viva-session/event (interview_started)")
r = c.post(f"{BASE}/viva-session/event", json={
    "session_id": ssid,
    "event_type": "interview_started",
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "duration_ms": 0,
    "confidence": None,
})
print(f"  Status : {r.status_code}")
assert r.status_code == 200, f"FAILED: {r.text}"

print("\nSTEP 4: fill answers")
for i, q in enumerate(qs):
    if i == 0:
        q["answer"] = "I used a TodoApp class with add, remove, and list_all methods to handle CRUD."
    elif i == 1:
        q["answer"] = "OOP was applied by encapsulating all todo logic inside the TodoApp class."
    # rest stay blank (skipped)
answered = sum(1 for q in qs if q["answer"].strip())
print(f"  {answered} answered, {len(qs)-answered} skipped")

print("\nSTEP 5: viva-session/end")
r = c.post(f"{BASE}/viva-session/end",
    json={"session_id": ssid, "answers": qs})
print(f"  Status : {r.status_code}")
assert r.status_code == 200, f"FAILED: {r.text}"

rep = r.json()
s = rep["evaluation_report"]["summary"]
oe = s["outcome_evaluation"]
pr = rep["proctoring_report"]

print("\n" + "=" * 60)
print("EVALUATION RESULT")
print(f"  overall_alignment : {s['overall_alignment']}")
print(f"  alignment_score   : {s['alignment_score']}")
print(f"  narrative         : {s['narrative'][:150]}")
print(f"  outcome_entries   : {len(oe)}")
for o in oe:
    gap = f" | gap: {o['gap'][:50]}" if o.get("gap") else ""
    print(f"    [{o['status']:8s}] {o['outcome'][:55]}{gap}")

print("\nPROCTORING RESULT")
print(f"  integrity_score : {pr['integrity_score']}")
print(f"  risk_level      : {pr['risk_level']}")
print(f"  id_check        : {pr['id_check']}")

# Assertions
assert s["overall_alignment"] in ("strong","partial","weak"), "Bad alignment"
assert s["alignment_score"] is not None, "alignment_score is None"
assert 0.0 <= s["alignment_score"] <= 1.0, f"Score out of range: {s['alignment_score']}"
assert len(s["narrative"]) > 10, "Narrative too short"
assert len(oe) > 0, "No outcome evaluations"
for o in oe:
    assert o["status"] in ("met","partial","not_met","not_verifiable"), f"Bad status: {o['status']}"
assert 0.0 <= pr["integrity_score"] <= 1.0

print("\n" + "=" * 60)
print("ALL CHECKS PASSED ✅")
