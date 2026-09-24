"use strict";
// TriageLine Live Assistant — phone PWA.
// mic → (on-device SpeechRecognition | server Whisper) → /say → agent → SSE → bubbles + TTS.
// Barge-in: pressing the mic (or typing) while the assistant speaks or works stops playback,
// discards queued obsolete speech, and sends the utterance as an `interruption`.

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const chat = $("#chat"), statusEl = $("#status"), micBtn = $("#mic");

let SID = null, ES = null, speaking = false, listening = false, rec = null, speechQ = [], tasks = {}, log = [];
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const opt = { tts: () => $("#m-tts").checked, hands: () => $("#m-hands").checked, serverAsr: () => $("#m-server-asr").checked || !SR };
if (!SR) { $("#m-server-asr").checked = true; $("#m-server-asr").disabled = true; }

function setStatus(t, cls = "") { statusEl.textContent = t; statusEl.className = "status small " + (cls || "muted"); }
function scroll() { chat.scrollTop = chat.scrollHeight; }
function bubble(cls, html) {
  const d = document.createElement("div"); d.className = "msg " + cls; d.innerHTML = html; chat.appendChild(d); scroll(); return d;
}

async function post(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const j = await r.json().catch(() => ({ error: "bad response" }));
  if (!r.ok) { const e = new Error(j.error || r.statusText); e.code = j.code; throw e; }
  return j;
}

// ------------------------------------------------------------------ session
async function start() {
  try {
    if (ES) ES.close();
    const j = await post("/api/live/start");
    SID = j.sid; $("#mode").textContent = j.mode; tasks = {}; renderTasks();
    sessionStorage.setItem("tl_sid", SID);
    connect();
  } catch (e) { setStatus("Could not start session: " + e.message, "neg"); setTimeout(start, 3000); }
}
function connect() {
  ES = new EventSource(`/api/live/${SID}/stream`);
  ES.onopen = () => setStatus(navigator.onLine ? "Connected · tap the mic to talk" : "Offline");
  ES.onmessage = (m) => onEvent(JSON.parse(m.data));
  ES.onerror = () => { setStatus("Reconnecting…"); };
}
window.addEventListener("online", () => { setStatus("Back online — reconnecting…"); if (ES) ES.close(); connect(); });
window.addEventListener("offline", () => setStatus("You're offline", "neg"));
document.addEventListener("visibilitychange", () => { if (document.hidden) { stopListening(); stopSpeech(); } });

function onEvent(ev) {
  log.push(ev);
  if (ev.kind === "say") {
    const cls = { filler_speech: "filler", clarification_request: "clar", final_response: "bot" }[ev.action] || "bot";
    const slots = ev.state && ev.state.slots && Object.keys(ev.state.slots).length ? ev.state.slots : null;
    const b = bubble(cls, esc(ev.text) + (cls !== "filler" && slots ? `<span class="snap">${esc(fmtSlots(slots))}</span>` : ""));
    if (ev.action === "clarification_request") markAwaiting();
    speak(ev.text, b, ev.action);
  } else if (ev.kind === "task") {
    const t = tasks[ev.call_id] || (tasks[ev.call_id] = { id: ev.call_id, api: ev.api });
    if (ev.api) t.api = ev.api;
    if (ev.status !== "cancel_noop") t.status = ev.status;
    if (ev.status === "cancelled") bubble("sys", `stopped ${esc(label(t.api))}`);
    renderTasks();
  } else if (ev.kind === "asr") {
    $("#interim").textContent = ev.text ? "" : (ev.error || "");
    if (!ev.text) setStatus(ev.error || "Didn't catch that", "neg");
  } else if (ev.kind === "user" && ev.as_ === "video_frame") {
    bubble("user", `<span class="tag">camera</span>frame sent`);
  } else if (ev.kind === "closed") {
    setStatus("Session ended");
  }
}
const fmtSlots = (s) => Object.entries(s).map(([k, v]) => `${k}: ${v}`).join(" · ");
const label = (api) => ({ flight_search: "Searching flights", book_flight: "Booking (simulated)", cancel_booking: "Cancelling booking (simulated)",
  lookup_manual: "Checking manual", create_support_ticket: "Opening ticket (simulated)" }[api] || (api || "task").replace(/_/g, " "));
function markAwaiting() { tasks["_await"] = { id: "_await", api: "your answer", status: "await" }; renderTasks(); }
function renderTasks() {
  const list = Object.values(tasks).slice(-6);
  $("#tasks").innerHTML = list.map((t) => `<span class="task ${t.status}"><b>${esc(t.status === "await" ? "Awaiting your answer" : label(t.api))}</b> ${
    { running: "", done: "· completed", cancelled: "· cancelled", error: "· failed" }[t.status] || ""}</span>`).join("");
}

// ------------------------------------------------------------------ speech output
function speak(text, el, action) {
  if (!opt.tts() || !("speechSynthesis" in window)) return;
  speechQ.push({ text, el, action });
  if (!speaking) nextSpeech();
}
function nextSpeech() {
  const item = speechQ.shift();
  if (!item) { speaking = false; micBtn.classList.remove("speaking"); if (opt.hands() && !listening) startListening(); return; }
  speaking = true; micBtn.classList.add("speaking");
  const u = new SpeechSynthesisUtterance(item.text);
  u.rate = 1.05; u.lang = "en-US";
  item.el.classList.add("speaking");
  const done = () => { item.el.classList.remove("speaking"); nextSpeech(); };
  u.onend = done; u.onerror = done;
  speechSynthesis.speak(u);
}
function stopSpeech() {
  speechQ = [];                          // invalidate queued obsolete speech
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  document.querySelectorAll(".msg.speaking").forEach((e) => e.classList.remove("speaking"));
  const was = speaking; speaking = false; micBtn.classList.remove("speaking");
  return was;
}
const busy = () => Object.values(tasks).some((t) => t.status === "running");

// ------------------------------------------------------------------ user input
async function sendText(text, wasSpeaking) {
  text = (text || "").trim(); if (!text || !SID) return;
  const barge = wasSpeaking || busy();
  delete tasks["_await"]; renderTasks();
  bubble("user" + (barge ? " barge" : ""), (barge ? `<span class="tag">barge-in</span>` : "") + esc(text));
  try { await post(`/api/live/${SID}/say`, { text, speaking: wasSpeaking }); }
  catch (e) { if (e.code === "no_session") { bubble("sys", "session expired — starting a new one"); await start(); } else bubble("sys", "send failed: " + esc(e.message)); }
}
$("#form").onsubmit = (e) => { e.preventDefault(); const was = stopSpeech(); sendText($("#text").value, was); $("#text").value = ""; };

micBtn.onclick = () => { if (listening) stopListening(); else startListening(); };
let wasSpeakingAtStart = false;
async function startListening() {
  wasSpeakingAtStart = stopSpeech();      // barge-in: stop current speech the instant the user takes the floor
  if (opt.serverAsr()) return recordForServer();
  try {
    rec = new SR(); rec.lang = "en-US"; rec.interimResults = true; rec.continuous = false; rec.maxAlternatives = 1;
    let final = "";
    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i]; if (r.isFinal) final += r[0].transcript; else interim += r[0].transcript;
      }
      $("#interim").textContent = interim || final;
    };
    rec.onerror = (e) => { setStatus(e.error === "not-allowed" ? "Microphone permission denied — allow it in site settings" : "Mic: " + e.error, "neg"); };
    rec.onend = () => { listening = false; micBtn.classList.remove("listening"); $("#interim").textContent = ""; if (final.trim()) sendText(final, wasSpeakingAtStart); };
    rec.start(); listening = true; micBtn.classList.add("listening"); setStatus("Listening…");
  } catch (e) { setStatus("Speech recognition unavailable: " + e.message, "neg"); }
}
function stopListening() {
  if (rec) try { rec.stop(); } catch (_) {}
  if (mediaRec && mediaRec.state === "recording") mediaRec.stop();
  listening = false; micBtn.classList.remove("listening");
}

// server-side Whisper fallback (Firefox / no on-device recogniser)
let mediaRec = null;
async function recordForServer() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const chunks = []; mediaRec = new MediaRecorder(stream);
    mediaRec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    mediaRec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop()); listening = false; micBtn.classList.remove("listening");
      const blob = new Blob(chunks, { type: mediaRec.mimeType || "audio/webm" });
      const url = await new Promise((r) => { const f = new FileReader(); f.onload = () => r(f.result); f.readAsDataURL(blob); });
      setStatus("Transcribing on server…");
      try { await post(`/api/live/${SID}/audio`, { audio: url, speaking: wasSpeakingAtStart }); } catch (e) { setStatus("Upload failed: " + e.message, "neg"); }
    };
    mediaRec.start(); listening = true; micBtn.classList.add("listening"); setStatus("Recording… tap again to send");
    setTimeout(() => mediaRec && mediaRec.state === "recording" && mediaRec.stop(), 12000);
  } catch (e) { setStatus("Microphone unavailable: " + e.message, "neg"); }
}

// ------------------------------------------------------------------ camera
let camStream = null;
$("#camera").onclick = async () => {
  if (!navigator.mediaDevices?.getUserMedia) return $("#file").click();
  try {
    camStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment", width: { ideal: 1280 } } });
    $("#video").srcObject = camStream; $("#cam").classList.remove("hidden");
  } catch (e) { $("#file").click(); }
};
function closeCam() { if (camStream) camStream.getTracks().forEach((t) => t.stop()); camStream = null; $("#cam").classList.add("hidden"); }
$("#camclose").onclick = closeCam;
$("#snap").onclick = async () => {
  const v = $("#video"), c = document.createElement("canvas");
  const s = Math.min(1, 960 / (v.videoWidth || 960)); c.width = (v.videoWidth || 960) * s; c.height = (v.videoHeight || 540) * s;
  c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
  closeCam(); await sendFrame(c.toDataURL("image/jpeg", 0.85));
};
$("#file").onchange = async (e) => {
  const f = e.target.files[0]; if (!f) return;
  const img = new Image(); img.src = URL.createObjectURL(f); await img.decode();
  const c = document.createElement("canvas"); const s = Math.min(1, 960 / img.width); c.width = img.width * s; c.height = img.height * s;
  c.getContext("2d").drawImage(img, 0, 0, c.width, c.height); await sendFrame(c.toDataURL("image/jpeg", 0.85)); e.target.value = "";
};
async function sendFrame(url) {
  try { await post(`/api/live/${SID}/frame`, { image: url }); setStatus("Frame sent — now ask about it"); }
  catch (e) { setStatus("Frame upload failed: " + e.message, "neg"); }
}

// ------------------------------------------------------------------ menu
$("#menu").onclick = () => $("#sheet").classList.toggle("hidden");
$("#m-new").onclick = async () => { $("#sheet").classList.add("hidden"); stopSpeech(); if (SID) post(`/api/live/${SID}/end`).catch(() => {}); chat.querySelectorAll(".msg").forEach((m) => m.remove()); await start(); };
$("#m-export").onclick = async () => {
  let server = {}; try { server = await post(`/api/live/${SID}/log`); } catch (_) {}
  const blob = new Blob([JSON.stringify({ sid: SID, mode: $("#mode").textContent, exported: new Date().toISOString(), client_log: log, server }, null, 2)], { type: "application/json" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `triageline-session-${Date.now()}.json`; a.click();
};
$("#m-ready").onclick = async () => {
  const box = $("#ready"); box.innerHTML = "checking…";
  const row = (k, ok, note = "") => `<div><span>${esc(k)}</span><span class="${ok ? "y" : "n"}">${ok ? "ok" : "missing"}${note ? " · " + esc(note) : ""}</span></div>`;
  const out = [];
  out.push(row("On-device speech recognition", !!SR, SR ? "" : "server Whisper will be used"));
  out.push(row("Speech output (TTS)", "speechSynthesis" in window));
  out.push(row("Secure context (needed for mic/camera)", window.isSecureContext));
  try { const s = await navigator.mediaDevices.getUserMedia({ audio: true }); s.getTracks().forEach((t) => t.stop()); out.push(row("Microphone permission", true)); }
  catch (e) { out.push(row("Microphone permission", false, e.name)); }
  out.push(row("Camera API", !!navigator.mediaDevices?.getUserMedia));
  out.push(row("Server connection", ES && ES.readyState === 1));
  try {
    const r = await (await fetch("/api/ready")).json();
    out.push(row("Server ASR model", r.asr_loaded), row("Server CLIP vision", r.clip_loaded), row("OCR (tesseract)", r.tesseract));
    Object.entries(r.assets).forEach(([k, v]) => out.push(row(k, v)));
  } catch (e) { out.push(row("Server readiness", false, e.message)); }
  box.innerHTML = out.join("");
};

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
start();
