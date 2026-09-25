"use strict";
// TriageLine Live Assistant — Clinical Voice & Multimodal PWA
// Architecture:
//   mic → (on-device SpeechRecognition | server Whisper) → /say → agent → SSE → bubbles + TTS.
//   Barge-in: pressing the mic (or typing) while the assistant speaks or works halts playback,
//   discards obsolete queued speech, and dispatches the utterance as an `interruption`.

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const chat = $("#chat"), statusEl = $("#status"), statusDot = $("#status-dot"), micBtn = $("#mic"), waveform = $("#waveform-container");

let SID = null, ES = null, speaking = false, listening = false, rec = null, speechQ = [], tasks = {}, log = [];
let currentSlots = {};
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

const opt = {
  tts: () => $("#m-tts").checked,
  hands: () => $("#m-hands").checked,
  serverAsr: () => $("#m-server-asr").checked || !SR
};

if (!SR) {
  const srvCheckbox = $("#m-server-asr");
  if (srvCheckbox) {
    srvCheckbox.checked = true;
    srvCheckbox.disabled = true;
  }
}

function setStatus(text, cls = "") {
  if (statusEl) {
    statusEl.textContent = text;
    statusEl.className = "status small " + (cls || "muted");
  }
  if (statusDot) {
    statusDot.className = "status-indicator-dot" + (cls ? " " + cls : "");
  }
}

function setWaveform(active) {
  if (waveform) {
    waveform.classList.toggle("active", active);
  }
}

function scroll() {
  chat.scrollTop = chat.scrollHeight;
}

function bubble(cls, html) {
  const d = document.createElement("div");
  d.className = "msg " + cls;
  d.innerHTML = html;
  chat.appendChild(d);
  scroll();
  return d;
}

async function post(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {})
  });
  const j = await r.json().catch(() => ({ error: "Bad response from server" }));
  if (!r.ok) {
    const e = new Error(j.error || r.statusText);
    e.code = j.code;
    throw e;
  }
  return j;
}

// ------------------------------------------------------------------ Session Management
async function start() {
  try {
    if (ES) ES.close();
    setStatus("Initiating clinical session…", "connecting");
    const j = await post("/api/live/start");
    SID = j.sid;
    const modeBadge = $("#mode");
    if (modeBadge) modeBadge.textContent = j.mode;
    tasks = {};
    currentSlots = {};
    renderTasks();
    updateMemoryDeck({});
    sessionStorage.setItem("tl_sid", SID);
    connect();
  } catch (e) {
    setStatus("Session start failed: " + e.message, "neg");
    setTimeout(start, 3000);
  }
}

function connect() {
  ES = new EventSource(`/api/live/${SID}/stream`);
  ES.onopen = () => {
    setStatus(navigator.onLine ? "Connected · Assistant ready" : "Offline", navigator.onLine ? "" : "neg");
  };
  ES.onmessage = (m) => onEvent(JSON.parse(m.data));
  ES.onerror = () => {
    setStatus("Reconnecting stream…", "connecting");
  };
}

window.addEventListener("online", () => {
  setStatus("Network restored — reconnecting…", "connecting");
  if (ES) ES.close();
  connect();
});

window.addEventListener("offline", () => {
  setStatus("Device offline", "neg");
});

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopListening();
    stopSpeech();
  }
});

// ------------------------------------------------------------------ Event Stream Processing
function onEvent(ev) {
  log.push(ev);

  if (ev.kind === "say") {
    const cls = {
      filler_speech: "filler",
      clarification_request: "clar",
      final_response: "bot"
    }[ev.action] || "bot";

    const slots = ev.state && ev.state.slots && Object.keys(ev.state.slots).length ? ev.state.slots : null;
    if (slots) updateMemoryDeck(slots);

    const formattedSlots = slots ? `<span class="snap">${esc(fmtSlots(slots))}</span>` : "";
    const b = bubble(cls, esc(ev.text) + (cls !== "filler" ? formattedSlots : ""));
    if (ev.action === "clarification_request") markAwaiting();
    speak(ev.text, b, ev.action);
  } else if (ev.kind === "task") {
    const t = tasks[ev.call_id] || (tasks[ev.call_id] = { id: ev.call_id, api: ev.api });
    if (ev.api) t.api = ev.api;
    if (ev.status !== "cancel_noop") t.status = ev.status;
    if (ev.status === "cancelled") {
      bubble("sys", `Interrupted & halted ${esc(label(t.api))}`);
    }
    renderTasks();
  } else if (ev.kind === "asr") {
    const interimEl = $("#interim");
    if (interimEl) interimEl.textContent = ev.text ? "" : (ev.error || "");
    if (!ev.text) setStatus(ev.error || "Speech not recognized", "neg");
  } else if (ev.kind === "user" && ev.as_ === "video_frame") {
    bubble("user", `<span class="tag">Camera Frame</span> Frame uploaded for analysis`);
  } else if (ev.kind === "closed") {
    setStatus("Session terminated");
    setWaveform(false);
  }
}

const fmtSlots = (s) => Object.entries(s).map(([k, v]) => `${k}: ${v}`).join(" · ");

const label = (api) => ({
  flight_search: "Flight Search",
  book_flight: "Booking Reservation",
  cancel_booking: "Cancelling Booking",
  lookup_manual: "Hardware Manual Lookup",
  create_support_ticket: "Opening Ticket",
  reserve_rental_car: "Rental Reservation",
  weather_lookup: "Weather Query"
}[api] || (api || "Task").replace(/_/g, " "));

function markAwaiting() {
  tasks["_await"] = { id: "_await", api: "Awaiting clarification", status: "await" };
  renderTasks();
}

function renderTasks() {
  const container = $("#tasks");
  if (!container) return;
  const list = Object.values(tasks).slice(-6);
  container.innerHTML = list.map((t) => `
    <span class="task ${t.status}">
      <b>${esc(t.status === "await" ? "Response Needed" : label(t.api))}</b>
      ${{ running: "· Processing", done: "· Completed", cancelled: "· Aborted", error: "· Failed" }[t.status] || ""}
    </span>
  `).join("");
}

function updateMemoryDeck(slots) {
  const deck = $("#memory-deck");
  const container = $("#memory-slots");
  if (!deck || !container) return;

  const entries = Object.entries(slots || {});
  if (!entries.length) {
    deck.classList.add("hidden");
    return;
  }

  deck.classList.remove("hidden");
  container.innerHTML = entries.map(([k, v]) => `
    <span class="memory-chip" title="${esc(k)}: ${esc(v)}">
      ${esc(k)}: <b>${esc(v)}</b>
    </span>
  `).join("");
}

// ------------------------------------------------------------------ Speech Output (TTS)
function speak(text, el, action) {
  if (!opt.tts() || !("speechSynthesis" in window)) return;
  speechQ.push({ text, el, action });
  if (!speaking) nextSpeech();
}

function nextSpeech() {
  const item = speechQ.shift();
  if (!item) {
    speaking = false;
    micBtn.classList.remove("speaking");
    setWaveform(listening);
    if (opt.hands() && !listening) startListening();
    return;
  }

  speaking = true;
  micBtn.classList.add("speaking");
  setWaveform(true);

  const u = new SpeechSynthesisUtterance(item.text);
  u.rate = 1.05;
  u.lang = "en-US";
  item.el.classList.add("speaking");

  const done = () => {
    item.el.classList.remove("speaking");
    nextSpeech();
  };

  u.onend = done;
  u.onerror = done;
  speechSynthesis.speak(u);
}

function stopSpeech() {
  speechQ = []; // Invalidate queued speech
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  document.querySelectorAll(".msg.speaking").forEach((e) => e.classList.remove("speaking"));
  const was = speaking;
  speaking = false;
  micBtn.classList.remove("speaking");
  setWaveform(listening);
  return was;
}

const busy = () => Object.values(tasks).some((t) => t.status === "running");

// ------------------------------------------------------------------ User Input & Barge-In
async function sendText(text, wasSpeaking) {
  text = (text || "").trim();
  if (!text || !SID) return;

  const barge = wasSpeaking || busy();
  delete tasks["_await"];
  renderTasks();

  bubble(
    "user" + (barge ? " barge" : ""),
    (barge ? `<span class="tag">Barge-In Interrupt</span>` : "") + esc(text)
  );

  try {
    await post(`/api/live/${SID}/say`, { text, speaking: wasSpeaking });
  } catch (e) {
    if (e.code === "no_session") {
      bubble("sys", "Session expired — reconnecting fresh session…");
      await start();
    } else {
      bubble("sys", "Dispatch error: " + esc(e.message));
    }
  }
}

$("#form").onsubmit = (e) => {
  e.preventDefault();
  const input = $("#text");
  const was = stopSpeech();
  sendText(input.value, was);
  input.value = "";
};

micBtn.onclick = () => {
  if (listening) stopListening();
  else startListening();
};

let wasSpeakingAtStart = false;

async function startListening() {
  wasSpeakingAtStart = stopSpeech();
  if (opt.serverAsr()) return recordForServer();

  try {
    rec = new SR();
    rec.lang = "en-US";
    rec.interimResults = true;
    rec.continuous = false;
    rec.maxAlternatives = 1;

    let final = "";
    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) final += r[0].transcript;
        else interim += r[0].transcript;
      }
      const interimEl = $("#interim");
      if (interimEl) interimEl.textContent = interim || final;
    };

    rec.onerror = (e) => {
      setStatus(
        e.error === "not-allowed" ? "Microphone access denied — enable in browser settings" : "Mic error: " + e.error,
        "neg"
      );
    };

    rec.onend = () => {
      listening = false;
      micBtn.classList.remove("listening");
      setWaveform(speaking);
      const interimEl = $("#interim");
      if (interimEl) interimEl.textContent = "";
      if (final.trim()) sendText(final, wasSpeakingAtStart);
    };

    rec.start();
    listening = true;
    micBtn.classList.add("listening");
    setWaveform(true);
    setStatus("Listening… speak now");
  } catch (e) {
    setStatus("Speech recognition unavailable: " + e.message, "neg");
  }
}

function stopListening() {
  if (rec) {
    try { rec.stop(); } catch (_) {}
  }
  if (mediaRec && mediaRec.state === "recording") {
    mediaRec.stop();
  }
  listening = false;
  micBtn.classList.remove("listening");
  setWaveform(speaking);
  const interimEl = $("#interim");
  if (interimEl) interimEl.textContent = "";
}

// Server-side Whisper fallback (Firefox / Safari without speech API)
let mediaRec = null;
async function recordForServer() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const chunks = [];
    mediaRec = new MediaRecorder(stream);
    mediaRec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    mediaRec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      listening = false;
      micBtn.classList.remove("listening");
      setWaveform(speaking);
      const blob = new Blob(chunks, { type: mediaRec.mimeType || "audio/webm" });
      const url = await new Promise((resolve) => {
        const f = new FileReader();
        f.onload = () => resolve(f.result);
        f.readAsDataURL(blob);
      });
      setStatus("Transcribing with Whisper on server…", "connecting");
      try {
        await post(`/api/live/${SID}/audio`, { audio: url, speaking: wasSpeakingAtStart });
      } catch (e) {
        setStatus("Audio upload failed: " + e.message, "neg");
      }
    };
    mediaRec.start();
    listening = true;
    micBtn.classList.add("listening");
    setWaveform(true);
    setStatus("Recording audio… tap mic to finish");
    setTimeout(() => {
      if (mediaRec && mediaRec.state === "recording") mediaRec.stop();
    }, 12000);
  } catch (e) {
    setStatus("Microphone access failed: " + e.message, "neg");
  }
}

// ------------------------------------------------------------------ Camera & Visual Inspection
let camStream = null;

$("#camera").onclick = async () => {
  if (!navigator.mediaDevices?.getUserMedia) return $("#file").click();
  try {
    camStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "environment", width: { ideal: 1280 } }
    });
    $("#video").srcObject = camStream;
    $("#cam").classList.remove("hidden");
  } catch (e) {
    $("#file").click();
  }
};

function closeCam() {
  if (camStream) {
    camStream.getTracks().forEach((t) => t.stop());
  }
  camStream = null;
  $("#cam").classList.add("hidden");
}

$("#camclose").onclick = closeCam;

$("#snap").onclick = async () => {
  const laser = $("#cam-laser");
  if (laser) laser.classList.add("scanning");

  setTimeout(async () => {
    const v = $("#video");
    const c = document.createElement("canvas");
    const s = Math.min(1, 960 / (v.videoWidth || 960));
    c.width = (v.videoWidth || 960) * s;
    c.height = (v.videoHeight || 540) * s;
    c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
    if (laser) laser.classList.remove("scanning");
    closeCam();
    await sendFrame(c.toDataURL("image/jpeg", 0.85));
  }, 350);
};

// Direct Sample Port Frame Button
const loadSampleBtn = $("#load-sample-cam");
if (loadSampleBtn) {
  loadSampleBtn.onclick = async () => {
    closeCam();
    bubble("sys", "Loaded sample hardware frame (HDMI port) from scenarios");
    try {
      const res = await fetch("/sample_port.png");
      const blob = await res.blob();
      const reader = new FileReader();
      reader.onload = async () => {
        await sendFrame(reader.result);
      };
      reader.readAsDataURL(blob);
    } catch (e) {
      setStatus("Failed to load sample: " + e.message, "neg");
    }
  };
}

$("#file").onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const img = new Image();
  img.src = URL.createObjectURL(f);
  await img.decode();
  const c = document.createElement("canvas");
  const s = Math.min(1, 960 / img.width);
  c.width = img.width * s;
  c.height = img.height * s;
  c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
  await sendFrame(c.toDataURL("image/jpeg", 0.85));
  e.target.value = "";
};

async function sendFrame(url) {
  try {
    setStatus("Uploading camera frame…", "connecting");
    await post(`/api/live/${SID}/frame`, { image: url });
    setStatus("Frame uploaded — now ask a question about it");
  } catch (e) {
    setStatus("Frame transmission failed: " + e.message, "neg");
  }
}

// ------------------------------------------------------------------ Menu & Session Controls
$("#menu").onclick = () => $("#sheet").classList.toggle("hidden");

const sheetClose = $("#sheet-close");
if (sheetClose) {
  sheetClose.onclick = () => $("#sheet").classList.add("hidden");
}

// Sample prompt chips in hello card
$$(".sample-chip").forEach((btn) => {
  btn.onclick = () => {
    const text = btn.dataset.sample;
    if (text) {
      const was = stopSpeech();
      sendText(text, was);
    }
  };
});

$("#m-new").onclick = async () => {
  $("#sheet").classList.add("hidden");
  stopSpeech();
  if (SID) post(`/api/live/${SID}/end`).catch(() => {});
  chat.querySelectorAll(".msg").forEach((m) => m.remove());
  await start();
};

$("#m-export").onclick = async () => {
  let server = {};
  try {
    server = await post(`/api/live/${SID}/log`);
  } catch (_) {}

  const payload = {
    sid: SID,
    mode: $("#mode").textContent,
    exported_at: new Date().toISOString(),
    client_events: log,
    server_diagnostics: server
  };

  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `triageline-session-${Date.now()}.json`;
  a.click();
};

$("#m-ready").onclick = async () => {
  const box = $("#ready");
  box.innerHTML = "<div style='color:var(--mut)'>Auditing hardware, models & assets…</div>";

  const row = (k, ok, note = "") => `
    <div>
      <span>${esc(k)}</span>
      <span class="${ok ? "y" : "n"}">${ok ? "✓ Ready" : "✕ Missing"}${note ? " · " + esc(note) : ""}</span>
    </div>`;

  const out = [];
  out.push(row("Web Speech Recognition", !!SR, SR ? "On-device active" : "Using server Whisper fallback"));
  out.push(row("Speech Synthesis (TTS)", "speechSynthesis" in window));
  out.push(row("Secure Context (HTTPS/Local)", window.isSecureContext));

  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    s.getTracks().forEach((t) => t.stop());
    out.push(row("Microphone Permission", true));
  } catch (e) {
    out.push(row("Microphone Permission", false, e.name));
  }

  out.push(row("Camera Media Devices", !!navigator.mediaDevices?.getUserMedia));
  out.push(row("Server Stream (SSE)", ES && ES.readyState === 1));

  try {
    const r = await (await fetch("/api/ready")).json();
    out.push(
      row("Faster-Whisper ASR Model", r.asr_loaded),
      row("CLIP Vision Model", r.clip_loaded),
      row("Tesseract OCR Binary", r.tesseract)
    );
    if (r.assets) {
      Object.entries(r.assets).forEach(([k, v]) => out.push(row(k, v)));
    }
  } catch (e) {
    out.push(row("Server Readiness API", false, e.message));
  }

  box.innerHTML = out.join("");
};

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}

start();
