# 🤖 Project Submission AI Analyzer

![Python Version](https://img.shields.io/badge/python-3.9+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.103+-green.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)

An intelligent, end-to-end evaluation pipeline that takes a student's project codebase and transforms it into a mentor-ready assessment. It deeply analyzes the code, maps it to a skills catalog, generates highly contextual interview questions, and conducts an AI-proctored live Viva session.

## ✨ Features

- **Codebase Analysis**: Safely extracts and analyzes student project submissions.
- **Skill Mapping Engine**: Automatically suggests relevant skills based on project context using LLMs.
- **Dynamic Viva Generation**: Generates contextual, code-grounded interview questions.
- **Live Proctored Viva**: 
  - Real-time client-side face detection using `face-api.js`
  - Multi-axis head pose estimation (Yaw/Pitch) to detect off-screen gazes
  - Tab visibility, fullscreen exit, and clipboard event tracking
- **Comprehensive Reporting**: Generates a unified JSON report combining code evaluation, interview performance, and proctoring integrity scores.

---

## 🚀 Getting Started

### Prerequisites
- Python 3.9+
- A modern browser with webcam support (Chrome/Edge recommended)
- Anthropic API Key (for LLM capabilities)

### Installation

1. **Clone and setup the virtual environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows use: venv\Scripts\activate
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment Variables:**
   ```bash
   cp .env.example .env
   ```
   *Open `.env` and configure your `ANTHROPIC_API_KEY` along with any desired threshold limits.*

### Running the Application

Start the backend server:
```bash
uvicorn app.main:app --reload --port 8000
```
Open **[http://localhost:8000](http://localhost:8000)** in your browser to access the Web UI.

---

## 🏗️ Architecture & Core Components

```text
app/
├── main.py            # FastAPI Application and Routes
├── config.py          # Environment configuration & thresholds
├── schemas.py         # Pydantic models for validation
├── zip_analyzer.py    # Secure ZIP extraction & evidence compilation
├── skill_engine.py    # LLM logic for skill matching & questioning
├── llm_client.py      # Anthropic API wrapper
└── proctoring.py      # Session lifecycle & integrity watchdog
static/
├── index.html         # Application frontend
├── style.css          # UI styling (Tailwind based)
└── app.js             # Client-side routing, face detection, & proctoring
data/
└── skill_catalog.json # Project skill definitions
```

---

## 🛡️ Proctoring & Privacy Design

We prioritize user privacy while maintaining exam integrity:
- **Client-Side Only**: Camera frames and audio **never** leave the browser. All face tracking runs locally using `face-api.js`.
- **Event-Driven**: The client only sends discrete, timestamped telemetry events (e.g., `gaze_off_screen`, `tab_switched`) to the server.
- **Server-Side Watchdog**: If a client stops sending heartbeats (due to disabled cameras or lost connections), the server automatically injects a `connection_lost` penalty.

---

## 🔌 API Endpoints

### 1. Analyze Submission
Upload a project and generate questions.
```bash
curl -X POST http://localhost:8000/analyze-submission \
  -F "project_title=Todo API" \
  -F "project_description=FastAPI todo service" \
  -F "project_outcomes=Build a REST API" \
  -F "questions_per_skill=2" \
  -F "zip_file=@sample_test.zip"
```

### 2. Manage Viva Session
Track proctoring events during the interview.
```bash
# Start Session
curl -X POST http://localhost:8000/viva-session/start \
  -H "Content-Type: application/json" \
  -d '{"submission_id": "sub-xxxx", "consent_acknowledged": true}'

# Log Integrity Event
curl -X POST http://localhost:8000/viva-session/event \
  -H "Content-Type: application/json" \
  -d '{"session_id": "sess-xxxx", "event_type": "gaze_off_screen", "duration_ms": 1200}'

# End Session & Generate Report
curl -X POST http://localhost:8000/viva-session/end -F "session_id=sess-xxxx"
```

---

## 🧪 Testing

Run the test suite using pytest:
```bash
pytest tests/ -v
```

## 📝 License
This project is open-source and available under the MIT License.
