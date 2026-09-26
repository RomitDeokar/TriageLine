"use strict";
// TriageLine Live Assistant — mobile PWA client.
//   mic → (on-device SpeechRecognition | server Whisper) → POST /say → agent → SSE → bubbles + TTS.
//   Barge-in: mic/typing while the assistant speaks or works halts playback, drops queued speech and
//   the utterance is dispatched as an `interruption` (server also upgrades to interruption when busy).
//   Stream: SSE with event ids; reconnects resume via Last-Event-ID and the client de-duplicates.

(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const chat = $("#chat"), statusEl = $("#status"), statusDot = $("#status-dot"), micBtn = $("#mic"),
    waveform = $("#waveform-container"), input = $("#text"), sendBtn = $("#send-btn"), connLabel = $("#conn-label");
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const HAS_TTS = "speechSynthesis" in window;
  const MAX_BUBBLES = 300;

  const state = {
    sid: null, es: null, lastId: 0, seen: new Set(), starting: null, reconnectTimer: null, backoff: 1000,
    speaking: false, listening: false, rec: null, mediaRec: null, recTimer: null, speechQ: [],
    tasks: {}, log: [], slots: {}, wasSpeakingAtStart: false, pendingSendAt: 0,
    audio: { provider: "local", offline: true, configured: false },
    metrics: { firstMs: [], tools: 0, barge: 0, cancel: 0 }, camStream: null, pttTimer: null, ptt: false,
  };

  const opt = {
    tts: () => HAS_TTS && $("#m-tts").checked,
    hands: () => $("#m-hands").checked,
    serverAsr: () => state.audio.offline || state.audio.provider !== "local" || $("#m-server-asr").checked || !SR,
    haptics: () => $("#m-haptics").checked,
  };

  // ------------------------------------------------------------ persisted preferences
  const PREFS = ["m-tts", "m-hands", "m-server-asr", "m-haptics"];
  try {
    const saved = JSON.parse(localStorage.getItem("tl_prefs") || "{}");
    PREFS.forEach((id) => { if (id in saved) $("#" + id).checked = !!saved[id]; });
  } catch (_) { /* ignore corrupt prefs */ }
  PREFS.forEach((id) => $("#" + id).addEventListener("change", () => {
    localStorage.setItem("tl_prefs", JSON.stringify(Object.fromEntries(PREFS.map((k) => [k, $("#" + k).checked]))));
    if (id === "m-tts" && !$("#m-tts").checked) stopSpeech();
  }));
  if (!SR) { const c = $("#m-server-asr"); c.checked = true; c.disabled = true; }
  if (!HAS_TTS) { const c = $("#m-tts"); c.checked = false; c.disabled = true; }

  // ------------------------------------------------------------ UI helpers
  function setStatus(text, cls = "") {
    statusEl.textContent = text;
    statusEl.className = "status " + cls;
    statusDot.className = "status-indicator-dot " + cls;
  }
  function setConn(text, cls) { connLabel.textContent = text; connLabel.dataset.state = cls || ""; }
  const setWaveform = (on) => waveform.classList.toggle("active", !!on);
  const buzz = (p) => { if (opt.haptics() && navigator.vibrate) try { navigator.vibrate(p); } catch (_) {} };

  function toast(msg, kind = "") {
    const t = document.createElement("div");
    t.className = "toast " + kind;
    t.textContent = msg;
    $("#toasts").appendChild(t);
    setTimeout(() => t.classList.add("out"), 2800);
    setTimeout(() => t.remove(), 3200);
  }

  const nearBottom = () => chat.scrollHeight - chat.scrollTop - chat.clientHeight < 120;
  function scroll(force) {
    if (force || nearBottom()) { chat.scrollTop = chat.scrollHeight; $("#jump").classList.add("hidden"); }
    else $("#jump").classList.remove("hidden");
  }
  chat.addEventListener("scroll", () => { if (nearBottom()) $("#jump").classList.add("hidden"); }, { passive: true });
  $("#jump").onclick = () => scroll(true);

  const clock = () => new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  function bubble(cls, html, meta) {
    const welcome = $("#welcome-card");
    if (welcome && !cls.startsWith("sys")) welcome.remove();
    const stick = nearBottom();
    const d = document.createElement("div");
    d.className = "msg " + cls;
    d.innerHTML = html + (meta !== false ? `<span class="meta">${esc(meta || clock())}</span>` : "");
    chat.appendChild(d);
    const all = $$(".msg", chat);
    if (all.length > MAX_BUBBLES) all.slice(0, all.length - MAX_BUBBLES).forEach((m) => m.remove());
    scroll(stick || cls.startsWith("user"));
    return d;
  }

  async function post(path, body, { timeout = 20000 } = {}) {
    const ctl = new AbortController();
    const to = setTimeout(() => ctl.abort(), timeout);
    try {
      const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}), signal: ctl.signal });
      const j = await r.json().catch(() => ({ error: "Bad response from server" }));
      if (!r.ok) { const e = new Error(j.error || r.statusText); e.code = j.code; e.status = r.status; throw e; }
      return j;
    } catch (e) {
      if (e.name === "AbortError") { const t = new Error("request timed out"); t.code = "timeout"; throw t; }
      throw e;
    } finally { clearTimeout(to); }
  }

  // ------------------------------------------------------------ session lifecycle
  function resetSessionUi() {
    state.tasks = {}; state.slots = {}; state.lastId = 0; state.seen.clear();
    renderTasks(); updateMemoryDeck({});
  }

  function start() {
    if (state.starting) return state.starting;
    state.starting = (async () => {
      closeStream();
      setStatus("Starting session…", "connecting"); setConn("Connecting…", "connecting");
      for (let attempt = 0; ; attempt++) {
        try {
          const j = await post("/api/live/start");
          state.sid = j.sid;
          state.audio = j.audio || state.audio;
          $("#mode").textContent = (j.mode || "").includes("MOCK") ? "MOCK TOOLS" : j.mode;
          $("#mode").title = j.mode || "";
          $("#sid-label").textContent = "Session " + j.sid.slice(0, 8);
          resetSessionUi();
          sessionStorage.setItem("tl_sid", state.sid);
          connect();
          return;
        } catch (e) {
          const wait = Math.min(15000, 1500 * 2 ** attempt);
          setStatus(`Session start failed (${e.message}) — retrying in ${Math.round(wait / 1000)}s`, "neg");
          setConn("Offline", "neg");
          await new Promise((r) => setTimeout(r, wait));
        }
      }
    })().finally(() => { state.starting = null; });
    return state.starting;
  }

  function closeStream() {
    clearTimeout(state.reconnectTimer);
    if (state.es) { state.es.onerror = state.es.onmessage = state.es.onopen = null; state.es.close(); }
    state.es = null;
  }

  function connect() {
    closeStream();
    if (!state.sid) return;
    const es = new EventSource(`/api/live/${encodeURIComponent(state.sid)}/stream?last=${state.lastId}`);
    state.es = es;
    es.onopen = () => {
      state.backoff = 1000;
      setStatus(navigator.onLine ? "Ready — speak or type" : "Offline", navigator.onLine ? "ok" : "neg");
      setConn("Live", "ok");
    };
    es.onmessage = (m) => { try { onEvent(JSON.parse(m.data)); } catch (err) { console.warn("bad event", err); } };
    es.onerror = () => {
      // EventSource auto-retries on transient errors; if it gave up (CLOSED) the session is likely gone.
      setConn("Reconnecting…", "connecting");
      if (es.readyState === EventSource.CLOSED) scheduleReconnect();
      else setStatus("Reconnecting stream…", "connecting");
    };
  }

  function scheduleReconnect() {
    closeStream();
    const wait = state.backoff; state.backoff = Math.min(15000, state.backoff * 2);
    setStatus(`Stream lost — retrying in ${Math.round(wait / 1000)}s`, "connecting");
    state.reconnectTimer = setTimeout(async () => {
      try { await post(`/api/live/${state.sid}/log`, {}, { timeout: 8000 }); connect(); }
      catch (e) {
        if (e.code === "no_session") { bubble("sys", "Session expired — started a fresh one.", false); start(); }
        else scheduleReconnect();
      }
    }, wait);
  }

  async function resume() {
    const sid = sessionStorage.getItem("tl_sid");
    if (!sid) return start();
    try {
      await post(`/api/live/${sid}/log`, {}, { timeout: 8000 });
      state.sid = sid;
      $("#sid-label").textContent = "Session " + sid.slice(0, 8);
      connect();          // lastId=0 → server replays the session history
    } catch (_) { start(); }
  }

  window.addEventListener("online", () => { toast("Back online"); if (state.sid) connect(); else start(); });
  window.addEventListener("offline", () => { setStatus("Device offline", "neg"); setConn("Offline", "neg"); });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { stopListening(true); stopSpeech(); }
    else if (state.sid && (!state.es || state.es.readyState === EventSource.CLOSED)) connect();
  });
  window.addEventListener("pagehide", () => { stopListening(true); stopCamStream(); });

  // ------------------------------------------------------------ event stream
  function onEvent(ev) {
    if (ev.id != null) {
      if (state.seen.has(ev.id)) return;
      state.seen.add(ev.id);
      if (state.seen.size > 2000) state.seen = new Set([...state.seen].slice(-1000));
      state.lastId = Math.max(state.lastId, ev.id);
    }
    state.log.push(ev);
    if (state.log.length > 1000) state.log.splice(0, state.log.length - 1000);
    const replay = !!ev.replay;

    if (ev.kind === "say") {
      const cls = { filler_speech: "filler", clarification_request: "clar", final_response: "bot" }[ev.action] || "bot";
      const slots = ev.state?.slots && Object.keys(ev.state.slots).length ? ev.state.slots : null;
      if (slots) updateMemoryDeck(slots);
      if (state.pendingSendAt && !replay) {
        const dt = performance.now() - state.pendingSendAt;
        state.metrics.firstMs.push(dt); state.pendingSendAt = 0; renderMetrics();
      }
      const label = { filler: "Working", clar: "Needs your input", bot: "" }[cls];
      const b = bubble(cls, (label ? `<span class="tag">${label}</span>` : "") + `<span class="txt">${esc(ev.text)}</span>`);
      if (ev.action === "clarification_request") { markAwaiting(); if (!replay) buzz([20, 40, 20]); }
      if (ev.action === "final_response") delete state.tasks._await;
      if (!replay) speak(ev.text, b, ev.action);
      renderTasks();
    } else if (ev.kind === "task") {
      const t = state.tasks[ev.call_id] || (state.tasks[ev.call_id] = { id: ev.call_id, api: ev.api, t0: ev.t_ms });
      if (ev.api) t.api = ev.api;
      if (ev.status === "cancel_noop") return;
      if (ev.status === "running" && !replay) state.metrics.tools++;
      t.status = ev.status; t.t1 = ev.t_ms;
      if (ev.status === "cancelled") {
        if (!replay) state.metrics.cancel++;
        bubble("sys", `Stopped <b>${esc(label(t.api))}</b> — superseded by your update`, false);
      } else if (ev.status === "error") {
        bubble("sys err", `${esc(label(t.api))} failed — the assistant will handle it`, false);
      }
      renderTasks(); renderMetrics();
      pruneTasks();
    } else if (ev.kind === "user") {
      if (!replay) {
        if (ev.as_ === "interruption") { state.metrics.barge++; renderMetrics(); }
        return;         // user bubbles are rendered optimistically at send time
      }
      if (ev.as_ === "video_frame") bubble("user", `<span class="tag">Camera</span>Frame shared`);
      else if (ev.as_ === "user_audio_chunk") bubble("user", `<span class="tag">Voice</span>Voice clip`);
      else bubble("user" + (ev.as_ === "interruption" ? " barge" : ""),
        (ev.as_ === "interruption" ? `<span class="tag">Barge-in</span>` : "") + esc(ev.text));
    } else if (ev.kind === "asr") {
      $("#interim").textContent = "";
      if (!ev.text) setStatus(ev.error || "Speech not recognized", "neg");
    } else if (ev.kind === "closed") {
      setStatus("Session ended", ""); setConn("Ended", ""); setWaveform(false);
    }
  }

  const label = (api) => ({
    flight_search: "Flight search", book_flight: "Booking", cancel_booking: "Cancelling booking",
    lookup_manual: "Manual lookup", create_support_ticket: "Support ticket", reserve_rental_car: "Car rental",
    weather_lookup: "Weather", identify_port: "Port identification",
  }[api] || String(api || "Task").replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase()));

  function markAwaiting() { state.tasks._await = { id: "_await", api: "_await", status: "await" }; renderTasks(); }

  function pruneTasks() {
    const done = Object.values(state.tasks).filter((t) => ["done", "cancelled", "error"].includes(t.status));
    if (done.length > 4) done.slice(0, done.length - 4).forEach((t) => delete state.tasks[t.id]);
  }

  function renderTasks() {
    const list = Object.values(state.tasks).slice(-6);
    $("#tasks").classList.toggle("empty", !list.length);
    $("#tasks").innerHTML = list.map((t) => {
      const dur = t.t1 != null && t.t0 != null && t.status !== "running" ? ` · ${((t.t1 - t.t0) / 1000).toFixed(1)}s` : "";
      const st = { running: "running", done: "done", cancelled: "cancelled", error: "failed", await: "" }[t.status] || "";
      return `<span class="task ${esc(t.status)}"><i></i><b>${esc(t.status === "await" ? "Waiting for you" : label(t.api))}</b>${st ? `<em>${st}${dur}</em>` : ""}</span>`;
    }).join("");
  }

  function renderMetrics() {
    const f = state.metrics.firstMs;
    $("#m-first").textContent = f.length ? `${Math.round(f[f.length - 1])} ms` : "—";
    $("#m-first").title = f.length ? `median ${Math.round([...f].sort((a, b) => a - b)[f.length >> 1])} ms over ${f.length} turns` : "";
    $("#m-tools").textContent = state.metrics.tools;
    $("#m-barge").textContent = state.metrics.barge;
    $("#m-cancel").textContent = state.metrics.cancel;
  }

  function updateMemoryDeck(slots) {
    state.slots = slots || {};
    const entries = Object.entries(state.slots).filter(([, v]) => v !== null && v !== "" && typeof v !== "object");
    $("#memory-deck").classList.toggle("hidden", !entries.length);
    $("#memory-slots").innerHTML = entries.map(([k, v]) =>
      `<span class="memory-chip" title="${esc(k)}: ${esc(v)}">${esc(k.replace(/_/g, " "))} <b>${esc(v)}</b></span>`).join("");
  }

  // ------------------------------------------------------------ speech output
  function speak(text, el, action) {
    if (!opt.tts() || !text) { if (action !== "filler_speech") maybeHandsFree(); return; }
    // a new final/clarification supersedes queued progress fillers
    if (action !== "filler_speech") state.speechQ = state.speechQ.filter((q) => q.action !== "filler_speech");
    state.speechQ.push({ text, el, action });
    if (!state.speaking) nextSpeech();
  }

  function nextSpeech() {
    const item = state.speechQ.shift();
    if (!item) {
      state.speaking = false;
      micBtn.classList.remove("speaking");
      setWaveform(state.listening);
      maybeHandsFree();
      return;
    }
    state.speaking = true;
    micBtn.classList.add("speaking");
    setWaveform(true);
    const u = new SpeechSynthesisUtterance(item.text);
    u.rate = 1.05; u.lang = "en-US";
    if (state.audio.offline) {
      const localVoice = speechSynthesis.getVoices().find((v) => v.localService && v.lang.startsWith("en"));
      if (!localVoice) { state.speaking = false; micBtn.classList.remove("speaking"); setWaveform(state.listening); state.speechQ = []; return; }
      u.voice = localVoice;
    }
    item.el.classList.add("speaking");
    let finished = false;
    const done = () => { if (finished) return; finished = true; clearTimeout(guard); item.el.classList.remove("speaking"); if (state.speaking) nextSpeech(); };
    // some mobile engines never fire onend — guard with a length-based timeout
    const guard = setTimeout(done, 4000 + item.text.length * 90);
    u.onend = done; u.onerror = done;
    speechSynthesis.speak(u);
  }

  function stopSpeech() {
    state.speechQ = [];
    const was = state.speaking;
    state.speaking = false; // onend/onerror may fire synchronously during cancel
    if (HAS_TTS) speechSynthesis.cancel();
    $$(".msg.speaking").forEach((e) => e.classList.remove("speaking"));
    micBtn.classList.remove("speaking");
    setWaveform(state.listening);
    return was;
  }

  function maybeHandsFree() {
    if (!opt.hands() || state.listening || state.speaking || document.hidden) return;
    if (Object.values(state.tasks).some((t) => t.status === "running")) return;
    setTimeout(() => { if (!state.listening && !state.speaking) startListening(); }, 350);
  }

  const busy = () => Object.values(state.tasks).some((t) => t.status === "running");

  // ------------------------------------------------------------ user input & barge-in
  async function sendText(text, wasSpeaking) {
    text = (text || "").replace(/\s+/g, " ").trim().slice(0, 500);
    if (!text) return;
    if (!state.sid) await start();
    const barge = wasSpeaking || busy();
    delete state.tasks._await; renderTasks();
    const b = bubble("user pending" + (barge ? " barge" : ""), (barge ? `<span class="tag">Barge-in</span>` : "") + esc(text));
    buzz(barge ? [15, 30, 15] : 10);
    state.pendingSendAt = performance.now();
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        await post(`/api/live/${state.sid}/say`, { text, speaking: wasSpeaking });
        b.classList.remove("pending");
        return;
      } catch (e) {
        if (e.code === "no_session" && attempt === 0) { bubble("sys", "Session expired — reconnected, resending…", false); await start(); continue; }
        b.classList.remove("pending"); b.classList.add("failed");
        b.insertAdjacentHTML("beforeend", `<button class="retry" type="button">Retry</button>`);
        $(".retry", b).onclick = () => { b.remove(); sendText(text, false); };
        toast("Couldn't send: " + e.message, "neg");
        state.pendingSendAt = 0;
        return;
      }
    }
  }

  input.addEventListener("input", () => { sendBtn.disabled = !input.value.trim(); });
  $("#form").onsubmit = (e) => {
    e.preventDefault();
    const v = input.value;
    if (!v.trim()) return;
    const was = stopSpeech();
    input.value = ""; sendBtn.disabled = true;
    sendText(v, was);
  };

  // tap = toggle; press-and-hold (>350ms) = push-to-talk, release to send
  micBtn.addEventListener("pointerdown", (e) => {
    if (e.button !== undefined && e.button !== 0) return;
    state.ptt = false;
    clearTimeout(state.pttTimer);
    if (!state.listening) state.pttTimer = setTimeout(() => { state.ptt = true; startListening(); }, 350);
  });
  const pttEnd = () => {
    clearTimeout(state.pttTimer);
    if (state.ptt) { state.ptt = false; stopListening(); state.suppressClick = true; }
  };
  micBtn.addEventListener("pointerup", pttEnd);
  micBtn.addEventListener("pointercancel", pttEnd);
  micBtn.addEventListener("pointerleave", pttEnd);
  micBtn.addEventListener("contextmenu", (e) => e.preventDefault());
  micBtn.addEventListener("click", () => {
    if (state.suppressClick) { state.suppressClick = false; return; }
    if (state.listening) stopListening(); else startListening();
  });

  function setListening(on) {
    state.listening = on;
    micBtn.classList.toggle("listening", on);
    micBtn.setAttribute("aria-pressed", String(on));
    setWaveform(on || state.speaking);
    if (!on) $("#interim").textContent = "";
  }

  async function startListening() {
    if (state.listening) return;
    if (!state.sid) await start();
    if (opt.serverAsr() && !state.audio.configured) {
      toast("Server speech is not configured. Check API keys or use text input.", "neg"); return;
    }
    state.wasSpeakingAtStart = stopSpeech() || busy();
    buzz(12);
    if (opt.serverAsr()) return recordForServer();
    try {
      const rec = new SR();
      state.rec = rec;
      rec.lang = "en-US"; rec.interimResults = true; rec.continuous = false; rec.maxAlternatives = 1;
      let final = "";
      rec.onresult = (e) => {
        let interim = "";
        for (let i = e.resultIndex; i < e.results.length; i++) {
          const r = e.results[i];
          if (r.isFinal) final += r[0].transcript; else interim += r[0].transcript;
        }
        $("#interim").textContent = (final + " " + interim).trim();
      };
      rec.onerror = (e) => {
        if (e.error === "no-speech" || e.error === "aborted") return;
        const msg = e.error === "not-allowed" || e.error === "service-not-allowed"
          ? "Microphone blocked — allow it in browser settings" : "Mic error: " + e.error;
        setStatus(msg, "neg"); toast(msg, "neg");
        if (e.error === "network") { $("#m-server-asr").checked = true; toast("Switched to server ASR"); }
      };
      rec.onend = () => {
        if (state.rec !== rec) return;
        state.rec = null;
        setListening(false);
        setStatus("Ready — speak or type", "ok");
        if (final.trim() && !rec._discard) sendText(final, state.wasSpeakingAtStart);
      };
      rec.start();
      setListening(true);
      setStatus(state.ptt ? "Listening… release to send" : "Listening… tap to stop", "live");
    } catch (e) {
      setStatus("Speech recognition unavailable: " + e.message, "neg");
    }
  }

  function stopListening(discard) {
    if (state.rec) { state.rec._discard = !!discard; try { state.rec.stop(); } catch (_) {} }
    if (state.mediaRec && state.mediaRec.state === "recording") { state.mediaRec._discard = !!discard; state.mediaRec.stop(); }
    clearTimeout(state.recTimer);
    setListening(false);
  }

  // Server-side Whisper fallback (Firefox / iOS without Web Speech)
  async function recordForServer() {
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (e) {
      const msg = e.name === "NotAllowedError" ? "Microphone blocked — allow it in browser settings" : "Microphone unavailable: " + e.message;
      setStatus(msg, "neg"); toast(msg, "neg"); return;
    }
    const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"]
      .find((m) => window.MediaRecorder && MediaRecorder.isTypeSupported?.(m));
    let mr;
    try { mr = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined); }
    catch (e) { stream.getTracks().forEach((t) => t.stop()); setStatus("Recording unsupported: " + e.message, "neg"); return; }
    const chunks = [];
    const t0 = performance.now();
    state.mediaRec = mr;
    mr.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    mr.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      if (state.mediaRec === mr) state.mediaRec = null;
      setListening(false);
      if (mr._discard || !chunks.length || performance.now() - t0 < 300) { setStatus("Ready — speak or type", "ok"); return; }
      const blob = new Blob(chunks, { type: mr.mimeType || "audio/webm" });
      const url = await new Promise((resolve) => { const f = new FileReader(); f.onload = () => resolve(f.result); f.readAsDataURL(blob); });
      setStatus("Transcribing on server…", "connecting");
      bubble("user" + (state.wasSpeakingAtStart ? " barge" : ""), `<span class="tag">Voice</span>Voice clip · ${((performance.now() - t0) / 1000).toFixed(1)}s`);
      state.pendingSendAt = performance.now();
      try { await post(`/api/live/${state.sid}/audio`, { audio: url, speaking: state.wasSpeakingAtStart }, { timeout: 60000 }); setStatus("Ready — speak or type", "ok"); }
      catch (e) { setStatus("Audio upload failed: " + e.message, "neg"); toast("Audio upload failed", "neg"); state.pendingSendAt = 0; }
    };
    mr.start();
    setListening(true);
    setStatus(state.ptt ? "Recording… release to send" : "Recording… tap to finish", "live");
    state.recTimer = setTimeout(() => { if (mr.state === "recording") mr.stop(); }, 15000);
  }

  // ------------------------------------------------------------ camera & images
  function stopCamStream() {
    if (state.camStream) state.camStream.getTracks().forEach((t) => t.stop());
    state.camStream = null;
  }
  function closeCam() { stopCamStream(); $("#cam").classList.add("hidden"); $("#video").srcObject = null; }

  $("#camera").onclick = async () => {
    if (!navigator.mediaDevices?.getUserMedia || !window.isSecureContext) return $("#file").click();
    $("#cam").classList.remove("hidden");
    try {
      state.camStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 } }, audio: false });
      $("#video").srcObject = state.camStream;
    } catch (e) {
      closeCam();
      toast(e.name === "NotAllowedError" ? "Camera blocked — choose an image instead" : "Camera unavailable — choose an image");
      $("#file").click();
    }
  };
  $("#camclose").onclick = closeCam;
  $("#pick-file").onclick = () => { closeCam(); $("#file").click(); };

  function frameToDataUrl(src, w, h) {
    const s = Math.min(1, 960 / Math.max(w, h));
    const c = document.createElement("canvas");
    c.width = Math.round(w * s); c.height = Math.round(h * s);
    c.getContext("2d").drawImage(src, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", 0.85);
  }

  $("#snap").onclick = async () => {
    const v = $("#video");
    if (!v.videoWidth) { toast("Camera not ready yet"); return; }
    buzz(20);
    $("#cam-laser").classList.add("scanning");
    const url = frameToDataUrl(v, v.videoWidth, v.videoHeight);
    setTimeout(() => { $("#cam-laser").classList.remove("scanning"); closeCam(); sendFrame(url); }, 300);
  };

  $("#load-sample-cam").onclick = async () => {
    closeCam();
    try {
      const blob = await (await fetch("/sample_port.png")).blob();
      const url = await new Promise((resolve) => { const r = new FileReader(); r.onload = () => resolve(r.result); r.readAsDataURL(blob); });
      sendFrame(url, "Sample frame");
    } catch (e) { toast("Failed to load sample: " + e.message, "neg"); }
  };

  $("#file").onchange = async (e) => {
    const f = e.target.files[0];
    e.target.value = "";
    if (!f) return;
    if (!f.type.startsWith("image/")) return toast("Please choose an image", "neg");
    const img = new Image();
    const objUrl = URL.createObjectURL(f);
    try {
      img.src = objUrl;
      await img.decode();
      sendFrame(frameToDataUrl(img, img.naturalWidth, img.naturalHeight));
    } catch (_) { toast("Couldn't read that image", "neg"); }
    finally { URL.revokeObjectURL(objUrl); }
  };

  async function sendFrame(url, tag = "Camera") {
    if (!state.sid) await start();
    const b = bubble("user pending img", `<span class="tag">${esc(tag)}</span><img class="thumb" alt="Shared frame" src="${esc(url)}">`);
    setStatus("Uploading frame…", "connecting");
    try {
      await post(`/api/live/${state.sid}/frame`, { image: url }, { timeout: 30000 });
      b.classList.remove("pending");
      setStatus("Frame shared — ask about it", "ok");
    } catch (e) {
      b.classList.remove("pending"); b.classList.add("failed");
      setStatus("Frame upload failed: " + e.message, "neg"); toast("Frame upload failed", "neg");
    }
  }

  // ------------------------------------------------------------ bottom sheet
  let lastFocus = null;
  function openSheet() {
    lastFocus = document.activeElement;
    $("#sheet").classList.remove("hidden"); $("#backdrop").classList.remove("hidden");
    $("#menu").setAttribute("aria-expanded", "true");
    $("#sheet-close").focus();
  }
  function closeSheet() {
    $("#sheet").classList.add("hidden"); $("#backdrop").classList.add("hidden");
    $("#menu").setAttribute("aria-expanded", "false");
    lastFocus?.focus?.();
  }
  $("#menu").onclick = () => ($("#sheet").classList.contains("hidden") ? openSheet() : closeSheet());
  $("#sheet-close").onclick = closeSheet;
  $("#backdrop").onclick = closeSheet;
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { if (!$("#cam").classList.contains("hidden")) closeCam(); else if (!$("#sheet").classList.contains("hidden")) closeSheet(); else if (state.speaking) stopSpeech(); }
  });
  // swipe the sheet down to dismiss
  (() => {
    const sh = $("#sheet"); let y0 = null, dy = 0;
    sh.addEventListener("touchstart", (e) => { if (sh.scrollTop <= 0) { y0 = e.touches[0].clientY; dy = 0; } }, { passive: true });
    sh.addEventListener("touchmove", (e) => { if (y0 == null) return; dy = Math.max(0, e.touches[0].clientY - y0); sh.style.transform = `translateY(${dy}px)`; }, { passive: true });
    sh.addEventListener("touchend", () => { sh.style.transform = ""; if (dy > 90) closeSheet(); y0 = null; });
  })();

  $$(".sample-chip").forEach((btn) => { btn.onclick = () => { const t = btn.dataset.sample; if (t) sendText(t, stopSpeech()); }; });

  $("#m-new").onclick = async () => {
    closeSheet(); stopSpeech(); stopListening(true);
    const old = state.sid;
    state.sid = null; sessionStorage.removeItem("tl_sid");
    if (old) post(`/api/live/${old}/end`).catch(() => {});
    $$(".msg", chat).forEach((m) => m.remove());
    state.metrics = { firstMs: [], tools: 0, barge: 0, cancel: 0 }; renderMetrics();
    await start();
    toast("New session started");
  };

  $("#m-export").onclick = async () => {
    let server = {};
    try { server = await post(`/api/live/${state.sid}/log`); } catch (_) {}
    const payload = { sid: state.sid, mode: $("#mode").title, exported_at: new Date().toISOString(),
      metrics: state.metrics, client_events: state.log, server_diagnostics: server };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `triageline-session-${Date.now()}.json`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  };

  $("#m-ready").onclick = async () => {
    const box = $("#ready");
    box.innerHTML = "<div class='muted'>Checking device, models &amp; assets…</div>";
    const row = (k, ok, note = "") => `<div><span>${esc(k)}</span><span class="${ok ? "y" : "n"}">${ok ? "✓" : "✕"}${note ? " " + esc(note) : ""}</span></div>`;
    const out = [
      row("Speech recognition", !!SR, opt.serverAsr() ? "server STT selected" : "browser service (may require internet)"),
      row("Speech synthesis", HAS_TTS),
      row("Secure context", window.isSecureContext),
      row("Camera API", !!navigator.mediaDevices?.getUserMedia),
      row("Live stream", state.es?.readyState === 1),
    ];
    try {
      const perm = await navigator.permissions?.query({ name: "microphone" });
      if (perm) out.push(row("Microphone permission", perm.state !== "denied", perm.state));
    } catch (_) { /* Safari doesn't support mic permission query */ }
    try {
      const r = await (await fetch("/api/ready")).json();
      out.push(row("Tool planner", r.planner?.configured, r.planner?.provider || "local rules"),
        row("Available tools", !!r.tools?.length, String(r.tools?.length || 0)),
        row("Server speech configuration", !!r.audio?.configured, r.speech || "local Whisper"),
        row("Local-only mode", !!r.offline, r.offline ? "no hosted API calls" : "hosted APIs allowed"));
      out.push(row("Whisper ASR model", r.asr_loaded, r.asr_loaded ? "" : "lazy-loads on first clip"),
        row("CLIP vision model", r.clip_loaded, r.clip_loaded ? "" : "lazy-loads on first frame"),
        row("Tesseract OCR", r.tesseract));
      Object.entries(r.assets || {}).forEach(([k, v]) => out.push(row(k.split("/").pop(), v)));
    } catch (e) { out.push(row("Server readiness API", false, e.message)); }
    box.innerHTML = out.join("");
  };

  // ------------------------------------------------------------ mobile viewport (keyboard-aware)
  if (window.visualViewport) {
    const fit = () => document.documentElement.style.setProperty("--app-h", `${window.visualViewport.height}px`);
    visualViewport.addEventListener("resize", () => { fit(); scroll(); });
    fit();
  }
  input.addEventListener("focus", () => setTimeout(() => scroll(true), 250));

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});

  renderMetrics();
  // open the long-lived stream only after load so it never holds the page's load event
  async function boot() {
    try {
      const r = await fetch("/api/ready");
      if (r.ok) state.audio = (await r.json()).audio || state.audio;
    } catch (_) { /* fail closed: keep browser cloud speech disabled */ }
    resume();
  }
  if (document.readyState === "complete") boot();
  else window.addEventListener("load", boot, { once: true });
})();
