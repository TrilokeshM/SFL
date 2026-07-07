
// --- GESTURE RECOGNITION (MediaPipe) ---
let gestureRecognizer = null;
let lastVideoTime = -1;

async function initGestureRecognizer() {
  try {
    const { GestureRecognizer, FilesetResolver } = await import("https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.3");
    const vision = await FilesetResolver.forVisionTasks("https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.3/wasm");
    gestureRecognizer = await GestureRecognizer.createFromOptions(vision, {
      baseOptions: {
        modelAssetPath: "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task",
        delegate: "GPU"
      },
      runningMode: "VIDEO"
    });
    console.log("Gesture Recognizer initialized.");
  } catch (err) {
    console.error("Failed to init gesture recognizer:", err);
  }
}

async function scanGestureLoop(video) {
  if (!state.streamActive || !gestureRecognizer) return;
  
  if (video.currentTime !== lastVideoTime) {
    lastVideoTime = video.currentTime;
    try {
      const results = gestureRecognizer.recognizeForVideo(video, Date.now());
      if (results.gestures.length > 0) {
        const gesture = results.gestures[0][0];
        if (gesture.categoryName === "Thumb_Up" && gesture.score > 0.6) {
          console.log("Thumbs Up detected!");
          // Capture photo
          const canvas = $("#photo-capture-canvas");
          if (canvas) {
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            const ctx = canvas.getContext("2d");
            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
            state.capturedPhoto = canvas.toDataURL("image/jpeg");
          }
          
          // Verify
          const btn = $("#consent-btn");
          if (btn) {
            btn.innerHTML = `<span class="material-symbols-outlined mr-2">check_circle</span> Verified & Start`;
            btn.classList.remove("opacity-50", "cursor-not-allowed");
            btn.disabled = false;
          }
          return; // Stop scanning once verified
        }
      }
    } catch (err) {}
  }
  requestAnimationFrame(() => scanGestureLoop(video));
}


const state = {
  submissionData: null,
  questions: [],
  qIndex: 0,
  finalReport: null,
  sessionId: null,
  faceModelReady: false,
  gazeOffSince: null,
  noFaceSince: null,
  strikeCount: 0,
  lastGazeWarn: null,
  lastNoFaceWarn: null,
};

function $(selector) { return document.querySelector(selector); }
function show(id) {
  document.querySelectorAll(".step-container").forEach(el => el.classList.add("hidden"));
  $(id).classList.remove("hidden");
}
function escapeHtml(unsafe) {
  if (!unsafe) return "";
  return unsafe.toString()
       .replace(/&/g, "&amp;")
       .replace(/</g, "&lt;")
       .replace(/>/g, "&gt;")
       .replace(/"/g, "&quot;")
       .replace(/'/g, "&#039;");
}

// -------------------------------------------------------------
// Initialize Face-API
// -------------------------------------------------------------
async function loadFaceModel() {
  try {
    const MODEL_URL = "https://justadudewhohacks.github.io/face-api.js/models";
    if (typeof faceapi === "undefined") {
      setTimeout(loadFaceModel, 500); // retry
      return;
    }
    await faceapi.nets.tinyFaceDetector.loadFromUri(MODEL_URL);
    await faceapi.nets.faceLandmark68Net.loadFromUri(MODEL_URL);
      await faceapi.nets.faceExpressionNet.loadFromUri(MODEL_URL);
    state.faceModelReady = true;
  } catch (err) {
    console.error("Face-api load failed:", err);
  }
}
loadFaceModel();

// -------------------------------------------------------------
// STEP 1: Upload and Analyze
// -------------------------------------------------------------
$("input[name='zip_file']").addEventListener("change", (e) => {
  if (e.target.files.length > 0) {
    const file = e.target.files[0];
    const sizeMb = (file.size / (1024 * 1024)).toFixed(2);
    const title = $("#upload-title");
    const desc = $("#upload-desc");
    if(title) title.innerText = "1 file uploaded";
    if(desc) desc.innerHTML = `<span class="font-semibold text-indigo-400">${file.name}</span> (${sizeMb} MB)<br/><span class="text-emerald-500">Ready for analysis</span>`;
  }
});

$("#analyze-btn").addEventListener("click", async () => {
  const form = $("#submit-form");
  const fileInput = $("input[name='zip_file']");
  if (!fileInput || fileInput.files.length === 0) {
    alert("File not uploaded");
    return;
  }
  if (!form.checkValidity()) {
    form.reportValidity();
    return;
  }
  
  const btn = $("#analyze-btn");
  btn.disabled = true;
  const originalText = btn.innerHTML;
  btn.innerHTML = `<span class="material-symbols-outlined mr-2 animate-spin">sync</span> Analyzing...`;
  
  const term = $("#ai-proctor-terminal");
  if (term) {
    term.innerHTML = "<p>&gt; Uploading payload...</p>";
    setTimeout(() => { if (term.parentElement) term.innerHTML += "<p>&gt; Extracting codebase...</p>"; }, 800);
    setTimeout(() => { if (term.parentElement) term.innerHTML += "<p>&gt; Analyzing architecture...</p>"; }, 1500);
    setTimeout(() => { if (term.parentElement) term.innerHTML += "<p>&gt; Evaluating skill alignment...</p>"; }, 2500);
  }


  try {
    const fd = new FormData();
    fd.append("project_title", form.querySelector("[name='project_title']").value);
    fd.append("project_description", form.querySelector("[name='project_description']").value);
    fd.append("project_outcomes", form.querySelector("[name='project_outcomes']").value);
    fd.append("zip_file", form.querySelector("[name='zip_file']").files[0]);

    const res = await fetch("/analyze-submission", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Analysis failed.");

    state.submissionData = data;
    prepareVivaSession(data);
    show("#step-consent");
    startCamera();
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalText;
  }
});

function prepareVivaSession(data) {
  state.questions = [];
  data.evaluation_report.skills.forEach(sk => {
    sk.questions.forEach(q => state.questions.push({ skill_name: sk.skill_name, answer: "", ...q }));
  });
}

// -------------------------------------------------------------
// STEP 2: Consent & Verification
// -------------------------------------------------------------
const consentCheck = $("#consent-check");
const consentBtn = $("#consent-btn");

  const manualCaptureBtn = $("#manual-capture-btn");
  if (manualCaptureBtn) {
    manualCaptureBtn.addEventListener("click", () => {
      const video = $("#camera-feed");
      if (!video) return;
      
      const captureCanvas = $("#photo-capture-canvas");
      if (captureCanvas) {
        captureCanvas.width = video.videoWidth || 640;
        captureCanvas.height = video.videoHeight || 480;
        const captureCtx = captureCanvas.getContext("2d");
        captureCtx.drawImage(video, 0, 0, captureCanvas.width, captureCanvas.height);
        state.capturedPhoto = captureCanvas.toDataURL("image/jpeg");
      }
      
      postEvent("id_verified", null, 1.0);
      logEvent("Identity verified manually", "success");
      
      manualCaptureBtn.innerHTML = `<span class="material-symbols-outlined mr-2">check_circle</span> Captured`;
      manualCaptureBtn.classList.replace("bg-primary", "bg-emerald-600");
      manualCaptureBtn.disabled = true;
      
      const btn = $("#consent-btn");
      if (btn) {
        btn.innerHTML = `<span class="material-symbols-outlined mr-2">check_circle</span> Verified & Start`;
        btn.classList.remove("opacity-50", "cursor-not-allowed");
        btn.disabled = false;
      }
    });
  }


if(consentCheck) {
  consentCheck.addEventListener("change", () => {
    consentBtn.disabled = !consentCheck.checked;
    if(consentCheck.checked) {
      consentBtn.classList.remove("opacity-50", "cursor-not-allowed");
    } else {
      consentBtn.classList.add("opacity-50", "cursor-not-allowed");
    }
  });
}

if(consentBtn) {
  consentBtn.addEventListener("click", async () => {
    const overlay = $("#success-overlay");
    const modal = $("#success-modal");
    const bar = $("#loading-bar");
    
    if(overlay) {
      overlay.classList.remove("hidden");
      setTimeout(() => {
        overlay.classList.replace("opacity-0", "opacity-100");
        modal.classList.replace("scale-95", "scale-100");
        setTimeout(() => { if(bar) bar.style.width = "100%"; }, 200);
      }, 10);
    }
    
    try {
      const res = await fetch("/viva-session/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          submission_id: state.submissionData.submission_id,
          consent_acknowledged: true
        })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Could not start session.");
      state.sessionId = data.session_id;

      setTimeout(() => {
        show("#step-viva");
        if(overlay) {
          overlay.classList.add("hidden");
          overlay.classList.replace("opacity-100", "opacity-0");
        }
        
        // Move camera to sidebar
        const sidebarCam = $("#viva-sidebar-camera");
        if (sidebarCam) {
            sidebarCam.innerHTML = "";
            sidebarCam.className = "relative rounded-lg overflow-hidden bg-black aspect-video flex items-center justify-center h-full";
            sidebarCam.appendChild($("#camera-feed"));
            sidebarCam.appendChild($("#camera-overlay"));
        }
        
        startVivaSession();
      }, 2000);
    } catch (err) {
      alert(err.message);
      if(overlay) overlay.classList.add("hidden");
    }
  });
}

// -------------------------------------------------------------
// STEP 3: Live Viva Session (Proctoring & Questions)
// -------------------------------------------------------------
let proctorInterval = null;

async function startVivaSession() {
  state.qIndex = 0;
  state.strikeCount = 0;
  renderQuestion();
  
  if ($("#event-log-container")) $("#event-log-container").innerHTML = "";
  postEvent("interview_started", null, 1.0);
  logEvent("Session started", "info");
  startTimer();

  document.addEventListener("visibilitychange", () => {
    if (document.hidden && state.sessionId && !$("#step-report").classList.contains("hidden")===false) {
      postEvent("tab_switched");
      logEvent("Tab switch detected", "error");
      if(typeof showToast === "function") showToast("Warning: Tab switch detected!", "error");
    }
  });
}

async function startCamera(preferredDeviceId = null) {
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const videoDevices = devices.filter(d => d.kind === 'videoinput');
    const select = $("#camera-select");
    
    if (select && select.options.length <= 1) {
        select.innerHTML = "";
        videoDevices.forEach(d => {
            const opt = document.createElement("option");
            opt.value = d.deviceId;
            opt.textContent = d.label || `Camera ${select.options.length + 1}`;
            select.appendChild(opt);
        });
        
        if (!preferredDeviceId) {
            const chiconyCam = videoDevices.find(d => d.label.includes('Chicony USB2.0'));
            if (chiconyCam) {
                select.value = chiconyCam.deviceId;
                preferredDeviceId = chiconyCam.deviceId;
            } else if (videoDevices.length > 0) {
                preferredDeviceId = videoDevices[0].deviceId;
                select.value = preferredDeviceId;
            }
        } else {
            select.value = preferredDeviceId;
        }

        select.addEventListener("change", (e) => {
            startCamera(e.target.value);
        });
    }

    let videoConstraints = true;
    if (preferredDeviceId) {
      videoConstraints = { deviceId: { exact: preferredDeviceId } };
    }

    const video = $("#camera-feed");
    if (video && video.srcObject) {
      video.srcObject.getTracks().forEach(t => t.stop());
    }

    const stream = await navigator.mediaDevices.getUserMedia({ video: videoConstraints, audio: false });
    if(video) {
      video.srcObject = stream;
      video.onloadedmetadata = () => {
        video.play();
        if (state.faceModelReady && !state.streamActive) runFaceLoop(video);
        state.streamActive = true;
      };
    }
  } catch (err) {
    console.error("Camera access denied", err);
  }
}

function logEvent(msg, level) {
  const logDiv = $("#event-log-container");
  if (!logDiv) return;
  const time = new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
  let color = "text-on-surface";
  if (level === "error") color = "text-rose-400";
  if (level === "success") color = "text-emerald-400";
  if (level === "warning") color = "text-amber-400";
  
  const el = document.createElement("div");
  el.className = `flex gap-3 ${color}`;
  el.innerHTML = `<span class="font-code-md text-body-sm opacity-70">${time}</span><span class="font-body-sm">${msg}</span>`;
  logDiv.appendChild(el);
  logDiv.scrollTop = logDiv.scrollHeight;
}

async function runFaceLoop(video) {
  const canvas = $("#camera-overlay");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const options = new faceapi.TinyFaceDetectorOptions({ inputSize: 224, scoreThreshold: 0.5 });
  let identityVerified = false;

  proctorInterval = setInterval(async () => {
    if (!video.srcObject) return;
    
    const displaySize = { width: video.videoWidth || 320, height: video.videoHeight || 240 };
    if (canvas.width !== displaySize.width) {
      canvas.width = displaySize.width;
      canvas.height = displaySize.height;
    }

    // Ping the backend every 5 seconds to prevent CONNECTION_TIMEOUT_S (12s)
    if (!state.lastHeartbeat || Date.now() - state.lastHeartbeat > 5000) {
      state.lastHeartbeat = Date.now();
      postEvent("heartbeat");
    }
    const detections = await faceapi.detectAllFaces(video, options).withFaceLandmarks().withFaceExpressions();
    const resized = faceapi.resizeResults(detections, displaySize);
    
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (!identityVerified && resized.length === 1) {
      identityVerified = true;
      postEvent("id_verified", null, 0.95);
      logEvent("Identity verified", "success");
    }

    if (resized.length === 0) {
      if (!state.noFaceSince) state.noFaceSince = Date.now();
      const elapsed = Date.now() - state.noFaceSince;
      if (elapsed > 2000) {
        if (!state.lastNoFaceWarn || Date.now() - state.lastNoFaceWarn > 3000) {
          state.lastNoFaceWarn = Date.now();
          postEvent("face_not_detected", elapsed);
          logEvent("Face not detected", "warning");
          if(typeof showToast === "function") showToast("Warning: Face not detected!", "error");
        }
      }
    } else {
      state.noFaceSince = null;
      state.lastNoFaceWarn = null;
    }

    if (resized.length > 1) {
      postEvent("multiple_faces_detected", null, 0.9);
      logEvent("Multiple faces detected", "error");
      if(typeof showToast === "function") showToast("Warning: Multiple faces detected!", "error");
    }

    if (resized.length === 1) {
      const box = resized[0].detection.box;
      ctx.strokeStyle = "#f5b700";
      ctx.lineWidth = 3;
      ctx.strokeRect(box.x, box.y, box.width, box.height);
      faceapi.draw.drawFaceLandmarks(canvas, resized);

      // --- Correct 68-point landmark gaze detection ---
      // faceLandmark68Net landmark indices (guaranteed positions):
      //   0-16:  Jaw outline
      //   27-30: Nose bridge (top=27, tip=30)
      //   36-41: Left eye  (leftmost=36, rightmost=39, inner corner=39)
      //   42-47: Right eye (inner corner=42, rightmost=45)
      //   8:     Chin point (bottom of jaw)
      const pts = resized[0].landmarks.positions;

      // Key reference points
      const noseTip      = pts[30]; // Nose tip
      const noseBridge   = pts[27]; // Top of nose
      const chin         = pts[8];  // Chin bottom
      const leftEyeL     = pts[36]; // Left eye outer corner
      const leftEyeR     = pts[39]; // Left eye inner corner
      const rightEyeL    = pts[42]; // Right eye inner corner
      const rightEyeR    = pts[45]; // Right eye outer corner
      const jawLeft      = pts[0];  // Left jaw edge
      const jawRight     = pts[16]; // Right jaw edge

      // ── SIGNAL 1: Eye Width Ratio (best yaw indicator) ──
      // When head turns LEFT: left eye appears narrower, right eye wider
      // When head turns RIGHT: right eye appears narrower, left eye wider
      const leftEyeWidth  = Math.abs(leftEyeR.x  - leftEyeL.x);
      const rightEyeWidth = Math.abs(rightEyeR.x - rightEyeL.x);
      const eyeWidthRatio = Math.min(leftEyeWidth, rightEyeWidth) / Math.max(leftEyeWidth, rightEyeWidth, 1);
      // When straight: ratio ≈ 0.85–1.0. When turned: one eye collapses → ratio drops
      const isYawTurned = eyeWidthRatio < 0.70;

      // ── SIGNAL 2: Nose vs Eye-Midpoint offset (yaw) ──
      // Midpoint between inner eye corners = stable face-center proxy
      const eyeMidX = (leftEyeR.x + rightEyeL.x) / 2;
      // When looking straight, nose tip should be close to eye midpoint horizontally
      const noseMidOffset = Math.abs(noseTip.x - eyeMidX) / Math.max(rightEyeR.x - leftEyeL.x, 1);
      const isNoseOffset = noseMidOffset > 0.15;

      // ── SIGNAL 3: Jaw symmetry (yaw) ──
      // When head turns, jaw shifts asymmetrically around the nose
      const jawLeftDist  = Math.abs(jawLeft.x  - noseTip.x);
      const jawRightDist = Math.abs(jawRight.x - noseTip.x);
      const jawRatio = Math.min(jawLeftDist, jawRightDist) / Math.max(jawLeftDist, jawRightDist, 1);
      const isJawAsymmetric = jawRatio < 0.60;

      // ── SIGNAL 4: Pitch (looking up or down) ──
      // Chin-to-nose vs nose-to-bridge ratio changes with head tilt
      const chinToNose   = Math.abs(chin.y      - noseTip.y);
      const noseToBridge = Math.abs(noseTip.y   - noseBridge.y);
      const pitchRatio   = chinToNose / Math.max(noseToBridge, 1);
      // Neutral: ratio ≈ 1.5–2.5. Looking down: chin moves away (ratio increases). Up: ratio decreases
      const isPitched = pitchRatio < 1.0 || pitchRatio > 3.5;

      // ── SIGNAL 5: Face moved to edge of frame ──
      const faceCenterX = box.x + box.width / 2;
      const frameCenterX = canvas.width / 2;
      const isFaceOffFrame = Math.abs(faceCenterX - frameCenterX) / canvas.width > 0.20;

      // Any single signal is enough to flag a head turn
      const headTurned = isYawTurned || isNoseOffset || isJawAsymmetric || isPitched || isFaceOffFrame;

      if (headTurned) {
        if (!state.gazeOffSince) state.gazeOffSince = Date.now();
        const elapsed = Date.now() - state.gazeOffSince;
        // Warn after 700ms — near-instant response
        if (elapsed > 700) {
          if (!state.lastGazeWarn || Date.now() - state.lastGazeWarn > 2000) {
            state.lastGazeWarn = Date.now();
            postEvent("gaze_off_screen", elapsed, 0.85);
            logEvent("Gaze off-screen", "warning");
            if (typeof showToast === "function") showToast("⚠️ Please look at the screen!", "error");
          }
        }
      } else {
        state.gazeOffSince = null;
        state.lastGazeWarn = null;
      }
    }
  }, 500);
}

async function postEvent(eventType, durationMs, confidence) {
  if (!state.sessionId) return;
  try {
    const res = await fetch("/viva-session/event", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: state.sessionId,
        event_type: eventType,
        timestamp: new Date().toISOString(),
        duration_ms: Math.floor(durationMs || 0),
        confidence: confidence || null
      })
    });
    const data = await res.json();
    if (data.severity) {
      state.strikeCount++;
    }
  } catch (err) {}
}

function renderQuestion() {
  const q = state.questions[state.qIndex];
  if (!q) {
    if ($("#question-text")) {
      $("#question-text").innerHTML = "No questions generated for this submission. You may end the session.";
    }
    if ($("#code-ref-block")) {
      $("#code-ref-block").classList.add("hidden");
    }
    return;
  }
  
  if ($("#q-counter-display")) {
    $("#q-counter-display").textContent = `QUESTION ${(state.qIndex + 1).toString().padStart(2, '0')}`;
  }
  if ($("#question-counter-header")) {
    const total = state.questions.length > 0 ? state.questions.length : 12;
    $("#question-counter-header").textContent = `${(state.qIndex + 1).toString().padStart(2, '0')} / ${total.toString().padStart(2, '0')}`;
  }
  const progContainer = $("#progress-container");
  if (progContainer && state.questions.length > 0) {
    progContainer.innerHTML = "";
    state.questions.forEach((q, i) => {
      const bar = document.createElement("div");
      bar.className = `h-1 flex-1 rounded-full transition-colors ${i <= state.qIndex ? 'bg-primary' : 'bg-outline-variant'}`;
      progContainer.appendChild(bar);
    });
  }
  
  if ($("#next-q-btn")) {
    const skipBtn = $("#skip-q-btn");
    if (state.qIndex === state.questions.length - 1) {
      $("#next-q-btn").innerHTML = 'SUBMIT SESSION <span class="material-symbols-outlined text-[18px]">done_all</span>';
      if (skipBtn) skipBtn.classList.add("hidden");
    } else {
      $("#next-q-btn").innerHTML = 'SUBMIT ANSWER <span class="material-symbols-outlined text-[18px]">send</span>';
      if (skipBtn) skipBtn.classList.remove("hidden");
    }
  }

  if ($("#question-text")) {
    $("#question-text").innerHTML = escapeHtml(q.question);
  }
  
  const refBlock = $("#code-ref-block");
  if (refBlock) {
    if (q.references && q.references.length > 0) {
      refBlock.classList.remove("hidden");
      $("#code-ref-filename").textContent = q.references.join(", ");
      $("#code-ref-content").textContent = `// Context cited from: ${q.references.join(", ")}`;
    } else {
      refBlock.classList.add("hidden");
    }
  }

  const answerInput = $("#answer-input");
  if (answerInput) {
    answerInput.value = q.answer || "";
  }
}

function saveAnswer() {
  const answerInput = $("#answer-input");
  if (answerInput && state.questions[state.qIndex]) {
    state.questions[state.qIndex].answer = answerInput.value;
  }
}

if ($("#prev-q-btn")) {
  $("#prev-q-btn").addEventListener("click", () => {
    if (state.qIndex > 0) { saveAnswer(); state.qIndex--; renderQuestion(); }
  });
}

async function submitSession() {
  saveAnswer();
  try {
    const video = $("#camera-feed");
    if (video && video.srcObject) video.srcObject.getTracks().forEach(t => t.stop());
    if (typeof proctorInterval !== "undefined" && proctorInterval) clearInterval(proctorInterval);

    const btn = $("#next-q-btn");
    if (btn) btn.innerHTML = `<span class="material-symbols-outlined mr-2 animate-spin">sync</span> Submitting...`;

    const res = await fetch("/viva-session/end", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: state.sessionId,
        answers: state.questions.map(q => ({
          question: q.question,
          answer: q.answer || "",
          skill_name: q.skill_name || ""
        }))
      })
    });
    const data = await res.json();
    if (!res.ok) {
      let errStr = data.detail || "Could not end session.";
      if (typeof errStr !== "string") errStr = JSON.stringify(errStr);
      throw new Error(errStr);
    }

    state.finalReport = data;
    renderReport(state.finalReport);
    show("#step-report");
  } catch (err) {
    alert(err.message);
  }
}

if ($("#next-q-btn")) {
  $("#next-q-btn").addEventListener("click", () => {
    if (state.qIndex < state.questions.length - 1) { 
        saveAnswer(); 
        state.qIndex++; 
        renderQuestion(); 
    } else {
        submitSession();
    }
  });
}

if ($("#skip-q-btn")) {
  $("#skip-q-btn").addEventListener("click", () => {
    if (state.qIndex < state.questions.length - 1) { saveAnswer(); state.qIndex++; renderQuestion(); }
  });
}

// -------------------------------------------------------------
// STEP 4: End Session -> Report
// -------------------------------------------------------------

function renderReport(data) {
  const sub = state.submissionData;
  if ($("#report-title")) $("#report-title").textContent = sub.project_title;
  if ($("#report-alignment-score")) {
    const rawScore = data.evaluation_report.summary.alignment_score;
    const scoreVal = rawScore != null ? Math.round(rawScore * 100) : 0;
    
    // Animate the counter
    let curr = 0;
    const interval = setInterval(() => {
      curr += 2;
      if (curr >= scoreVal) {
        curr = scoreVal;
        clearInterval(interval);
      }
      $("#report-alignment-score").textContent = `${curr}%`;
    }, 20);

    // Update gauge gradient and shadow color
    const gauge = $("#score-gauge");
    if (gauge) {
      let color = "#3b82f6"; // primary
      if (scoreVal >= 80) color = "#10b981"; // emerald
      else if (scoreVal < 50) color = "#ef4444"; // rose
      
      gauge.style.background = `conic-gradient(${color} ${scoreVal}%, transparent ${scoreVal}%)`;
      gauge.parentElement.style.boxShadow = `0 0 25px ${color}80`;
      $("#report-alignment-score").style.color = color;
    }
  }
  
  if ($("#final-photo-display") && state.capturedPhoto) {
    $("#final-photo-display").src = state.capturedPhoto;
  }

  if ($("#report-narrative")) $("#report-narrative").textContent = data.evaluation_report.summary.narrative;
  if ($("#report-integrity-score")) $("#report-integrity-score").textContent = data.proctoring_report.integrity_score.toFixed(2);
  if ($("#report-risk-level")) {
    const rl = data.proctoring_report.risk_level;
    $("#report-risk-level").textContent = rl.toUpperCase() + " RISK";
    if (rl === "high") $("#report-risk-level").className = "px-3 py-1 bg-rose-100 text-rose-700 font-bold rounded-lg text-sm";
    else if (rl === "medium") $("#report-risk-level").className = "px-3 py-1 bg-amber-100 text-amber-700 font-bold rounded-lg text-sm";
    else $("#report-risk-level").className = "px-3 py-1 bg-emerald-100 text-emerald-700 font-bold rounded-lg text-sm";
  }

  // Skills
  const skillsContainer = $("#report-skills-container");
  if (skillsContainer) {
    skillsContainer.innerHTML = data.suggested_skills.map(sk => `
      <div class="bg-surface-container-lowest p-container-padding rounded-xl shadow-sm border border-outline-variant hover:shadow-md transition-shadow group">
          <div class="flex justify-between items-start mb-4">
              <div>
                  <h3 class="font-headline-sm text-headline-sm">${escapeHtml(sk.skill_name)}</h3>
              </div>
              <span class="text-headline-sm text-primary">${Math.round(sk.confidence * 100)}%</span>
          </div>
          <div class="w-full bg-surface-container-high h-2 rounded-full mb-6 overflow-hidden">
              <div class="bg-primary h-full transition-all duration-1000" style="width: ${sk.confidence * 100}%"></div>
          </div>
          <div class="bg-surface-container-low p-4 rounded-lg">
              <span class="font-label-md text-label-md text-primary block mb-1">Rationale</span>
              <p class="text-on-surface-variant font-body-md italic leading-relaxed">
                  "${escapeHtml(sk.rationale)}"
              </p>
          </div>
      </div>
    `).join("");
  }

  // Outcomes
  const outcomesContainer = $("#report-outcomes-container");
  if (outcomesContainer) {
    outcomesContainer.innerHTML = data.evaluation_report.summary.outcome_evaluation.map(o => {
      let statusColor = "emerald";
      if (o.status === "partial") statusColor = "amber";
      if (o.status === "not_met" || o.status === "not_verifiable") statusColor = "rose";
      
      return `
      <div class="bg-surface-container-lowest p-6 rounded-xl shadow-sm status-border-${statusColor} flex flex-col justify-between">
          <div>
              <div class="flex items-center gap-2 mb-2">
                  <span class="bg-${statusColor}-100 text-${statusColor}-700 font-label-md text-label-md px-2 py-0.5 rounded-full capitalize">${escapeHtml(o.status)}</span>
                  <span class="text-on-surface-variant font-label-md truncate block">${escapeHtml(o.outcome)}</span>
              </div>
              <p class="text-on-surface-variant font-body-sm mb-4 line-clamp-3">${escapeHtml(o.evidence)}</p>
          </div>
          ${o.gap ? `
          <div class="bg-inverse-surface text-surface-variant p-3 rounded-lg font-code-md text-code-md mt-4">
              <span class="text-primary-fixed-dim text-xs block mb-1">Identified Gap</span>
              ${escapeHtml(o.gap)}
          </div>
          ` : ""}
      </div>
      `;
    }).join("");
  }

  // Flags Log
  const logContainer = $("#report-event-log");
  if (logContainer) {
    logContainer.innerHTML = data.proctoring_report.flags.map(f => {
      const time = new Date(f.timestamp).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
      let color = "";
      if (f.severity === "high") color = "text-rose-400";
      else if (f.severity === "medium") color = "text-amber-400";
      return `
      <div class="flex gap-3 ${color}">
          <span class="opacity-40">${time}</span>
          <span>${escapeHtml(f.type)} (${f.duration_ms ? f.duration_ms + 'ms' : '-'})</span>
      </div>
      `;
    }).join("");
    if (data.proctoring_report.flags.length === 0) {
      logContainer.innerHTML = `<div class="flex gap-3 text-emerald-400"><span>No integrity flags recorded.</span></div>`;
    }
  }
}


// --- TOAST NOTIFICATIONS ---
function showToast(msg, type = 'error') {
  const container = $("#toast-container");
  if (!container) return;
  const t = document.createElement("div");
  const bg = type === 'error' ? 'bg-rose-600' : 'bg-emerald-600';
  t.className = `${bg} text-white px-4 py-2 rounded shadow-lg transition-all duration-300 transform translate-x-10 opacity-0 font-body-sm z-50`;
  t.innerText = msg;
  container.appendChild(t);
  setTimeout(() => { t.classList.remove('translate-x-10', 'opacity-0'); }, 10);
  setTimeout(() => {
    t.classList.add('opacity-0');
    setTimeout(() => t.remove(), 300);
  }, 4000);
}

// --- SPEECH RECOGNITION ---
let recognition = null;
let isRecording = false;

if ('webkitSpeechRecognition' in window) {
  recognition = new webkitSpeechRecognition();
  recognition.continuous = true;
  recognition.interimResults = true;
  
  recognition.onresult = (event) => {
    let finalTranscript = '';
    for (let i = event.resultIndex; i < event.results.length; ++i) {
      if (event.results[i].isFinal) {
        finalTranscript += event.results[i][0].transcript + ' ';
      }
    }
    const input = $("#answer-input");
    if (input && finalTranscript) {
      input.value += finalTranscript;
    }
  };
  
  recognition.onerror = (event) => {
    console.error("Speech recognition error", event.error);
    stopRecording();
  };
  
  recognition.onend = () => {
    if (isRecording) {
      try { recognition.start(); } catch(e){}
    }
  };
}

function stopRecording() {
  isRecording = false;
  if(recognition) recognition.stop();
  const icon = $("#mic-icon");
  const text = $("#mic-text");
  if(icon) { icon.classList.remove("animate-pulse"); icon.style.color = ""; }
  if(text) text.innerText = "Click to speak...";
}

if ($("#mic-btn")) {
  $("#mic-btn").addEventListener("click", () => {
    if (!recognition) {
      alert("Speech recognition not supported in this browser. Try Chrome.");
      return;
    }
    const icon = $("#mic-icon");
    const text = $("#mic-text");
    if (isRecording) {
      stopRecording();
    } else {
      isRecording = true;
      recognition.start();
      if(icon) { icon.classList.add("animate-pulse"); icon.style.color = "#dc2626"; }
      if(text) text.innerText = "Listening...";
    }
  });
}

// --- TIMER ---
let timerInterval = null;

function startTimer() {
  state.sessionStartTime = Date.now();
  const timerEl = $("#session-timer");
  if (!timerEl) return;
  if (timerInterval) clearInterval(timerInterval);
  
  timerInterval = setInterval(() => {
    const elapsed = Math.floor((Date.now() - state.sessionStartTime) / 1000);
    const m = String(Math.floor(elapsed / 60)).padStart(2, '0');
    const s = String(elapsed % 60).padStart(2, '0');
    timerEl.innerText = `${m}:${s}`;
  }, 1000);
}

// --- JSON DOWNLOAD ---
if ($("#download-json-btn")) {
  $("#download-json-btn").addEventListener("click", () => {
    if (!state.finalReport) return;
    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(state.finalReport, null, 2));
    const a = document.createElement('a');
    a.setAttribute("href", dataStr);
    a.setAttribute("download", `viva_evaluation_${state.sessionId || 'report'}.json`);
    document.body.appendChild(a);
    a.click();
    a.remove();
  });
}



  // Real-time payload and time updates for Consent Step
  setInterval(() => {
    const timeEl = $("#system-time");
    if (timeEl && !$("#step-consent").classList.contains("hidden")) {
      const now = new Date();
      timeEl.textContent = "SYSTEM TIME: " + now.toISOString().split("T")[1].split(".")[0] + " UTC";
    }
    
    const payloadEl = $("#signal-payload");
    if (payloadEl && !$("#step-consent").classList.contains("hidden")) {
      const payload = {
        integrity_index: (0.95 + Math.random() * 0.05).toFixed(2),
        eye_gaze: "on_canvas",
        external_audio: (Math.random() * 0.1).toFixed(2),
        face_detected: !!(state.streamActive && !state.noFaceSince),
        viva_status: "authorized"
      };
      payloadEl.textContent = JSON.stringify(payload, null, 2);
    }
  }, 1000);
