"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let SCEN = [], SEL = null, AGENT = "triageline";
let activeModFilter = "all", searchQuery = "";
const lastScores = {};
let currentTrace = null, currentTraceEnd = 0, currentPlayTimer = null, currentPlaySpeed = 1;

// ---------------------------------------------------------------- Theme Switcher
const savedTheme = localStorage.getItem("tl_theme") || "dark";
document.documentElement.setAttribute("data-theme", savedTheme);
updateThemeUI(savedTheme);

const themeToggleBtn = $("#theme-toggle");
if (themeToggleBtn) {
  themeToggleBtn.onclick = () => {
    const cur = document.documentElement.getAttribute("data-theme");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("tl_theme", next);
    updateThemeUI(next);
  };
}

function updateThemeUI(theme) {
  const lbl = $("#theme-label");
  if (lbl) lbl.textContent = theme === "dark" ? "Dark" : "Light";
}

// ---------------------------------------------------------------- Navigation Tabs
$$(".tab").forEach((b) => {
  b.onclick = () => {
    if (b.classList.contains("live-link")) return;
    $$(".tab").forEach((x) => {
      x.classList.toggle("active", x === b);
      x.setAttribute("aria-selected", x === b ? "true" : "false");
    });
    $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + b.dataset.view));
  };
});

// Agent selector
$$("#agent-seg button").forEach((b) => {
  b.onclick = () => {
    AGENT = b.dataset.agent;
    $$("#agent-seg button").forEach((x) => x.classList.toggle("on", x === b));
  };
});

// ---------------------------------------------------------------- API Helper
async function api(path, body) {
  const r = await fetch(path, body ? {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  } : {});
  const j = await r.json();
  if (j.error) throw new Error(j.error);
  return j;
}

// ---------------------------------------------------------------- Scenario List & Filters
async function loadScenarios() {
  try {
    SCEN = await api("/api/scenarios");
  } catch (e) {
    $("#scenario-list").innerHTML = `<li class="neg" style="padding:12px;color:var(--cancel)">Failed to connect to harness: ${esc(e.message)}</li>`;
    return;
  }
  if (!SCEN.length) {
    $("#scenario-list").innerHTML = `<li class="muted" style="padding:12px">No scenarios found in scenarios/ directory.</li>`;
    return;
  }

  renderScenarioList();
  select(Math.min(1, SCEN.length - 1));
}

function renderScenarioList() {
  const ul = $("#scenario-list");
  const q = searchQuery.toLowerCase().trim();

  const filtered = SCEN.map((s, i) => ({ s, i })).filter(({ s }) => {
    const matchesMod = activeModFilter === "all" || (s.modality || "text").toLowerCase() === activeModFilter;
    const matchesSearch = !q || (s.id || "").toLowerCase().includes(q) || (s.description || "").toLowerCase().includes(q);
    return matchesMod && matchesSearch;
  });

  const countBadge = $("#sc-count");
  if (countBadge) countBadge.textContent = `${filtered.length}/${SCEN.length}`;

  if (!filtered.length) {
    ul.innerHTML = `<li class="muted" style="padding:12px;text-align:center">No matching scenarios found.</li>`;
    return;
  }

  ul.innerHTML = filtered.map(({ s, i }) => {
    const mod = (s.modality || "text").toLowerCase();
    const diff = (s.difficulty || "L1").toLowerCase();
    const hasMissing = s.missing_media && s.missing_media.length > 0;
    const scoreVal = lastScores[i] != null ? lastScores[i] : "";
    const scoreClass = scoreVal !== "" ? (scoreVal >= 90 ? "pass" : scoreVal >= 50 ? "warn" : "fail") : "";

    return `
      <li data-i="${i}" class="${SEL === s ? "sel" : ""}">
        <span class="sid" title="${esc(s.id)}">${esc(s.id)}</span>
        <div class="tags-row">
          <span class="tag mod-${mod}">${esc(s.modality || "text")}</span>
          <span class="tag diff-${diff}">${esc(s.difficulty || "L1")}</span>
          ${s.path && s.path.includes("extra") ? '<span class="tag">EXTRA</span>' : ""}
          ${hasMissing ? '<span class="media-warn">⚠ Missing Media</span>' : ""}
        </div>
        <span class="sc ${scoreClass}" id="sc-${i}">${scoreVal !== "" ? scoreVal : "—"}</span>
      </li>`;
  }).join("");

  $$("li[data-i]", ul).forEach((li) => {
    li.onclick = () => select(+li.dataset.i);
  });
}

// Search and Filter Listeners
const searchInput = $("#sc-search");
if (searchInput) {
  searchInput.addEventListener("input", (e) => {
    searchQuery = e.target.value;
    renderScenarioList();
  });
}

$$("#mod-filters .filter-chip").forEach((btn) => {
  btn.onclick = () => {
    $$("#mod-filters .filter-chip").forEach((b) => b.classList.toggle("active", b === btn));
    activeModFilter = btn.dataset.mod;
    renderScenarioList();
  };
});

function select(i) {
  if (i < 0 || i >= SCEN.length) return;
  SEL = SCEN[i];

  $$("#scenario-list li").forEach((li) => {
    li.classList.toggle("sel", +li.dataset.i === i);
  });

  $("#sc-title").textContent = SEL.id;
  $("#sc-meta").innerHTML = `
    <span>${esc(SEL.modality || "text").toUpperCase()}</span>
    <span class="dot-sep">·</span>
    <span>DIFFICULTY ${esc(SEL.difficulty || "L1")}</span>
    <span class="dot-sep">·</span>
    <span>${SEL.events ? SEL.events.length : 0} TIMED EVENTS</span>
    ${SEL.path && SEL.path.includes("extra") ? '<span class="dot-sep">·</span><span>EXTRA TEST</span>' : ""}
  `;

  let desc = SEL.description || "Official evaluation scenario with ground truth scoring metrics.";
  if (SEL.missing_media && SEL.missing_media.length) {
    desc += `<span class="warn-inline">⚠ Missing bundled media file(s): ${esc(SEL.missing_media.join(", "))}. Agent will engage fallback.</span>`;
  }
  $("#sc-desc").innerHTML = desc;
  $("#run-btn").disabled = false;
}

// Keyboard shortcut: Cmd/Ctrl + Enter to run scenario
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
    const runBtn = $("#run-btn");
    if (runBtn && !runBtn.disabled && $("#view-run").classList.contains("active")) {
      e.preventDefault();
      runBtn.click();
    }
  }
});

// ---------------------------------------------------------------- Run Scenario Execution
let RUN_ID = 0;
$("#run-btn").onclick = async () => {
  if (!SEL) return;
  const btn = $("#run-btn");
  const req = { sc: SEL, i: SCEN.indexOf(SEL), agent: AGENT, ts: +$("#ts").value, id: ++RUN_ID };

  btn.classList.add("busy");
  btn.innerHTML = `
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="spin"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
    <span>Executing…</span>`;
  btn.disabled = true;

  try {
    const res = await api("/api/run", { path: req.sc.path, agent: req.agent, time_scale: req.ts });
    if (res.score) {
      const formatted = res.score.total.toFixed(0);
      lastScores[req.i] = formatted;
      const scoreBadge = $("#sc-" + req.i);
      if (scoreBadge) {
        scoreBadge.textContent = formatted;
        scoreBadge.className = `sc ${res.score.total >= 90 ? "pass" : res.score.total >= 50 ? "warn" : "fail"}`;
      }
    }

    if (req.id !== RUN_ID || SEL !== req.sc) return;
    renderAll(res, "#timeline", "#transcript");
    renderScore(res.score, res);
  } catch (e) {
    if (req.id !== RUN_ID) return;
    $("#timeline").innerHTML = `<div class="warn-box" style="margin:16px;">Execution failed: ${esc(e.message)} — no score recorded for this run.</div>`;
    $("#transcript").innerHTML = `<li class="muted">No transcript available due to execution error.</li>`;
    $("#checks").innerHTML = `<div class="warn-box">Runner error: ${esc(e.message)}</div>`;
    $("#score-strip").classList.add("hidden");
  } finally {
    btn.classList.remove("busy");
    btn.innerHTML = `
      <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
      <span>Run scenario</span>`;
    btn.disabled = false;
  }
};

// ---------------------------------------------------------------- Trace Parsing & Rendering
function parseTrace(trace) {
  const calls = {}, marks = [], rows = [];
  let tEnd = 0;

  for (let idx = 0; idx < trace.length; idx++) {
    const e = trace[idx];
    const t = e.t_ms ?? 0;
    tEnd = Math.max(tEnd, t);

    if (e.kind === "event") {
      const p = e.payload || {};
      if (e.event_type === "user_speech_chunk" || e.event_type === "user_audio_chunk" || e.event_type === "video_frame") {
        const txt = p.text || (p.audio_ref ? "Audio: " + p.audio_ref : "") || (p.image_ref ? "Frame: " + p.image_ref : "");
        marks.push({ id: `m-${idx}`, lane: "user", cls: "user", t, tip: `${e.event_type}\n${txt}` });
        rows.push({
          id: `r-${idx}`, t, who: "user", cls: "user",
          msg: esc(txt) + (p.end_of_turn === false ? ' <span class="muted">[streaming…]</span>' : "")
        });
      } else if (e.event_type === "interruption") {
        marks.push({ id: `m-${idx}`, lane: "user", cls: "int", t, tip: `Barge-In Interruption\n"${p.text}"`, vline: true });
        rows.push({
          id: `r-${idx}`, t, who: "barge-in", cls: "user",
          msg: `<strong>${esc(p.text)}</strong>`
        });
      }
    } else if (e.kind === "action") {
      const a = e.action;
      if (a === "tool_call") {
        calls[e.call_id] = { id: e.call_id, traceIdx: idx, api: e.api_name, args: e.args, t0: t, t1: null, st: "pending" };
        rows.push({
          id: `r-${idx}`, t, who: "tool call", cls: "tool",
          msg: `<code>${esc(e.api_name)}</code> <span class="muted">${esc(shortArgs(e.args))}</span>`
        });
      } else {
        const txt = (e.payload || {}).text || "";
        const cls = a === "filler_speech" ? "fill" : a === "clarification_request" ? "clar" : "final";
        const lane = cls === "final" ? "answer" : "fast";
        const snap = e.state_snapshot ? JSON.stringify(e.state_snapshot.slots || {}) : "";
        marks.push({ id: `m-${idx}`, lane, cls, t, tip: `${a}\n${txt}${snap ? "\nslots: " + snap : ""}` });

        const whoLabel = { fill: "fast filler", clar: "clarify", final: "final answer" }[cls];
        const rowClass = { fill: "filler", clar: "clar", final: "final" }[cls];

        let formattedSnap = "";
        if (snap && snap !== "{}" && e.state_snapshot && e.state_snapshot.slots) {
          const slotEntries = Object.entries(e.state_snapshot.slots)
            .map(([k, v]) => `<b>${esc(k)}</b>: ${esc(v)}`)
            .join(" · ");
          formattedSnap = `<span class="snap">${slotEntries}</span>`;
        }

        rows.push({
          id: `r-${idx}`, t, who: whoLabel, cls: rowClass,
          msg: esc(txt) + formattedSnap
        });
      }
    } else if (e.kind === "tool_completed") {
      const c = calls[e.call_id];
      if (c) { c.t1 = t; c.st = e.status === "success" ? "ok" : "err"; c.res = e.result; }
      rows.push({
        id: `r-${idx}`, t, who: "tool result", cls: "tool",
        msg: `<code>${esc(e.api_name)}</code> ${e.status === "success" ? '<span style="color:var(--slow)">✓ Success</span>' : '<span style="color:var(--cancel)">✕ Failed</span>'} <span class="muted">${esc(shortArgs(e.result, 100))}</span>`
      });
    } else if (e.kind === "tool_cancelled" || e.kind === "cancel_noop") {
      const c = calls[e.call_id];
      if (c && e.kind === "tool_cancelled") { c.t1 = t; c.st = "cancelled"; }
      rows.push({
        id: `r-${idx}`, t, who: "cancel", cls: "cancel",
        msg: `<code>${esc(e.call_id)}</code> ${c ? esc(c.api) : ""} <span class="muted">${
          e.kind === "tool_cancelled" ? "Cancelled mid-flight (aborted on interrupt)" : "Cancel requested (call had already completed)"
        }</span>`
      });
    } else if (e.kind === "tool_abandoned") {
      const c = calls[e.call_id];
      if (c) { c.t1 = tEnd; c.st = "abandoned"; }
    }
  }

  rows.sort((a, b) => a.t - b.t);
  return { calls: Object.values(calls), marks, rows, tEnd };
}

function shortArgs(o, n = 70) {
  if (!o) return "";
  const s = JSON.stringify(o, (k, v) =>
    Array.isArray(v) && v.length > 6 && typeof v[0] === "number" ? `[${v.length}-d vector]` : v
  );
  return s.length > n ? s.slice(0, n) + "…" : s;
}

function renderAll(res, tlSel, trSel) {
  const parsed = parseTrace(res.trace);
  currentTrace = parsed;
  const { calls, marks, rows, tEnd } = parsed;
  currentTraceEnd = tEnd;

  const span = Math.max(1000, Math.ceil((tEnd + 200) / 1000) * 1000);
  const x = (t) => (100 * t / span).toFixed(3) + "%";

  const lane = (name, inner) => `
    <div class="lane">
      <div class="ln">${name}</div>
      <div class="track">${inner}</div>
    </div>`;

  const mk = (m) => `
    <span class="mk ${m.cls}" id="${m.id}" data-t="${m.t}" style="left:${x(m.t)}" data-tip="${esc(`+${Math.round(m.t)} ms · ${m.tip}`)}"></span>`;

  const bars = calls.map((c) => {
    const t1 = c.t1 ?? tEnd;
    const dur = Math.max(t1 - c.t0, 20);
    const stCls = c.st === "cancelled" ? "cancelled" : c.st === "err" ? "err" : c.st === "abandoned" ? "abandoned" : "";
    return `
      <span class="bar ${stCls}" id="bar-${c.id}" data-t0="${c.t0}" data-t1="${t1}"
            style="left:${x(c.t0)};width:${x(dur)}"
            data-tip="${esc(`${c.id} · ${c.api} (${c.st})\nDuration: +${Math.round(c.t0)}ms → +${Math.round(t1)}ms (${Math.round(t1 - c.t0)}ms)\nArgs: ${shortArgs(c.args, 240)}`)}">
        <span>${esc(c.api)}</span>
      </span>`;
  });

  const lanes = [];
  calls.forEach((c, i) => {
    const t1 = c.t1 ?? tEnd;
    let L = lanes.findIndex((end) => end < c.t0 - 40);
    if (L < 0) { L = lanes.length; lanes.push(0); }
    lanes[L] = t1;
    c._lane = L;
    c._html = bars[i];
  });

  const slowLanes = (lanes.length ? lanes : [0]).map((_, L) =>
    lane(L === 0 ? "Slow Tools" : "", calls.filter((c) => c._lane === L).map((c) => c._html).join(""))
  );

  const ticks = [];
  const step = span > 8000 ? 2000 : 1000;
  for (let t = 0; t <= span; t += step) {
    ticks.push(`<span class="tk" style="left:${x(t)}">${t / 1000}s</span>`);
  }

  const vlines = marks
    .filter((m) => m.vline)
    .map((m) => `<span class="vline" style="left:calc(110px + (100% - 110px) * ${m.t / span})"></span>`)
    .join("");

  const tl = $(tlSel);
  tl.classList.remove("empty");
  tl.innerHTML = `
    <div id="scrubber-needle" class="scrubber-needle hidden" style="left:110px"></div>
    ${vlines}
    ${lane("User Input", marks.filter((m) => m.lane === "user").map(mk).join(""))}
    ${lane("Fast Reflex", marks.filter((m) => m.lane === "fast").map(mk).join(""))}
    ${slowLanes.join("")}
    ${lane("Assistant", marks.filter((m) => m.lane === "answer").map(mk).join(""))}
    <div class="axis"><div></div><div class="track">${ticks.join("")}</div></div>
    <div class="legend">
       <span><i style="background:var(--user)"></i>User Speech</span>
       <span><i style="background:var(--cancel);border-radius:2px;transform:rotate(45deg)"></i>Barge-in Interrupt</span>
       <span><i style="background:var(--fast)"></i>Fast Filler (&lt;5ms)</span>
       <span><i style="background:var(--clar)"></i>Clarification</span>
       <span><i style="background:var(--slow);border-radius:2px"></i>Active Tool</span>
       <span><i style="background:var(--cancel);border-radius:2px"></i>Cancelled Tool</span>
       <span><i style="background:var(--ink)"></i>Final Response</span>
    </div>`;

  const trContainer = $(trSel);
  if (trContainer) {
    trContainer.innerHTML = rows.map((r) => `
      <li id="${r.id}" data-t="${r.t}">
        <span class="t">+${Math.round(r.t)}ms</span>
        <span class="who ${r.cls}">${esc(r.who)}</span>
        <div class="msg">${r.msg}</div>
      </li>
    `).join("") || "<li class='muted'>No execution output recorded.</li>";

    // Click on transcript item to highlight on timeline
    $$("li[data-t]", trContainer).forEach((li) => {
      li.onclick = () => {
        const t = +li.dataset.t;
        setScrubberPosition(t, span);
        highlightAtTime(t);
      };
    });
  }

  // Setup playback scrubber
  setupPlayback(span, tEnd);
}

// ---------------------------------------------------------------- Interactive Playback Scrubber
function setupPlayback(span, tEnd) {
  const scrubber = $("#tl-scrubber");
  const timeLbl = $("#tl-playback-time");
  const playBtn = $("#tl-play");
  const needle = $("#scrubber-needle");

  if (!scrubber || !playBtn) return;

  scrubber.max = tEnd;
  scrubber.value = 0;
  if (timeLbl) timeLbl.textContent = "0 ms";

  if (currentPlayTimer) {
    clearInterval(currentPlayTimer);
    currentPlayTimer = null;
  }
  updatePlayBtn(false);

  scrubber.oninput = (e) => {
    const val = +e.target.value;
    setScrubberPosition(val, span);
    highlightAtTime(val);
  };

  playBtn.onclick = () => {
    if (currentPlayTimer) {
      clearInterval(currentPlayTimer);
      currentPlayTimer = null;
      updatePlayBtn(false);
    } else {
      let cur = +scrubber.value;
      if (cur >= tEnd) cur = 0;
      updatePlayBtn(true);
      if (needle) needle.classList.remove("hidden");

      const tick = 25;
      currentPlayTimer = setInterval(() => {
        cur += tick * currentPlaySpeed;
        if (cur >= tEnd) {
          cur = tEnd;
          clearInterval(currentPlayTimer);
          currentPlayTimer = null;
          updatePlayBtn(false);
        }
        scrubber.value = cur;
        setScrubberPosition(cur, span);
        highlightAtTime(cur);
      }, tick);
    }
  };

  $$("#tl-speed-group .speed-chip").forEach((btn) => {
    btn.onclick = () => {
      $$("#tl-speed-group .speed-chip").forEach((b) => b.classList.toggle("active", b === btn));
      currentPlaySpeed = +btn.dataset.speed;
    };
  });
}

function updatePlayBtn(isPlaying) {
  const playBtn = $("#tl-play");
  const text = $("#tl-play-text");
  if (!playBtn) return;
  if (isPlaying) {
    playBtn.innerHTML = `
      <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>
      <span id="tl-play-text">Pause</span>`;
  } else {
    playBtn.innerHTML = `
      <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
      <span id="tl-play-text">Play Replay</span>`;
  }
}

function setScrubberPosition(t, span) {
  const needle = $("#scrubber-needle");
  const timeLbl = $("#tl-playback-time");
  if (timeLbl) timeLbl.textContent = `${Math.round(t)} ms`;

  if (needle) {
    needle.classList.remove("hidden");
    needle.style.left = `calc(110px + (100% - 110px) * ${t / span})`;
  }
}

function highlightAtTime(t) {
  // Highlight active markers & bars
  $$(".mk").forEach((m) => {
    const mt = +m.dataset.t;
    m.classList.toggle("highlighted", Math.abs(mt - t) < 60);
  });

  $$(".bar").forEach((b) => {
    const t0 = +b.dataset.t0, t1 = +b.dataset.t1;
    b.classList.toggle("highlighted", t >= t0 && t <= t1);
  });

  // Highlight nearest transcript row
  $$("#transcript li").forEach((li) => {
    const rowT = +li.dataset.t;
    const isNear = Math.abs(rowT - t) < 60;
    li.classList.toggle("highlighted", isNear);
    if (isNear) {
      li.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  });
}

// ---------------------------------------------------------------- Score Metrics Deck
function renderScore(s, res) {
  const strip = $("#score-strip"), box = $("#checks");
  if (!s) {
    strip.classList.add("hidden");
    box.innerHTML = "<p class='muted'>No ground truth specification available for this scenario.</p>";
    return;
  }

  strip.classList.remove("hidden");
  const cfg = (res && res.config) || {}, st = (res && res.status) || {};
  const warnings = [];

  if (!cfg.official) warnings.push(`Development Preview (${cfg.time_scale}× speed) — Virtual clock accelerated`);
  if (st.missing_media && st.missing_media.length) warnings.push("Missing input media files: " + st.missing_media.join(", "));
  if (st.runner_stopped_with_pending_calls && st.runner_stopped_with_pending_calls.length) {
    warnings.push("Runner halted with uncancelled pending calls: " + st.runner_stopped_with_pending_calls.join(", "));
  }
  if (st.agent_crash && st.agent_crash.length) warnings.push("Agent exception detected: " + st.agent_crash.join("; "));

  const b = s.breakdown;
  const cell = (title, data) => {
    if (!data) {
      return `<div><div class="k">${title}</div><div class="v muted">—</div></div>`;
    }
    const pct = Math.round(100 * data.fraction);
    return `
      <div>
        <div class="k">${title}</div>
        <div class="v">${data.points.toFixed(1)} <span class="denom">/ ${data.weight.toFixed(0)}</span></div>
        <div class="meter"><i style="width:${pct}%"></i></div>
      </div>`;
  };

  const totalScore = s.total;
  let scoreTag = "score-tag good";
  let scoreVerdict = "PASSED";
  if (totalScore >= 98) {
    scoreTag = "score-tag perfect";
    scoreVerdict = "OPTIMAL COMPLIANCE";
  } else if (totalScore < 60) {
    scoreTag = "score-tag fail";
    scoreVerdict = "DEFICIENT";
  } else if (totalScore < 85) {
    scoreTag = "score-tag partial";
    scoreVerdict = "PARTIAL RESOLUTION";
  }

  strip.innerHTML = `
    <div class="total">
      <div class="k">Evaluation Result</div>
      <div class="v">${s.total.toFixed(1)} <span class="denom">/ 100</span></div>
      <span class="${scoreTag}">${scoreVerdict}</span>
    </div>
    ${cell("Task Execution", b.task)}
    ${cell("Interrupt Recovery", b.recovery)}
    ${cell("Latency Overhead", b.latency)}
    ${cell("Safety & State", b.safety)}
  `;

  // Render Checkpoints
  const out = [];
  if (b.task) {
    out.push(group("Task Completion & Tool Grounding", b.task.detail.checkpoints.map((c) =>
      ck(c.passed, c.id, `weight ${c.weight}`, c.note)
    )));
  }
  if (b.recovery) {
    out.push(group("Interruption Handling & Recovery", b.recovery.detail.checks.map((c) =>
      ck(c.passed, c.check, "", c.note)
    )));
  }
  if (b.latency) {
    out.push(group("Latency Constraints & Responsiveness", b.latency.detail.responses.map((r) =>
      ck(r.fraction >= 0.99, `Event #${r.event_index}`, r.delta_ms == null ? "No reply issued" : `${Math.round(r.delta_ms)} ms response`, "")
    )));
  }
  if (b.safety) {
    out.push(group("Safety, Fillers & Idempotency", b.safety.detail.notes.map((n) =>
      ck(n === "clean", n === "clean" ? "Clean execution (No violations)" : n, "", "")
    )));
  }

  const warnHtml = warnings.map((w) => `<div class="warn-box">⚠ ${esc(w)}</div>`).join("");
  box.innerHTML = `
    <div style="margin-bottom:12px;font-size:12px;color:var(--muted);font-family:var(--font-mono)">
      Configuration: ${esc(cfg.agent)} · ${cfg.time_scale}× scale · tail ${cfg.tail_ms}ms ${cfg.official ? "(Official Scorer Standard)" : ""}
    </div>
    ${warnHtml}
    ${out.join("")}
  `;
}

const group = (title, items) => `
  <div class="ck-group">
    <h4>${title}</h4>
    ${items.join("")}
  </div>`;

const ck = (ok, name, right, note) => `
  <div class="ck ${ok ? "pass" : "fail"}">
    <span class="d"></span>
    <span class="ck-name">${esc(name)}</span>
    <span class="n">${esc(right)}</span>
    ${note ? `<span class="note">${esc(note)}</span>` : ""}
  </div>`;

// ---------------------------------------------------------------- Checkpoint Filter Chips
$$("#ck-filters .filter-chip").forEach((btn) => {
  btn.onclick = () => {
    $$("#ck-filters .filter-chip").forEach((b) => b.classList.toggle("active", b === btn));
    const mode = btn.dataset.filter;
    $$(".ck").forEach((item) => {
      if (mode === "all") item.classList.remove("hidden");
      else if (mode === "fail") item.classList.toggle("hidden", !item.classList.contains("fail"));
      else if (mode === "pass") item.classList.toggle("hidden", !item.classList.contains("pass"));
    });
  };
});

// ---------------------------------------------------------------- Transcript Search
const trSearch = $("#tr-search");
if (trSearch) {
  trSearch.addEventListener("input", (e) => {
    const q = e.target.value.toLowerCase().trim();
    $$("#transcript li").forEach((li) => {
      const txt = li.textContent.toLowerCase();
      li.style.display = (!q || txt.includes(q)) ? "" : "none";
    });
  });
}

// ---------------------------------------------------------------- Copy Transcript
const copyBtn = $("#copy-transcript");
if (copyBtn) {
  copyBtn.onclick = () => {
    const listItems = $$("#transcript li");
    if (!listItems.length) return;
    const text = listItems.map((li) => {
      const t = $(".t", li)?.textContent || "";
      const who = $(".who", li)?.textContent || "";
      const msg = $(".msg", li)?.textContent || "";
      return `[${t}] ${who}: ${msg}`;
    }).join("\n");

    navigator.clipboard.writeText(text).then(() => {
      const orig = copyBtn.textContent;
      copyBtn.textContent = "Copied!";
      setTimeout(() => copyBtn.textContent = orig, 1800);
    });
  };
}

// ---------------------------------------------------------------- Tooltips
const tip = $("#tip");
function showTip(e) {
  const t = e.target.closest("[data-tip]");
  if (!t) { tip.classList.add("hidden"); return; }
  tip.textContent = t.dataset.tip;
  tip.classList.remove("hidden");
  const w = tip.offsetWidth;
  tip.style.left = Math.min(e.clientX + 14, window.innerWidth - w - 16) + "px";
  tip.style.top = (e.clientY + 16) + "px";
}

document.addEventListener("mousemove", showTip);
document.addEventListener("click", showTip);

// ---------------------------------------------------------------- Compose View (Sandbox)
const atSlider = $("#c-at-slider");
const atInput = $("#c-at");
const atLabel = $("#c-at-label");

if (atSlider && atInput) {
  atSlider.oninput = (e) => {
    atInput.value = e.target.value;
    if (atLabel) atLabel.textContent = `+${Number(e.target.value).toLocaleString()} ms`;
  };
  atInput.oninput = (e) => {
    atSlider.value = e.target.value;
    if (atLabel) atLabel.textContent = `+${Number(e.target.value).toLocaleString()} ms`;
  };
}

$$(".chip").forEach((c) => {
  c.onclick = () => {
    const [u, i, at, tool] = JSON.parse(c.dataset.p);
    $("#c-utt").value = u;
    $("#c-int").value = i;
    $("#c-at").value = at || 1600;
    if (atSlider) atSlider.value = at || 1600;
    if (atLabel) atLabel.textContent = `+${Number(at || 1600).toLocaleString()} ms`;
    $("#c-tool").checked = tool;
  };
});

let composing = false;
$("#c-run").onclick = async () => {
  if (composing) return;
  composing = true;
  const btn = $("#c-run");
  btn.classList.add("busy");
  btn.innerHTML = `
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="spin"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
    <span>Executing…</span>`;
  btn.disabled = true;

  const utt = $("#c-utt").value.trim();
  const it = $("#c-int").value.trim();
  const at = +$("#c-at").value || 1600;
  const words = utt.split(" ");
  const half = Math.ceil(words.length / 2);

  const events = [
    { timestamp_ms: 100, event_type: "user_speech_chunk", payload: { text: words.slice(0, half).join(" ") + " ", end_of_turn: false } },
    { timestamp_ms: 700, event_type: "user_speech_chunk", payload: { text: words.slice(half).join(" "), end_of_turn: true } },
  ];

  if (it) {
    events.push({ timestamp_ms: 700 + at, event_type: "interruption", payload: { text: it } });
  }

  const scenario = { scenario_id: "composed-interactive", metadata: { modality: "text" }, events };

  if ($("#c-tool").checked) {
    scenario.tool_manifest = {
      hotel_search: {
        kind: "read_only",
        delay_range_ms: [900, 1800],
        description: "Search hotels in a target city.",
        args: {
          city: { type: "string", required: true },
          nights: { type: "number", required: false }
        },
        default_result: {
          hotels: [
            { hotel_id: "HT-0042", name: "Harborview Medical Suites", price_usd: 179 },
            { hotel_id: "HT-0043", name: "Beacon Health Inn", price_usd: 219 }
          ]
        }
      }
    };
  }

  try {
    const res = await api("/api/run", { scenario, agent: AGENT, time_scale: 1 });
    renderAll(res, "#c-timeline", "#c-transcript");
  } catch (e) {
    $("#c-timeline").innerHTML = `<div class="warn-box" style="margin:16px;">Failed: ${esc(e.message)}</div>`;
    $("#c-transcript").innerHTML = "<li class='muted'>No events recorded due to execution error.</li>";
  }

  composing = false;
  btn.classList.remove("busy");
  btn.innerHTML = `
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
    <span>Run conversation</span>`;
  btn.disabled = false;
};

// ---------------------------------------------------------------- Suite Benchmark View
$("#suite-btn").onclick = async () => {
  const btn = $("#suite-btn");
  btn.disabled = true;
  const tb = $("#suite-table tbody"), tf = $("#suite-table tfoot");

  tb.innerHTML = SCEN.map((s, i) => `
    <tr id="r${i}">
      <td class="id">${esc(s.id)}</td>
      <td><span class="tag mod-${(s.modality || "text").toLowerCase()}">${esc(s.modality || "text")}</span></td>
      <td class="num b muted">—</td>
      <td class="num t muted">—</td>
      <td class="num dl">—</td>
      <td><div class="dual"><i class="b" style="width:0"></i><i class="t" style="width:0"></i></div></td>
    </tr>
  `).join("");

  const sums = { b: 0, t: 0, n: 0, fail: 0, missing: 0 };

  for (let i = 0; i < SCEN.length; i++) {
    btn.innerHTML = `
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="spin"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
      <span>Running ${i + 1} of ${SCEN.length}…</span>`;
    const row = $("#r" + i);
    if (!row) continue;
    row.style.backgroundColor = "var(--surface-subtle)";

    try {
      const [b, t] = await Promise.all([
        api("/api/run", { path: SCEN[i].path, agent: "baseline", time_scale: 1 }),
        api("/api/run", { path: SCEN[i].path, agent: "triageline", time_scale: 1 })
      ]);

      if ((t.status?.missing_media || []).length) {
        sums.missing++;
        $(".id", row).innerHTML += ' <span class="media-warn" style="font-size:10px">(Missing Media)</span>';
      }

      const bs = b.score?.total ?? 0;
      const ts = t.score?.total ?? 0;

      $(".b", row).textContent = bs.toFixed(1);
      $(".b", row).classList.remove("muted");
      $(".t", row).textContent = ts.toFixed(1);
      $(".t", row).classList.remove("muted");

      const d = ts - bs;
      $(".dl", row).innerHTML = `<span class="${d >= 0 ? "pos" : "neg"}">${d >= 0 ? "+" : ""}${d.toFixed(1)}</span>`;

      $(".dual i.b", row).style.width = bs + "%";
      $(".dual i.t", row).style.width = ts + "%";

      sums.b += bs;
      sums.t += ts;
      sums.n++;

      // Update Top KPI Cards
      const kpiCount = $("#kpi-count");
      const kpiTl = $("#kpi-triageline");
      const kpiBs = $("#kpi-baseline");
      const kpiDelta = $("#kpi-delta");

      if (kpiCount) kpiCount.textContent = `${sums.n} / ${SCEN.length}`;
      if (kpiTl) kpiTl.textContent = (sums.t / sums.n).toFixed(1);
      if (kpiBs) kpiBs.textContent = (sums.b / sums.n).toFixed(1);
      if (kpiDelta) {
        const netD = (sums.t / sums.n) - (sums.b / sums.n);
        kpiDelta.innerHTML = `<span class="${netD >= 0 ? "pos" : "neg"}">${netD >= 0 ? "+" : ""}${netD.toFixed(1)} pts</span>`;
      }
    } catch (e) {
      sums.fail++;
      $(".t", row).textContent = "Error";
      $(".dl", row).innerHTML = `<span class="neg small">${esc(e.message)}</span>`;
    } finally {
      row.style.backgroundColor = "";
    }
  }

  const note = `${SCEN.length} attempted · ${sums.n} evaluated · ${sums.fail} failed · ${sums.missing} missing media · 1× official standard`;
  if (!sums.n) {
    tf.innerHTML = `<tr><td colspan="6" class="neg">No completed benchmark runs. ${note}</td></tr>`;
  } else {
    const mb = sums.b / sums.n;
    const mt = sums.t / sums.n;
    const d = mt - mb;
    tf.innerHTML = `
      <tr>
        <td><strong>Aggregate Mean Performance</strong></td>
        <td></td>
        <td class="num">${mb.toFixed(1)}</td>
        <td class="num">${mt.toFixed(1)}</td>
        <td class="num"><span class="${d >= 0 ? "pos" : "neg"}">${d >= 0 ? "+" : ""}${d.toFixed(1)}</span></td>
        <td></td>
      </tr>
      <tr><td colspan="6" class="small muted" style="padding-top:4px">${note}</td></tr>
    `;
  }

  btn.innerHTML = `
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
    <span>Run all scenarios</span>`;
  btn.disabled = false;
};

// Start by loading all scenario metadata
loadScenarios();
