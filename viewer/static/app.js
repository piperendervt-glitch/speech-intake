"use strict";
// speech-intake 閲覧画面。外部ライブラリなし（localhost だけで動く）。

const $ = (id) => document.getElementById(id);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const SPK_COLORS = ["#e8590c", "#1c7ed6", "#2f9e44", "#ae3ec9", "#f59f00", "#0ca678", "#d6336c", "#5c7cfa"];
const LANES = { wave: 110, spk: 18, text: 46, words: 38 };

const S = {
  sessions: [],
  sid: null,
  meta: null,
  peaks: null, // {data: Int16Array(min,max,...), pps, duration}
  runs: [null, null], // {name, info, utterances, trace}
  view: { t0: 0, dur: 10 },
  play: null, // {t0, t1|null}
  hover: null,
  spkIndex: {},
  pollTimer: null,
};

// ---------- data ----------
async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}

async function loadSessions(keep = true) {
  S.sessions = await getJSON("/api/sessions");
  const sel = $("session");
  const prev = keep ? S.sid : null;
  sel.innerHTML = "";
  for (const s of S.sessions) {
    const o = document.createElement("option");
    const m = s.meta || {};
    const dur = m.duration != null ? `${fmtTime(m.duration)}` : m.status === "recording" ? "録音中" : "";
    o.value = s.session;
    o.textContent = `${s.session}  ${dur}  ${m.source || ""}${m.original ? " (" + m.original + ")" : ""}`;
    sel.appendChild(o);
  }
  const want = prev || new URLSearchParams(location.search).get("session") || (S.sessions[0] && S.sessions[0].session);
  if (want) sel.value = want;
  if (!S.sessions.length) setStatus("セッションがありません（python -m intake record / import で作る）");
}

function fillRunSelects() {
  const s = S.sessions.find((x) => x.session === S.sid);
  const runs = s ? s.runs : [];
  const q = new URLSearchParams(location.search);
  for (const [id, allowNone, idx] of [["runA", false, 0], ["runB", true, 1]]) {
    const sel = $(id);
    const prev = (S.runs[idx] && S.runs[idx].name) || q.get(id) || sel.value;
    sel.innerHTML = "";
    if (allowNone || !runs.length) {
      const o = document.createElement("option");
      o.value = "";
      o.textContent = allowNone ? "（比較しない）" : "（結果なし：波形のみ）";
      sel.appendChild(o);
    }
    for (const r of runs) {
      const o = document.createElement("option");
      o.value = r.run;
      const p = r.providers || {};
      o.textContent = `${r.run}  [vad:${p.vad || "?"} stt:${p.stt || "?"} spk:${p.speaker || "?"}]`;
      sel.appendChild(o);
    }
    if (prev && runs.some((r) => r.run === prev)) sel.value = prev;
    else if (!allowNone && runs.length) sel.value = runs.some((r) => r.run === "default") ? "default" : runs[0].run;
  }
}

async function loadPeaks() {
  const r = await fetch(`/api/sessions/${S.sid}/peaks`, { cache: "no-store" });
  if (!r.ok) { S.peaks = null; return; }
  const buf = await r.arrayBuffer();
  const pps = +r.headers.get("X-Peaks-Per-Sec");
  const frames = +r.headers.get("X-Frames");
  const sr = +r.headers.get("X-Sample-Rate");
  S.peaks = { data: new Int16Array(buf), pps, duration: frames / sr };
}

async function loadRun(idx) {
  const name = $(idx === 0 ? "runA" : "runB").value;
  if (!name) { S.runs[idx] = null; return; }
  const d = await getJSON(`/api/sessions/${S.sid}/runs/${encodeURIComponent(name)}`);
  S.runs[idx] = { name, ...d };
}

function indexSpeakers() {
  const names = new Set();
  for (const r of S.runs) if (r) for (const u of r.utterances) if (u.speaker) names.add(u.speaker);
  S.spkIndex = {};
  [...names].sort().forEach((n, i) => (S.spkIndex[n] = SPK_COLORS[i % SPK_COLORS.length]));
}

async function openSession(sid, resetView = true) {
  S.sid = sid;
  S.meta = (S.sessions.find((x) => x.session === sid) || {}).meta || {};
  const audio = $("audio");
  stop();
  audio.src = `/api/sessions/${sid}/audio?ts=${Date.now()}`;
  fillRunSelects();
  await Promise.all([loadPeaks(), loadRun(0), loadRun(1)]);
  indexSpeakers();
  if (resetView) fitView();
  updateUrl();
  buildPanels();
  setStatus(statusText());
  schedulePoll();
}

function statusText() {
  const m = S.meta || {};
  const d = S.peaks ? fmtTime(S.peaks.duration) : "?";
  const n = S.runs.map((r) => (r ? `${r.name}: 発話 ${r.utterances.length} 件` : null)).filter(Boolean).join(" / ");
  return `長さ ${d}  ${m.status === "recording" ? "● 録音中（自動更新）" : ""}  ${n}`;
}

function schedulePoll() {
  clearTimeout(S.pollTimer);
  if ((S.meta || {}).status !== "recording") return;
  S.pollTimer = setTimeout(async () => {
    try {
      await loadSessions(true);
      S.meta = (S.sessions.find((x) => x.session === S.sid) || {}).meta || {};
      const atEnd = S.peaks && S.view.t0 + S.view.dur >= S.peaks.duration - 0.5;
      await Promise.all([loadPeaks(), loadRun(0), loadRun(1)]);
      indexSpeakers();
      if (atEnd && S.peaks) S.view.t0 = Math.max(0, S.peaks.duration - S.view.dur);
      buildTables();
      drawAll();
      setStatus(statusText());
    } finally {
      schedulePoll();
    }
  }, 2000);
}

function updateUrl() {
  const q = new URLSearchParams();
  q.set("session", S.sid);
  if ($("runA").value) q.set("runA", $("runA").value);
  if ($("runB").value) q.set("runB", $("runB").value);
  history.replaceState(null, "", "?" + q.toString());
}

// ---------- view ----------
function duration() {
  return S.peaks ? Math.max(S.peaks.duration, 0.1) : 10;
}
function fitView() {
  S.view.t0 = 0;
  S.view.dur = duration();
}
function clampView() {
  const D = duration();
  S.view.dur = Math.min(Math.max(S.view.dur, 0.05), Math.max(D, 0.05));
  S.view.t0 = Math.min(Math.max(S.view.t0, 0), Math.max(0, D - S.view.dur));
}
function zoomAt(factor, tCenter) {
  const c = tCenter == null ? S.view.t0 + S.view.dur / 2 : tCenter;
  const frac = (c - S.view.t0) / S.view.dur;
  S.view.dur *= factor;
  clampView();
  S.view.t0 = c - frac * S.view.dur;
  clampView();
  drawAll();
}
const tToX = (t, w) => ((t - S.view.t0) / S.view.dur) * w;
const xToT = (x, w) => S.view.t0 + (x / w) * S.view.dur;

function fmtTime(t) {
  if (t == null || isNaN(t)) return "";
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `${m}:${s.toFixed(2).padStart(5, "0")}`;
}

// ---------- panels ----------
function panelLayout() {
  const L = [{ key: "wave", h: LANES.wave }];
  if ($("tSpk").checked) L.push({ key: "spk", h: LANES.spk });
  if ($("tText").checked) L.push({ key: "text", h: LANES.text });
  if ($("tWords").checked) L.push({ key: "words", h: LANES.words });
  let y = 0;
  for (const l of L) { l.y = y; y += l.h; }
  return { lanes: L, height: y };
}

function buildPanels() {
  const root = $("panels");
  root.innerHTML = "";
  const list = S.runs[1] ? [0, 1] : [0];
  for (const idx of list) {
    const r = S.runs[idx];
    const div = document.createElement("div");
    div.className = "panel";
    const h = document.createElement("h3");
    const label = idx === 0 ? "A" : "B";
    if (r) {
      const p = r.info.providers || {};
      h.innerHTML = `${label}: ${esc(r.name)}<span class="muted">vad=${esc(p.vad)} / stt=${esc(p.stt)} / speaker=${esc(p.speaker)} / 発話 ${r.utterances.length} 件 / ${esc(r.info.source || "")}</span>`;
    } else {
      h.innerHTML = `${label}: 波形のみ<span class="muted">この録音はまだ流し直していない（python -m intake replay ${esc(S.sid)}）</span>`;
    }
    const cv = document.createElement("canvas");
    cv.dataset.idx = idx;
    div.append(h, cv);
    root.appendChild(div);
    attachPointer(cv);
  }
  buildTables();
  drawAll();
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

function buildTables() {
  const root = $("tables");
  root.innerHTML = "";
  root.className = "tables";
  S.runs.forEach((r, idx) => {
    if (!r) return;
    const d = document.createElement("details");
    d.open = r.utterances.length > 0 && r.utterances.length < 400;
    d.innerHTML = `<summary>${idx === 0 ? "A" : "B"}: ${esc(r.name)} の発話レコード（${r.utterances.length} 件）</summary>`;
    const t = document.createElement("table");
    t.innerHTML = "<thead><tr><th>id</th><th>t0</th><th>t1</th><th>話者</th><th>文字</th></tr></thead>";
    const tb = document.createElement("tbody");
    for (const u of r.utterances) {
      const tr = document.createElement("tr");
      tr.className = "utt";
      const col = u.speaker ? S.spkIndex[u.speaker] : null;
      tr.innerHTML = `<td class="num">${esc(u.id)}</td><td class="num">${u.t0.toFixed(2)}</td><td class="num">${u.t1.toFixed(2)}</td>` +
        `<td>${col ? `<span class="swatch" style="background:${col}"></span>` : ""}${esc(u.speaker || "")}${u.speaker_conf != null ? ` <span class="muted">${u.speaker_conf.toFixed(2)}</span>` : ""}</td><td>${esc(u.text)}</td>`;
      tr.onclick = () => { focusRange(u.t0, u.t1); playRange(u.t0, u.t1); };
      tr.title = JSON.stringify(u);
      tb.appendChild(tr);
    }
    t.appendChild(tb);
    d.appendChild(t);
    root.appendChild(d);
  });
}

function focusRange(t0, t1) {
  if (t0 >= S.view.t0 && t1 <= S.view.t0 + S.view.dur) return;
  S.view.t0 = t0 - S.view.dur * 0.2;
  clampView();
  drawAll();
}

// ---------- drawing ----------
function sizeCanvas(cv, h) {
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth || cv.parentElement.clientWidth;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
    cv.width = Math.round(w * dpr);
    cv.height = Math.round(h * dpr);
    cv.style.height = h + "px";
  }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h };
}

function drawAll() {
  drawRuler();
  const layout = panelLayout();
  for (const cv of document.querySelectorAll("#panels canvas")) drawPanel(cv, layout);
  $("clock").textContent = `${fmtTime(S.view.t0)} – ${fmtTime(S.view.t0 + S.view.dur)}` + (S.play ? `  ▶ ${fmtTime($("audio").currentTime)}` : "");
}

function niceStep(dur, w) {
  const target = dur / (w / 90);
  const steps = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600];
  return steps.find((s) => s >= target) || 3600;
}

function drawRuler() {
  const cv = $("ruler");
  const { ctx, w, h } = sizeCanvas(cv, 24);
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = css("--muted");
  ctx.strokeStyle = css("--line");
  ctx.font = "11px ui-monospace, Consolas, monospace";
  const step = niceStep(S.view.dur, w);
  const start = Math.ceil(S.view.t0 / step) * step;
  for (let t = start; t <= S.view.t0 + S.view.dur; t += step) {
    const x = Math.round(tToX(t, w)) + 0.5;
    ctx.beginPath(); ctx.moveTo(x, 14); ctx.lineTo(x, 24); ctx.stroke();
    ctx.fillText(step < 1 ? t.toFixed(2) : fmtTime(t).replace(/\.00$/, ""), x + 3, 11);
  }
  drawPlayhead(ctx, w, h);
}

function drawPlayhead(ctx, w, h) {
  if (!S.play) return;
  const x = tToX($("audio").currentTime, w);
  if (x < 0 || x > w) return;
  ctx.strokeStyle = css("--play");
  ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
  ctx.lineWidth = 1;
}

function drawWave(ctx, w, y, h) {
  ctx.strokeStyle = css("--line");
  ctx.beginPath(); ctx.moveTo(0, y + h / 2 + 0.5); ctx.lineTo(w, y + h / 2 + 0.5); ctx.stroke();
  if (!S.peaks) return;
  const { data, pps } = S.peaks;
  const n = data.length / 2;
  const mid = y + h / 2;
  const amp = (h / 2 - 2) / 32768;
  ctx.fillStyle = css("--wave");
  for (let px = 0; px < w; px++) {
    const a = Math.floor(xToT(px, w) * pps);
    const b = Math.max(a + 1, Math.floor(xToT(px + 1, w) * pps));
    if (b <= 0 || a >= n) continue;
    let lo = 32767, hi = -32768;
    for (let i = Math.max(a, 0); i < Math.min(b, n); i++) {
      if (data[2 * i] < lo) lo = data[2 * i];
      if (data[2 * i + 1] > hi) hi = data[2 * i + 1];
    }
    if (hi < lo) continue;
    ctx.fillRect(px, mid - hi * amp, 1, Math.max(1, (hi - lo) * amp));
  }
}

function visible(t0, t1) {
  return t1 >= S.view.t0 && t0 <= S.view.t0 + S.view.dur;
}

function clipText(ctx, text, maxW) {
  if (ctx.measureText(text).width <= maxW) return text;
  let lo = 0, hi = text.length;
  while (lo < hi) {
    const m = (lo + hi + 1) >> 1;
    if (ctx.measureText(text.slice(0, m) + "…").width <= maxW) lo = m; else hi = m - 1;
  }
  return lo > 0 ? text.slice(0, lo) + "…" : "";
}

function drawPanel(cv, layout) {
  const idx = +cv.dataset.idx;
  const r = S.runs[idx];
  const { ctx, w, h } = sizeCanvas(cv, layout.height);
  ctx.clearRect(0, 0, w, h);
  const hits = [];
  const spkOn = $("tSpk").checked;
  for (const L of layout.lanes) {
    ctx.strokeStyle = css("--line");
    if (L.y > 0) { ctx.beginPath(); ctx.moveTo(0, L.y + 0.5); ctx.lineTo(w, L.y + 0.5); ctx.stroke(); }
    if (L.key === "wave") {
      drawWave(ctx, w, L.y, L.h);
      if (r && $("tVad").checked) {
        for (const s of (r.trace.vad || [])) {
          if (!visible(s.t0, s.t1)) continue;
          const x0 = tToX(s.t0, w), x1 = tToX(s.t1, w);
          ctx.fillStyle = css("--vad");
          ctx.fillRect(x0, L.y, Math.max(1, x1 - x0), L.h);
          ctx.fillStyle = css("--vad-edge");
          ctx.fillRect(x0, L.y, Math.max(1, x1 - x0), 3);
          hits.push({ x0, x1, y0: L.y, y1: L.y + 6, t0: s.t0, t1: s.t1, rec: s, kind: "VAD 区間" });
        }
      }
    }
    if (!r) continue;
    if (L.key === "spk") {
      ctx.font = "11px system-ui, sans-serif";
      for (const u of r.utterances) {
        if (!u.speaker || !visible(u.t0, u.t1)) continue;
        const x0 = tToX(u.t0, w), x1 = tToX(u.t1, w);
        ctx.fillStyle = S.spkIndex[u.speaker] || "#888";
        ctx.globalAlpha = u.speaker_conf != null ? 0.35 + 0.65 * u.speaker_conf : 1;
        ctx.fillRect(x0, L.y + 3, Math.max(1, x1 - x0), L.h - 6);
        ctx.globalAlpha = 1;
        ctx.fillStyle = "#fff";
        const label = clipText(ctx, u.speaker, x1 - x0 - 4);
        if (label) ctx.fillText(label, x0 + 2, L.y + L.h - 5);
        hits.push({ x0, x1, y0: L.y, y1: L.y + L.h, t0: u.t0, t1: u.t1, rec: u, kind: "発話レコード" });
      }
    }
    if (L.key === "text") {
      ctx.font = "13px system-ui, sans-serif";
      for (const u of r.utterances) {
        if (!visible(u.t0, u.t1)) continue;
        const x0 = tToX(u.t0, w), x1 = tToX(u.t1, w);
        const col = spkOn && u.speaker ? S.spkIndex[u.speaker] : null;
        ctx.fillStyle = css("--utt");
        ctx.fillRect(x0, L.y + 3, Math.max(1, x1 - x0), L.h - 6);
        ctx.strokeStyle = col || css("--utt-edge");
        ctx.lineWidth = col ? 2 : 1;
        ctx.strokeRect(x0 + 0.5, L.y + 3.5, Math.max(1, x1 - x0 - 1), L.h - 7);
        ctx.lineWidth = 1;
        ctx.fillStyle = css("--fg");
        const label = clipText(ctx, u.text || "（文字なし）", x1 - x0 - 6);
        if (label) ctx.fillText(label, x0 + 4, L.y + L.h / 2 + 5);
        hits.push({ x0, x1, y0: L.y, y1: L.y + L.h, t0: u.t0, t1: u.t1, rec: u, kind: "発話レコード" });
      }
    }
    if (L.key === "words") {
      ctx.font = "11px system-ui, sans-serif";
      let row = 0;
      for (const u of r.utterances) {
        for (const wd of u.words || []) {
          if (!visible(wd.t0, wd.t1)) continue;
          const x0 = tToX(wd.t0, w), x1 = tToX(wd.t1, w);
          const yy = L.y + 4 + (row % 2) * 16;
          row++;
          ctx.fillStyle = css("--utt");
          ctx.fillRect(x0, yy, Math.max(1, x1 - x0), 14);
          ctx.fillStyle = css("--utt-edge");
          ctx.fillRect(x0, yy, 1, 14);
          ctx.fillStyle = css("--fg");
          const label = clipText(ctx, wd.w, x1 - x0 - 3);
          if (label) ctx.fillText(label, x0 + 2, yy + 11);
          hits.push({ x0, x1, y0: yy, y1: yy + 14, t0: wd.t0, t1: wd.t1, rec: { ...wd, utt: u.id }, kind: "単語" });
        }
      }
    }
  }
  if (S.play && S.play.t1 != null) {
    const x0 = tToX(S.play.t0, w), x1 = tToX(S.play.t1, w);
    ctx.strokeStyle = css("--play");
    ctx.setLineDash([4, 3]);
    ctx.strokeRect(x0 + 0.5, 0.5, Math.max(1, x1 - x0 - 1), h - 1);
    ctx.setLineDash([]);
  }
  drawPlayhead(ctx, w, h);
  cv._hits = hits;
}

// ---------- interaction ----------
function hitAt(cv, x, y) {
  const hs = cv._hits || [];
  // 細い（短い）ものを優先
  let best = null;
  for (const hh of hs) {
    if (x >= hh.x0 - 1 && x <= hh.x1 + 1 && y >= hh.y0 && y <= hh.y1) {
      if (!best || hh.x1 - hh.x0 < best.x1 - best.x0) best = hh;
    }
  }
  return best;
}

function attachPointer(cv) {
  let drag = null;
  cv.addEventListener("pointerdown", (e) => {
    drag = { x: e.offsetX, t0: S.view.t0, moved: false };
    cv.setPointerCapture(e.pointerId);
  });
  cv.addEventListener("pointermove", (e) => {
    if (drag) {
      const dx = e.offsetX - drag.x;
      if (Math.abs(dx) > 3) drag.moved = true;
      if (drag.moved) {
        S.view.t0 = drag.t0 - (dx / cv.clientWidth) * S.view.dur;
        clampView();
        drawAll();
      }
      return;
    }
    const hh = hitAt(cv, e.offsetX, e.offsetY);
    const tip = $("tip");
    if (hh) {
      tip.hidden = false;
      tip.textContent = `${hh.kind}  ${hh.t0.toFixed(3)}–${hh.t1.toFixed(3)} s（クリックで再生）\n` + JSON.stringify(hh.rec, null, 1);
      const tx = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
      const ty = Math.min(e.clientY + 14, window.innerHeight - tip.offsetHeight - 8);
      tip.style.left = tx + "px";
      tip.style.top = ty + "px";
    } else {
      tip.hidden = true;
    }
  });
  cv.addEventListener("pointerleave", () => ($("tip").hidden = true));
  cv.addEventListener("pointerup", (e) => {
    const d = drag;
    drag = null;
    if (!d || d.moved) return;
    const hh = hitAt(cv, e.offsetX, e.offsetY);
    if (hh) playRange(hh.t0, hh.t1);
    else playRange(xToT(e.offsetX, cv.clientWidth), null);
  });
  cv.addEventListener("wheel", (e) => {
    e.preventDefault();
    if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
      const d = e.shiftKey ? e.deltaY : e.deltaX;
      S.view.t0 += (d / cv.clientWidth) * S.view.dur;
      clampView();
      drawAll();
    } else {
      zoomAt(Math.exp(e.deltaY * 0.0015), xToT(e.offsetX, cv.clientWidth));
    }
  }, { passive: false });
}

// ---------- audio ----------
function playRange(t0, t1) {
  const a = $("audio");
  S.play = { t0: Math.max(0, t0), t1 };
  a.currentTime = S.play.t0;
  a.play().catch((err) => setStatus("再生できない: " + err.message));
  $("play").textContent = "■ 停止";
  tick();
}
function stop() {
  const a = $("audio");
  if (!a.paused) a.pause();
  S.play = null;
  $("play").textContent = "▶ 再生";
  drawAll();
}
function tick() {
  if (!S.play) return;
  const a = $("audio");
  if (S.play.t1 != null && a.currentTime >= S.play.t1) { stop(); return; }
  if (a.ended || (a.paused && a.readyState >= 2 && a.currentTime > S.play.t0 + 0.05)) { stop(); return; }
  drawAll();
  requestAnimationFrame(tick);
}

function setStatus(t) {
  $("status").textContent = t;
}

// ---------- wiring ----------
$("session").onchange = () => { S.runs = [null, null]; openSession($("session").value); };
$("runA").onchange = async () => { await loadRun(0); indexSpeakers(); updateUrl(); buildPanels(); setStatus(statusText()); };
$("runB").onchange = async () => { await loadRun(1); indexSpeakers(); updateUrl(); buildPanels(); setStatus(statusText()); };
$("reload").onclick = async () => { await loadSessions(true); await openSession($("session").value, false); };
for (const id of ["tVad", "tText", "tWords", "tSpk"]) $(id).onchange = () => { buildTables(); drawAll(); };
$("zoomIn").onclick = () => zoomAt(0.5);
$("zoomOut").onclick = () => zoomAt(2);
$("zoomFit").onclick = () => { fitView(); drawAll(); };
$("play").onclick = () => (S.play ? stop() : playRange(S.view.t0, null));
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "SELECT" || e.target.tagName === "INPUT") return;
  if (e.code === "Space") { e.preventDefault(); S.play ? stop() : playRange(S.view.t0, null); }
  if (e.key === "+" || e.key === "=") zoomAt(0.5);
  if (e.key === "-") zoomAt(2);
});
window.addEventListener("resize", drawAll);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", drawAll);

(async () => {
  try {
    await loadSessions(false);
    if ($("session").value) await openSession($("session").value);
  } catch (err) {
    setStatus("読み込みに失敗: " + err.message);
  }
})();
