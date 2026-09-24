"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let SCEN = [], SEL = null, AGENT = "triageline";
const lastScores = {};

// ---------------------------------------------------------------- tabs
$$(".tab").forEach((b) => b.onclick = () => {
  $$(".tab").forEach((x) => x.classList.toggle("active", x === b));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + b.dataset.view));
});
$$("#agent-seg button").forEach((b) => b.onclick = () => {
  AGENT = b.dataset.agent;
  $$("#agent-seg button").forEach((x) => x.classList.toggle("on", x === b));
});

// ---------------------------------------------------------------- api
async function api(path, body) {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  const j = await r.json();
  if (j.error) throw new Error(j.error);
  return j;
}

// ---------------------------------------------------------------- scenario list
async function loadScenarios() {
  SCEN = await api("/api/scenarios");
  const ul = $("#scenario-list");
  ul.innerHTML = SCEN.map((s, i) => `
    <li data-i="${i}">
      <span class="sid">${esc(s.id)}</span>
      <span class="tag">${esc(s.modality || "text")} · ${esc(s.difficulty || "")}${s.path.includes("extra") ? " · extra" : ""}</span>
      <span class="sc" id="sc-${i}"></span>
    </li>`).join("");
  $$("li", ul).forEach((li) => li.onclick = () => select(+li.dataset.i));
  select(1);
}
function select(i) {
  SEL = SCEN[i];
  $$("#scenario-list li").forEach((li) => li.classList.toggle("sel", +li.dataset.i === i));
  $("#sc-title").textContent = SEL.id;
  $("#sc-meta").textContent = `${SEL.modality || "text"} · ${SEL.difficulty || ""} · ${SEL.events.length} events`;
  $("#sc-desc").textContent = SEL.description;
  $("#run-btn").disabled = false;
}

$("#run-btn").onclick = async () => {
  if (!SEL) return;
  const btn = $("#run-btn");
  btn.classList.add("busy"); btn.textContent = "Running…"; btn.disabled = true;
  try {
    const res = await api("/api/run", { path: SEL.path, agent: AGENT, time_scale: +$("#ts").value });
    renderAll(res, "#timeline", "#transcript");
    renderScore(res.score);
    const i = SCEN.indexOf(SEL);
    if (res.score) $("#sc-" + i).textContent = res.score.total.toFixed(0);
  } catch (e) {
    $("#timeline").innerHTML = `<span class="neg">${esc(e.message)}</span>`;
  } finally {
    btn.classList.remove("busy"); btn.textContent = "Run scenario"; btn.disabled = false;
  }
};

// ---------------------------------------------------------------- rendering
function parseTrace(trace) {
  const calls = {}, marks = [], rows = [];
  let tEnd = 0;
  for (const e of trace) {
    const t = e.t_ms ?? 0; tEnd = Math.max(tEnd, t);
    if (e.kind === "event") {
      const p = e.payload || {};
      if (e.event_type === "user_speech_chunk" || e.event_type === "user_audio_chunk" || e.event_type === "video_frame") {
        const txt = p.text || (p.audio_ref ? "🎙 " + p.audio_ref : "") || (p.image_ref ? "▣ " + p.image_ref : "");
        marks.push({ lane: "user", cls: "user", t, tip: `${e.event_type}\n${txt}` });
        rows.push({ t, who: "user", cls: "user", msg: esc(txt) + (p.end_of_turn === false ? ' <span class="muted">…</span>' : "") });
      } else if (e.event_type === "interruption") {
        marks.push({ lane: "user", cls: "int", t, tip: `interruption\n${p.text}`, vline: true });
        rows.push({ t, who: "barge-in", cls: "user", msg: `<b>${esc(p.text)}</b>` });
      }
    } else if (e.kind === "action") {
      const a = e.action;
      if (a === "tool_call") {
        calls[e.call_id] = { id: e.call_id, api: e.api_name, args: e.args, t0: t, t1: null, st: "pending" };
        rows.push({ t, who: "tool call", cls: "tool", msg: `<code>${esc(e.api_name)}</code> <span class="muted">${esc(shortArgs(e.args))}</span>` });
      } else if (a === "cancel_tool") {
        rows.push({ t, who: "cancel", cls: "cancel", msg: `<code>${esc(e.call_id || (e.payload || {}).call_id)}</code>` });
      } else {
        const txt = (e.payload || {}).text || "";
        const cls = a === "filler_speech" ? "fill" : a === "clarification_request" ? "clar" : "final";
        const lane = cls === "final" ? "answer" : "fast";
        const snap = e.state_snapshot ? JSON.stringify(e.state_snapshot.slots || {}) : "";
        marks.push({ lane, cls, t, tip: `${a}\n${txt}${snap ? "\nslots " + snap : ""}` });
        rows.push({ t, who: { fill: "filler", clar: "clarify", final: "answer" }[cls], cls: { fill: "filler", clar: "clar", final: "final" }[cls],
                    msg: esc(txt) + (snap && snap !== "{}" ? `<span class="snap">slots ${esc(snap)}</span>` : "") });
      }
    } else if (e.kind === "tool_completed") {
      const c = calls[e.call_id]; if (c) { c.t1 = t; c.st = e.status === "success" ? "ok" : "err"; c.res = e.result; }
      rows.push({ t, who: "result", cls: "tool", msg: `<code>${esc(e.api_name)}</code> ${e.status === "success" ? "✓" : "✕ " + esc((e.result || {}).error)} <span class="muted">${esc(shortArgs(e.result, 90))}</span>` });
    } else if (e.kind === "tool_cancelled") {
      const c = calls[e.call_id]; if (c) { c.t1 = t; c.st = "cancelled"; }
    } else if (e.kind === "tool_abandoned") {
      const c = calls[e.call_id]; if (c) { c.t1 = tEnd; c.st = "abandoned"; }
    }
  }
  rows.sort((a, b) => a.t - b.t);
  return { calls: Object.values(calls), marks, rows, tEnd };
}
function shortArgs(o, n = 70) {
  if (!o) return "";
  const s = JSON.stringify(o, (k, v) => Array.isArray(v) && v.length > 6 && typeof v[0] === "number" ? `[${v.length}-d embedding]` : v);
  return s.length > n ? s.slice(0, n) + "…" : s;
}

function renderAll(res, tlSel, trSel) {
  const { calls, marks, rows, tEnd } = parseTrace(res.trace);
  const span = Math.max(1000, Math.ceil((tEnd + 200) / 1000) * 1000);
  const x = (t) => (100 * t / span).toFixed(3) + "%";
  const lane = (name, inner) => `<div class="lane"><div class="ln">${name}</div><div class="track">${inner}</div></div>`;
  const mk = (m) => `<span class="mk ${m.cls}" style="left:${x(m.t)}" data-tip="${esc(`+${Math.round(m.t)} ms  ${m.tip}`)}"></span>`;
  const bars = calls.map((c) => {
    const t1 = c.t1 ?? tEnd;
    return `<span class="bar ${c.st === "cancelled" ? "cancelled" : c.st === "err" ? "err" : c.st === "abandoned" ? "abandoned" : ""}"
      style="left:${x(c.t0)};width:${x(Math.max(t1 - c.t0, 20))}"
      data-tip="${esc(`${c.id} ${c.api}  ${c.st}\n+${Math.round(c.t0)} → +${Math.round(t1)} ms\nargs ${shortArgs(c.args, 200)}`)}"><span>${esc(c.api)}</span></span>`;
  });
  // stack overlapping tool bars into separate slow-path lanes
  const lanes = [];
  calls.forEach((c, i) => {
    const t1 = c.t1 ?? tEnd;
    let L = lanes.findIndex((end) => end < c.t0 - 40);
    if (L < 0) { L = lanes.length; lanes.push(0); }
    lanes[L] = t1; c._lane = L; c._html = bars[i];
  });
  const slowLanes = (lanes.length ? lanes : [0]).map((_, L) =>
    lane(L === 0 ? "slow path" : "", calls.filter((c) => c._lane === L).map((c) => c._html).join("")));
  const ticks = [];
  const step = span > 8000 ? 2000 : 1000;
  for (let t = 0; t <= span; t += step) ticks.push(`<span class="tk" style="left:${x(t)}">${t / 1000}s</span>`);
  const vlines = marks.filter((m) => m.vline).map((m) => `<span class="vline" style="left:calc(92px + (100% - 92px) * ${m.t / span})"></span>`).join("");

  const tl = $(tlSel);
  tl.classList.remove("empty");
  tl.innerHTML = vlines +
    lane("user", marks.filter((m) => m.lane === "user").map(mk).join("")) +
    lane("fast path", marks.filter((m) => m.lane === "fast").map(mk).join("")) +
    slowLanes.join("") +
    lane("answer", marks.filter((m) => m.lane === "answer").map(mk).join("")) +
    `<div class="axis"><div></div><div class="track">${ticks.join("")}</div></div>` +
    `<div class="legend small">
       <span><i style="background:var(--user)"></i>user / barge-in</span>
       <span><i style="background:var(--fast)"></i>filler</span>
       <span><i style="background:var(--clar)"></i>clarify</span>
       <span><i style="background:var(--slow);border-radius:2px"></i>tool</span>
       <span><i style="background:var(--cancel);border-radius:2px"></i>cancelled</span>
       <span><i style="background:var(--ink)"></i>final answer</span></div>`;

  $(trSel).innerHTML = rows.map((r) =>
    `<li><span class="t">+${Math.round(r.t)}</span><span class="who ${r.cls}">${r.who}</span><span class="msg">${r.msg}</span></li>`).join("") || "<li class='muted'>no output</li>";
}

function renderScore(s) {
  const strip = $("#score-strip"), box = $("#checks");
  if (!s) { strip.classList.add("hidden"); box.innerHTML = "<p class='muted'>No ground truth for this run.</p>"; return; }
  strip.classList.remove("hidden");
  const b = s.breakdown;
  const cell = (k, d) => d ? `<div><div class="k">${k}</div><div class="v">${d.points.toFixed(1)}<span class="muted small"> / ${d.weight.toFixed(0)}</span></div><div class="meter"><i style="width:${100 * d.fraction}%"></i></div></div>`
                           : `<div><div class="k">${k}</div><div class="v muted">n/a</div></div>`;
  strip.innerHTML = `<div class="total"><div class="k">Score</div><div class="v">${s.total.toFixed(1)}</div></div>` +
    cell("task", b.task) + cell("recovery", b.recovery) + cell("latency", b.latency) + cell("safety", b.safety);

  const out = [];
  if (b.task) out.push(group("Task", b.task.detail.checkpoints.map((c) => ck(c.passed, c.id, `w ${c.weight}`, c.note))));
  if (b.recovery) out.push(group("Recovery", b.recovery.detail.checks.map((c) => ck(c.passed, c.check, "", c.note))));
  if (b.latency) out.push(group("Latency", b.latency.detail.responses.map((r) => ck(r.fraction >= 0.99, `event #${r.event_index}`, r.delta_ms == null ? "no reply" : `${Math.round(r.delta_ms)} ms`, ""))));
  if (b.safety) out.push(group("Safety", b.safety.detail.notes.map((n) => ck(n === "clean", n, "", ""))));
  box.innerHTML = out.join("");
}
const group = (t, items) => `<div><h4>${t}</h4>${items.join("")}</div>`;
const ck = (ok, name, right, note) =>
  `<div class="ck ${ok ? "pass" : "fail"}"><span class="d"></span><span>${esc(name)}</span><span class="n">${esc(right)}</span>${note ? `<span class="note">${esc(note)}</span>` : ""}</div>`;

// ---------------------------------------------------------------- tooltip
const tip = $("#tip");
document.addEventListener("mousemove", (e) => {
  const t = e.target.closest("[data-tip]");
  if (!t) { tip.classList.add("hidden"); return; }
  tip.textContent = t.dataset.tip; tip.classList.remove("hidden");
  const w = tip.offsetWidth;
  tip.style.left = Math.min(e.clientX + 12, innerWidth - w - 10) + "px";
  tip.style.top = e.clientY + 14 + "px";
});

// ---------------------------------------------------------------- compose
$$(".chip").forEach((c) => c.onclick = () => {
  const [u, i, at, tool] = JSON.parse(c.dataset.p);
  $("#c-utt").value = u; $("#c-int").value = i; $("#c-at").value = at || 1600; $("#c-tool").checked = tool;
});
$("#c-run").onclick = async () => {
  const btn = $("#c-run"); btn.classList.add("busy"); btn.textContent = "Running…";
  const utt = $("#c-utt").value.trim(), it = $("#c-int").value.trim(), at = +$("#c-at").value || 1600;
  const words = utt.split(" "), half = Math.ceil(words.length / 2);
  const events = [
    { timestamp_ms: 100, event_type: "user_speech_chunk", payload: { text: words.slice(0, half).join(" ") + " ", end_of_turn: false } },
    { timestamp_ms: 700, event_type: "user_speech_chunk", payload: { text: words.slice(half).join(" "), end_of_turn: true } },
  ];
  if (it) events.push({ timestamp_ms: 700 + at, event_type: "interruption", payload: { text: it } });
  const scenario = { scenario_id: "composed", metadata: { modality: "text" }, events };
  if ($("#c-tool").checked) scenario.tool_manifest = { hotel_search: { kind: "read_only", delay_range_ms: [900, 1800], description: "Search hotels in a city.",
    args: { city: { type: "string", required: true }, nights: { type: "number", required: false } },
    default_result: { hotels: [{ hotel_id: "HT-0042", name: "Harborview Inn", price_usd: 179 }] } } };
  try {
    const res = await api("/api/run", { scenario, agent: AGENT, time_scale: 2 });
    renderAll(res, "#c-timeline", "#c-transcript");
  } catch (e) { $("#c-timeline").innerHTML = `<span class="neg">${esc(e.message)}</span>`; }
  btn.classList.remove("busy"); btn.textContent = "Run conversation";
};

// ---------------------------------------------------------------- suite
$("#suite-btn").onclick = async () => {
  const btn = $("#suite-btn"); btn.disabled = true;
  const tb = $("#suite-table tbody"), tf = $("#suite-table tfoot");
  tb.innerHTML = SCEN.map((s, i) => `<tr id="r${i}"><td class="id">${esc(s.id)}</td><td>${esc(s.modality || "text")}</td><td class="num b">…</td><td class="num t">…</td><td class="dl"></td><td><div class="dual"><i class="b" style="width:0"></i><i class="t" style="width:0"></i></div></td></tr>`).join("");
  const sums = { b: 0, t: 0, n: 0 };
  for (let i = 0; i < SCEN.length; i++) {
    btn.textContent = `Running ${i + 1}/${SCEN.length}…`;
    const row = $("#r" + i);
    try {
      const [b, t] = [await api("/api/run", { path: SCEN[i].path, agent: "baseline", time_scale: 4 }),
                      await api("/api/run", { path: SCEN[i].path, agent: "triageline", time_scale: 4 })];
      const bs = b.score?.total ?? 0, ts = t.score?.total ?? 0;
      $(".b", row).textContent = bs.toFixed(1); $(".t", row).textContent = ts.toFixed(1);
      const d = ts - bs; $(".dl", row).innerHTML = `<span class="${d >= 0 ? "pos" : "neg"}">${d >= 0 ? "+" : ""}${d.toFixed(1)}</span>`;
      $(".dual i.b", row).style.width = bs + "%"; $(".dual i.t", row).style.width = ts + "%";
      sums.b += bs; sums.t += ts; sums.n++;
    } catch (e) { $(".t", row).textContent = "error"; }
  }
  const mb = sums.b / sums.n, mt = sums.t / sums.n;
  tf.innerHTML = `<tr><td>Mean</td><td></td><td class="num">${mb.toFixed(1)}</td><td class="num">${mt.toFixed(1)}</td><td><span class="pos">+${(mt - mb).toFixed(1)}</span></td><td></td></tr>`;
  btn.textContent = "Run all"; btn.disabled = false;
};

loadScenarios();
