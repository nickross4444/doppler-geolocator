'use strict';
const C = 343.0;
const $ = id => document.getElementById(id);
const api = async (u, opt) => { const r = await fetch(u, opt); if (!r.ok) throw new Error((await r.text()) || r.status); return r.json(); };
const post = (u, b) => api(u, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b || {})});
const S = {pid: null, proj: null, view: null, events: [], selEvent: null, trace: [], job: null, summary: null,
           selRank: 1, compare: [], matchCurves: {}, heat: null, info: null};
const COLORS = ['#4fc3f7', '#aed581', '#ffb74d', '#f06292', '#ba68c8', '#4db6ac', '#fff176'];

// ---------------------------------------------------------------- physics (same formulas as dopp/physics.py)
function gPosition(t, v, d, t0) { const tau = t - t0; const vr = v * v * tau / Math.sqrt(d * d + (v * tau) ** 2); return C / (C + vr); }
function gDelayed(t, v, d, t0) {   // retarded constant-velocity model, t0 = delayed CPA
  const tc = t0 - d / C, s = t - tc, a = C * C - v * v;
  const disc = Math.max(C ** 4 * s * s - a * (C * C * s * s - d * d), 0);
  const u = (C * C * s - Math.sqrt(disc)) / a; const r = Math.sqrt(d * d + (v * u) ** 2);
  return C / (C + v * v * u / r);
}
const readouts = (f0, v, d) => ({fpre: f0 * C / (C - v), fpost: f0 * C / (C + v), roc: f0 * v * v / (C * d)});

// ---------------------------------------------------------------- time zones
function tzOffsetMs(ms, tz) {
  const f = new Intl.DateTimeFormat('en-US', {timeZone: tz, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit'});
  const p = Object.fromEntries(f.formatToParts(new Date(ms)).map(x => [x.type, x.value]));
  return Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour % 24, +p.minute, +p.second) - Math.floor(ms / 1000) * 1000;
}
function localToUtc(str, tz) { const g = Date.parse(str + 'Z'); let u = g - tzOffsetMs(g, tz); u = g - tzOffsetMs(u, tz); return u / 1000; }
function utcToLocal(sec, tz) {
  const ms = sec * 1000 + tzOffsetMs(sec * 1000, tz); return new Date(ms).toISOString().slice(0, 23);
}
const fmtUtc = s => s == null ? '—' : new Date(s * 1000).toISOString().replace('T', ' ').slice(0, 23) + 'Z';
const fmtT = s => { const sg = s < 0 ? '-' : ''; s = Math.abs(s); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = (s % 60).toFixed(1).padStart(4, '0'); return sg + (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + x; };

// ---------------------------------------------------------------- projects
async function loadProjects(sel) {
  const ps = await api('/api/projects');
  $('projSel').innerHTML = ps.map(p => `<option value="${p.id}">${p.preset ? '★ ' : ''}${p.name}</option>`).join('');
  if (sel) $('projSel').value = sel;
  if (ps.length) await openProject($('projSel').value);
}
async function openProject(pid) {
  S.pid = pid; const r = await api(`/api/projects/${pid}`); S.proj = r; S.events = r.events; S.trace = []; S.selEvent = null;
  S.job = null; S.summary = null; S.compare = []; S.matchCurves = {};
  const m = r.meta;
  $('projInfo').textContent = `${fmtT(m.duration_s)} · ${m.source}` + (m.truth ? ` · ground truth ${m.truth[0].toFixed(6)}, ${m.truth[1].toFixed(6)}` : '') + (m.note ? ' · ' + m.note : '');
  $('tzSel').value = m.tz || 'UTC';
  $('startLocal').value = m.start_utc != null ? utcToLocal(m.start_utc, $('tzSel').value) : '';
  $('startUtc').textContent = m.start_utc != null ? 'UTC ' + fmtUtc(m.start_utc) : 'start time unknown (multi-event search does not need it)';
  if (m.day) $('daySel').value = m.day;
  $('player').src = `/api/projects/${pid}/audio.mp3`;
  S.view = {t0: r.spec.t0, t1: r.spec.t1, f0: r.spec.f0, f1: Math.min(r.spec.f1, 1000)};
  if (m.view) Object.assign(S.view, m.view);
  $('s_t0').min = r.spec.t0; $('s_t0').max = r.spec.t1;
  if (m.default_mode) $('modeSel').value = m.default_mode;
  $('pJets').checked = !!m.include_jets;
  if (m.region) { S.presetRegion = m.region; } else S.presetRegion = null;
  $('regionSel').value = m.region ? 'preset' : 'none';
  setSliders(m.sliders || {f0: 120, v: 100, d: 2000, t0: (S.view.t0 + S.view.t1) / 2});
  renderEvents(r.gaps); drawSpec(); await loadResults(); clearMap(); if (m.truth) map.setView(m.truth, 11);
}
$('projSel').onchange = e => openProject(e.target.value);
$('upForm').onsubmit = async e => {
  e.preventDefault(); $('upMsg').textContent = 'uploading + extracting audio (ffmpeg) + spectrogram…';
  try { const fd = new FormData(e.target); fd.append('tz', $('tzSel').value);
    const r = await fetch('/api/projects', {method: 'POST', body: fd}); if (!r.ok) throw new Error(await r.text());
    const m = await r.json(); $('upMsg').textContent = 'done'; await loadProjects(m.id);
  } catch (err) { $('upMsg').textContent = 'error: ' + err.message; }
};
$('saveStart').onclick = async () => {
  const v = $('startLocal').value; const tz = $('tzSel').value;
  const utc = v ? localToUtc(v.length === 16 ? v + ':00' : v, tz) : null;
  const m = await post(`/api/projects/${S.pid}/meta`, {start_utc: utc, tz, day: $('daySel').value});
  S.proj.meta = m; $('startUtc').textContent = utc != null ? 'UTC ' + fmtUtc(utc) : '';
  if (utc != null) { const d = new Date(utc * 1000).toISOString().slice(0, 10); if ([...$('daySel').options].some(o => o.value === d)) $('daySel').value = d; }
};
$('daySel').onchange = () => post(`/api/projects/${S.pid}/meta`, {day: $('daySel').value});

// ---------------------------------------------------------------- spectrogram view
const img = $('specImg'), ov = $('specOv'); let imgObj = null, imgKey = '';
function sizeCanvas() { const r = $('specWrap').getBoundingClientRect(); for (const c of [img, ov]) { c.width = r.width; c.height = r.height; } }
const X = t => (t - S.view.t0) / (S.view.t1 - S.view.t0) * ov.width;
const Y = f => (S.view.f1 - f) / (S.view.f1 - S.view.f0) * ov.height;
const T = x => S.view.t0 + x / ov.width * (S.view.t1 - S.view.t0);
const F = y => S.view.f1 - y / ov.height * (S.view.f1 - S.view.f0);
let specTimer = null;
function drawSpec() {
  if (!S.pid) return; sizeCanvas(); drawOverlay();
  clearTimeout(specTimer);
  specTimer = setTimeout(() => {
    const v = S.view, k = $('kindSel').value;
    const u = `/api/projects/${S.pid}/spec.png?t0=${v.t0}&t1=${v.t1}&f0=${v.f0}&f1=${v.f1}&w=${img.width}&h=${img.height}&kind=${k}` + (k === 'z' ? `&hi=${$('zmax').value}` : '');
    if (u === imgKey) return; imgKey = u;
    const im = new Image(); im.onload = () => { if (imgKey === u) { imgObj = im; img.getContext('2d').drawImage(im, 0, 0, img.width, img.height); } }; im.src = u;
  }, 60);
  if (imgObj) { const c = img.getContext('2d'); c.clearRect(0, 0, img.width, img.height); }
}
function curvePts(fn, t0, t1, n = 400) { const a = []; for (let i = 0; i <= n; i++) { const t = t0 + (t1 - t0) * i / n; a.push([t, fn(t)]); } return a; }
function polyline(ctx, pts, col, w = 1.5, dash = []) {
  ctx.strokeStyle = col; ctx.lineWidth = w; ctx.setLineDash(dash); ctx.beginPath(); let on = false;
  for (const [t, f] of pts) { if (!isFinite(f)) { on = false; continue; } const x = X(t), y = Y(f); on ? ctx.lineTo(x, y) : ctx.moveTo(x, y); on = true; }
  ctx.stroke(); ctx.setLineDash([]);
}
function drawOverlay() {
  const ctx = ov.getContext('2d'); ctx.clearRect(0, 0, ov.width, ov.height);
  const v = S.view; ctx.font = '11px sans-serif'; ctx.fillStyle = '#ccc';
  // axes ticks
  const span = v.t1 - v.t0, st = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800].find(s => span / s < 12) || 3600;
  for (let t = Math.ceil(v.t0 / st) * st; t <= v.t1; t += st) { ctx.fillRect(X(t), ov.height - 6, 1, 6); ctx.fillText(fmtT(t), X(t) + 2, ov.height - 8); }
  const fs = [5, 10, 20, 50, 100, 200, 500].find(s => (v.f1 - v.f0) / s < 10) || 1000;
  for (let f = Math.ceil(v.f0 / fs) * fs; f <= v.f1; f += fs) { ctx.fillRect(0, Y(f), 6, 1); ctx.fillText(f + ' Hz', 8, Y(f) + 4); }
  // events
  S.events.forEach((e, i) => {
    const col = e.selected ? COLORS[i % COLORS.length] : '#888';
    const w = 1.4 * (Math.abs(e.d) / Math.max(e.v, 1) * 3 + 40);
    if ($('showLines').checked && e.line) for (const [t, f] of e.line) { ctx.fillStyle = col; ctx.fillRect(X(t) - 1, Y(f) - 1, 2, 2); }
    polyline(ctx, curvePts(t => e.f0 * gDelayed(t, e.v, e.d, e.t0), e.t0 - 60, e.t0 + 60), col, e === S.selEvent ? 2.2 : 1, [4, 3]);
    ctx.fillStyle = col; ctx.fillText(`#${e.id}`, X(e.t0) + 3, Y(e.f0 * 1.04));
  });
  // slider curve
  const q = sliders(); const mode = document.querySelector('input[name=pmode]:checked').value;
  const both = $('showBoth').checked;
  if (mode === 'delayed' || both) polyline(ctx, curvePts(t => q.f0 * gDelayed(t, q.v, q.d, q.t0), q.t0 - 90, q.t0 + 90), '#ffffff', 1.6);
  if (mode === 'position' || both) polyline(ctx, curvePts(t => q.f0 * gPosition(t, q.v, q.d, q.t0), q.t0 - 90, q.t0 + 90), '#ff5252', 1.3, [6, 4]);
  ctx.fillStyle = '#fff'; ctx.fillRect(X(q.t0), 0, 1, 10);
  // trace points
  ctx.fillStyle = '#00e5ff'; for (const [t, f] of S.trace) { ctx.beginPath(); ctx.arc(X(t), Y(f), 2.5, 0, 7); ctx.fill(); }
  // predicted curves of the selected match
  const mc = S.matchCurves[S.selRank];
  if (mc) mc.curves.forEach(c => polyline(ctx, c.t.map((t, i) => [t, c.f[i]]), '#76ff03', 1.4));
  // playhead
  const pt = $('player').currentTime + (S.proj?.meta?.audio_offset_s || 0);
  if (pt >= v.t0 && pt <= v.t1) { ctx.fillStyle = '#ff0'; ctx.fillRect(X(pt), 0, 1, ov.height); }
}
$('player').ontimeupdate = drawOverlay;
let drag = null;
ov.onmousedown = e => {
  const r = ov.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
  if ($('traceMode').checked) { drag = {trace: true}; S.trace.push([T(x), F(y)]); drawOverlay(); return; }
  drag = {x, y, v: {...S.view}, moved: false};
};
window.onmouseup = e => {
  if (drag && !drag.trace && !drag.moved && e.target === ov) {   // click = seek audio
    const r = ov.getBoundingClientRect(); const t = T(e.clientX - r.left) - (S.proj?.meta?.audio_offset_s || 0);
    if (t >= 0) $('player').currentTime = t;
  }
  drag = null;
};
ov.onmousemove = e => {
  const r = ov.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
  $('hover').textContent = `t ${fmtT(T(x))}  f ${F(y).toFixed(1)} Hz`;
  if (!drag) return;
  if (drag.trace) { const t = T(x); const last = S.trace[S.trace.length - 1]; if (!last || Math.abs(X(last[0]) - x) > 3) { S.trace.push([t, F(y)]); drawOverlay(); } return; }
  const dx = (x - drag.x) / ov.width * (drag.v.t1 - drag.v.t0), dy = (y - drag.y) / ov.height * (drag.v.f1 - drag.v.f0);
  if (Math.abs(x - drag.x) + Math.abs(y - drag.y) > 3) drag.moved = true;
  S.view = {t0: drag.v.t0 - dx, t1: drag.v.t1 - dx, f0: Math.max(0, drag.v.f0 + dy), f1: drag.v.f1 + dy}; drawSpec();
};
ov.onwheel = e => {
  e.preventDefault(); const r = ov.getBoundingClientRect(); const k = e.deltaY > 0 ? 1.25 : 0.8;
  if (e.shiftKey) { const f = F(e.clientY - r.top); S.view.f0 = Math.max(0, f - (f - S.view.f0) * k); S.view.f1 = f + (S.view.f1 - f) * k; }
  else { const t = T(e.clientX - r.left); S.view.t0 = t - (t - S.view.t0) * k; S.view.t1 = t + (S.view.t1 - t) * k; }
  drawSpec();
};
const zoomT = k => { const c = (S.view.t0 + S.view.t1) / 2, h = (S.view.t1 - S.view.t0) / 2 * k; S.view.t0 = c - h; S.view.t1 = c + h; drawSpec(); };
$('zoomIn').onclick = () => zoomT(0.5); $('zoomOut').onclick = () => zoomT(2);
$('zoomAll').onclick = () => { const s = S.proj.spec; S.view = {t0: s.t0, t1: s.t1, f0: s.f0, f1: Math.min(s.f1, 1000)}; drawSpec(); };
for (const id of ['kindSel', 'zmax']) $(id).onchange = drawSpec;
$('showLines').onchange = drawOverlay; $('clearTrace').onclick = () => { S.trace = []; drawOverlay(); };
window.onresize = drawSpec;
function focusEvent(e) { S.view = {t0: e.t0 - 70, t1: e.t0 + 70, f0: e.f0 * 0.75, f1: e.f0 * 1.3}; drawSpec(); }

// ---------------------------------------------------------------- sliders
const SL = ['f0', 'v', 'd', 't0'];
function sliders() { const o = {}; for (const k of SL) o[k] = parseFloat($('n_' + k).value); return o; }
function setSliders(q) { for (const k of SL) if (q[k] != null) { $('s_' + k).value = q[k]; $('n_' + k).value = (+q[k]).toFixed(k === 'd' ? 0 : 2); } updReadouts(); }
function updReadouts() { const q = sliders(), r = readouts(q.f0, q.v, q.d);
  $('readouts').textContent = `f_pre ${r.fpre.toFixed(2)} Hz · f_post ${r.fpost.toFixed(2)} Hz · max RoC ${r.roc.toFixed(3)} Hz/s · CPA time (geometric) ${fmtT(q.t0 - q.d / C)}`; }
for (const k of SL) {
  $('s_' + k).oninput = () => { $('n_' + k).value = $('s_' + k).value; updReadouts(); drawOverlay(); };
  $('n_' + k).onchange = () => { $('s_' + k).value = $('n_' + k).value; updReadouts(); drawOverlay(); };
}
document.querySelectorAll('input[name=pmode]').forEach(r => r.onchange = drawOverlay); $('showBoth').onchange = drawOverlay;
$('fromView').onclick = () => setSliders({t0: (S.view.t0 + S.view.t1) / 2});
$('fitTrace').onclick = async () => {
  if (S.trace.length < 6) return alert('trace at least 6 points along the curve first (tick trace mode)');
  const r = await post(`/api/projects/${S.pid}/events`, {trace: S.trace, init: null, replace_id: S.selEvent ? S.selEvent.id : undefined});
  S.trace = []; S.events = r.events; S.selEvent = S.events.find(e => e.id === r.event.id); setSliders(r.event); renderEvents(r.gaps); drawOverlay();
};
$('addEvent').onclick = async () => { const r = await post(`/api/projects/${S.pid}/events`, {params: sliders()}); S.events = r.events; S.selEvent = S.events.find(e => e.id === r.event.id); renderEvents(r.gaps); drawOverlay(); };
$('updEvent').onclick = async () => { if (!S.selEvent) return; const r = await post(`/api/projects/${S.pid}/events`, {params: sliders(), replace_id: S.selEvent.id}); S.events = r.events; S.selEvent = S.events.find(e => e.id === r.event.id); renderEvents(r.gaps); drawOverlay(); };

// ---------------------------------------------------------------- events
function renderEvents(gaps) {
  const rows = S.events.map((e, i) => `<tr class="${e === S.selEvent ? 'sel' : ''}" data-id="${e.id}">
    <td><input type="checkbox" class="evsel" ${e.selected ? 'checked' : ''}></td><td style="color:${COLORS[i % COLORS.length]}">#${e.id}</td>
    <td>${e.source}</td><td>${e.conf != null ? `<span class="conf" style="width:${Math.min(60, e.conf * 3)}px"></span> ${e.conf.toFixed(1)}` : ''}</td>
    <td>${fmtT(e.t0)} ±${(e.t0_sigma || 0).toFixed(2)}</td><td>${e.f0.toFixed(2)}</td><td>${e.v.toFixed(1)} [${e.v_min.toFixed(0)}–${e.v_max.toFixed(0)}]</td>
    <td>${e.d.toFixed(0)} [${e.d_min.toFixed(0)}–${e.d_max.toFixed(0)}]</td><td>${e.fit_rms_hz != null ? e.fit_rms_hz.toFixed(2) : ''}</td><td>${(e.line || []).length}</td>
    <td><button class="evgo">view</button><button class="evdel">✕</button></td></tr>`).join('');
  $('evTable').innerHTML = `<tr><th>use</th><th>id</th><th>src</th><th>confidence</th><th>t0 (delayed CPA)</th><th>f0 Hz</th><th>v m/s [range]</th><th>d m [range]</th><th>rms Hz</th><th>pts</th><th></th></tr>` + rows;
  $('evTable').querySelectorAll('tr[data-id]').forEach(tr => {
    const id = +tr.dataset.id, e = S.events.find(x => x.id === id);
    tr.querySelector('.evsel').onchange = async ev => { const r = await post(`/api/projects/${S.pid}/events/${id}`, {selected: ev.target.checked}); S.events = r.events; renderEvents(r.gaps); drawOverlay(); };
    tr.querySelector('.evdel').onclick = async () => { const r = await post(`/api/projects/${S.pid}/events/${id}`, {delete: true}); S.events = r.events; S.selEvent = null; renderEvents(r.gaps); drawOverlay(); };
    tr.querySelector('.evgo').onclick = () => { S.selEvent = e; setSliders(e); focusEvent(e); renderEvents(); $('updEvent').disabled = false; };
    tr.onclick = ev => { if (ev.target.tagName === 'INPUT' || ev.target.tagName === 'BUTTON') return; S.selEvent = e; setSliders(e); $('updEvent').disabled = false; renderEvents(); drawOverlay(); };
  });
  if (gaps) S.gaps = gaps;
  const g = S.gaps || [];
  $('gapTable').innerHTML = '<tr><th>pair</th><th>gap (s)</th><th>gap (m:s)</th><th>± tol (s)</th></tr>' +
    g.map(r => `<tr><td>#${r.a} → #${r.b}</td><td>${r.gap_s.toFixed(2)}</td><td>${fmtT(r.gap_s)}</td><td>${r.tol_s.toFixed(2)}</td></tr>`).join('');
}
$('detectBtn').onclick = async () => {
  $('detMsg').textContent = 'searching for faint Doppler curves (whitening, harmonic stacking, template bank, ridge tracing)…';
  const r = await post(`/api/projects/${S.pid}/detect`, {}); S.events = r.events;
  $('detMsg').textContent = `${r.n_found} candidates (sorted by confidence). Tick the ones to use.`; renderEvents(); drawOverlay();
};

// ---------------------------------------------------------------- search
const ADV = ['grid_m', 'dense_m', 'max_radius_m', 'offset_tol_s', 'tol_sigma_k', 'silence_ratio', 'audibility_radius_m', 'half_window_s',
  'dt_sweep_s', 'f0_sweep_rel', 'harmonics', 'max_fine_points', 'single_time_tol_s', 'single_dt_sweep_s'];
function buildAdv(defaults) {
  $('advParams').innerHTML = ADV.map(k => `<label>${k}<input id="p_${k}" type="number" step="any" value="${defaults[k]}"></label>`).join('') +
    `<label title="do jets count as competing (would-have-been-heard) traffic in the silence filter?">silence_count_jets<select id="p_silence_count_jets"><option value="">same as jets switch</option><option value="1">yes</option><option value="0">no</option></select></label>` +
    `<label title="N>2 only: one window over all events (span) or one per time-adjacent pair (consecutive)">silence_windows<select id="p_silence_windows"><option>consecutive</option><option>span</option></select></label>` +
    `<label title="NOT in the video (off = exact method). Doppler depends on motion relative to the air; ADS-B gives ground velocity.">wind<select id="p_wind"><option value="off">off (video method)</option><option value="era5">Open-Meteo/ERA5 at flight altitude</option><option value="fit">fit per candidate (top results)</option><option value="manual">manual u,v</option></select></label>` +
    `<label title="full = moving-medium physics (wind cancels to first order); air_los = air-relative source velocity along the line of sight">wind_model<select id="p_wind_model"><option>full</option><option>air_los</option></select></label>` +
    `<label>wind u,v (m/s, toward E,N)<input id="p_wind_uv" type="text" value="0,0"></label>`;
}
function params() {
  const p = {include_jets: $('pJets').checked, ranking: $('pRank').value};
  for (const k of ADV) p[k] = parseFloat($('p_' + k).value);
  const sj = $('p_silence_count_jets').value; if (sj !== '') p.silence_count_jets = sj === '1';
  p.silence_windows = $('p_silence_windows').value;
  p.wind = $('p_wind').value; p.wind_model = $('p_wind_model').value;
  if (p.wind === 'manual') p.wind_uv = $('p_wind_uv').value.split(',').map(parseFloat);
  if ($('regionSel').value === 'map') { const b = map.getBounds(); p.region = [b.getSouth(), b.getNorth(), b.getWest(), b.getEast()]; }
  else if ($('regionSel').value === 'preset' && S.presetRegion) p.region = S.presetRegion;
  return p;
}
$('runBtn').onclick = async () => {
  const mode = $('modeSel').value, sel = S.events.filter(e => e.selected);
  if (mode === 'multi' && sel.length < 2) return alert('select at least 2 events (multi-event mode)');
  if (mode === 'single' && sel.length !== 1) return alert('select exactly 1 event (single-flight mode)');
  try {
    const r = await post(`/api/projects/${S.pid}/search`, {mode, day: $('daySel').value, params: params(), use_start_time: $('useStart').checked, clock_uncertainty_s: +$('clockU').value});
    pollJob(r.job);
  } catch (err) { $('jobMsg').textContent = err.message; }
};
async function pollJob(jid) {
  const j = await api(`/api/jobs/${jid}`);
  $('progBar').style.width = (j.progress * 100) + '%'; $('jobMsg').textContent = `${j.status} · ${j.stage || ''}`;
  $('jobLog').textContent = (j.log || []).join('\n'); $('jobLog').scrollTop = 1e9;
  if (j.status === 'running' || j.status === 'queued') return setTimeout(() => pollJob(jid), 1000);
  if (j.error) $('jobMsg').textContent = 'error: ' + j.error;
  await loadResults(jid);
}
async function loadResults(sel) {
  const rs = await api(`/api/projects/${S.pid}/results`);
  $('resSel').innerHTML = '<option value="">— previous results —</option>' + rs.map(r => `<option value="${r.id}">${new Date(r.created * 1000).toLocaleString()} · ${r.mode} · ${r.n_events} ev · ${r.status}${r.truth_rank ? ' · truth #' + r.truth_rank : ''}${r.preset ? ' · preset' : ''}${r.tag ? ' · ' + r.tag : ''}</option>`).join('');
  const m = S.proj.meta, dm = m.default_tag, md = m.default_mode;
  const pick = sel || (rs.find(r => dm && r.tag === dm) || rs.find(r => r.preset && r.mode === md) || rs[0] || {}).id;
  if (pick) { $('resSel').value = pick; await showJob(pick); }
}
$('resSel').onchange = e => e.target.value && showJob(e.target.value);
async function showJob(jid) {
  const j = await api(`/api/jobs/${jid}?full=1`); S.job = j; S.summary = j.summary || {}; S.compare = []; S.matchCurves = {}; S.heat = null;
  $('jobLog').textContent = (j.log || []).join('\n');
  const s = S.summary;
  $('summary').innerHTML = s.status !== 'ok' ? `status: ${s.status}` :
    (j.mode === 'multi' ? `${j.events.length} events · gaps from fitted t0s ${s.gaps.slice(1).map(g => g.toFixed(1)).join(', ')} s (vs event 1) · candidates ${s.n_candidates} → offset ${s.n_offset} → silence ${s.n_silence1} → ${s.n_silence2} → dense ${s.n_dense}` :
      `single-flight · ${s.n} (point, flight) candidates · known start ${fmtUtc(j.rec_start)}`) + ` · ${(s.elapsed_s || 0).toFixed(0)} s` +
    (s.truth ? ` · <b>truth rank: ${s.truth_rank ?? 'not in top list'}</b>` : '');
  renderResults(); renderMap(); drawScatter();
  if ((s.combos || []).length) selectRank(1);
}
function renderResults() {
  const s = S.summary, N = S.job.mode === 'multi' ? S.job.events.length : 1;
  if (!s.combos) { $('resTable').innerHTML = ''; return; }
  $('resTable').innerHTML = `<tr><th>cmp</th><th>#</th><th>score</th>${[...Array(N).keys()].map(k => `<th>ev${k + 1} flight</th><th>ev${k + 1}</th>`).join('')}<th>dt s</th><th>lat, lon</th>${s.truth ? '<th>to truth</th>' : ''}</tr>` +
    s.combos.slice(0, 60).map(r => `<tr data-r="${r.rank}" class="${r.rank === S.selRank ? 'sel' : ''}"><td><input type="checkbox" class="cmpchk" ${S.compare.includes(r.rank) ? 'checked' : ''}></td><td>${r.rank}</td><td>${r.total_score.toFixed(3)}</td>
      ${r.flights.map((f, k) => `<td title="hex ${f.hex} · fid ${f.fid} · ${f.cls}">${f.reg || f.hex} ${f.type || ''}</td><td>${(r.event_scores[k] ?? 0).toFixed(2)}</td>`).join('')}
      <td>${r.dt_s != null ? r.dt_s.toFixed(2) : ''}${r.wind_uv && (r.wind_uv[0] || r.wind_uv[1]) ? ` <small title="fitted wind u,v m/s">w ${r.wind_uv.map(x => x.toFixed(0)).join(',')}</small>` : ''}</td><td>${r.lat.toFixed(5)}, ${r.lon.toFixed(5)}</td>${s.truth ? `<td>${(r.dist_to_truth_m / 1000).toFixed(2)} km</td>` : ''}</tr>`).join('');
  $('resTable').querySelectorAll('tr[data-r]').forEach(tr => {
    const rank = +tr.dataset.r;
    tr.onclick = e => { if (e.target.classList.contains('cmpchk')) return; selectRank(rank); };
    tr.querySelector('.cmpchk').onchange = e => { if (e.target.checked) { S.compare.push(rank); if (S.compare.length > 3) S.compare.shift(); } else S.compare = S.compare.filter(x => x !== rank); renderResults(); renderCompare(); };
  });
}
async function selectRank(rank) {
  S.selRank = rank; renderResults();
  const r = S.summary.combos[rank - 1]; map.setView([r.lat, r.lon], Math.max(map.getZoom(), 12)); selMarker.setLatLng([r.lat, r.lon]).addTo(map);
  if (!S.compare.includes(rank) && S.compare.length < 3) S.compare.push(rank);
  await getCurves(rank); drawOverlay(); renderCompare();
  $('selLinks').innerHTML = `#${rank} ${r.lat.toFixed(6)}, ${r.lon.toFixed(6)} · ` + gmaps(r.lat, r.lon);
  try { S.heat = await api(`/api/jobs/${S.job.id}/heatmap?rank=${rank}&cached_only=1`); drawHeat(); }
  catch (e) { if (heatLayer) { map.removeLayer(heatLayer); heatLayer = null; } $('heatMsg').textContent = 'no cached heat map for this match (click the button to compute)'; }
}
async function getCurves(rank) { if (!S.matchCurves[rank]) S.matchCurves[rank] = await api(`/api/jobs/${S.job.id}/curves?rank=${rank}`); return S.matchCurves[rank]; }

// ---------------------------------------------------------------- map
const map = L.map('map').setView([39.5, -98], 4);
const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '© OpenStreetMap contributors'}).addTo(map);
const sat = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {maxZoom: 19, attribution: 'Imagery © Esri'});
L.control.layers({'Map': osm, 'Satellite': sat}).addTo(map);
const gmaps = (la, lo) => `<a target="_blank" href="https://www.google.com/maps/search/?api=1&query=${la},${lo}">Google Maps</a> · <a target="_blank" href="https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${la},${lo}">Street View</a>`;
let ptLayer = L.layerGroup().addTo(map), heatLayer = null, truthMarker = null;
const selMarker = L.circleMarker([0, 0], {radius: 9, color: '#76ff03', weight: 3, fill: false});
function clearMap() { ptLayer.clearLayers(); if (heatLayer) map.removeLayer(heatLayer); heatLayer = null; if (truthMarker) map.removeLayer(truthMarker); truthMarker = null; map.removeLayer(selMarker); }
function scoreColor(x) { const h = 240 - 240 * Math.max(0, Math.min(1, x)); return `hsl(${h},90%,55%)`; }
function renderMap() {
  clearMap(); const s = S.summary;
  if (s.truth) truthMarker = L.marker([s.truth.lat, s.truth.lon], {title: 'ground truth'}).bindPopup('ground truth').addTo(map);
  if (!s.combos) return;
  const top = s.combos.slice(0, 200), lo = top[top.length - 1].total_score, hi = top[0].total_score;
  if ($('showPts').checked) top.slice().reverse().forEach(r => {
    L.circleMarker([r.lat, r.lon], {radius: r.rank <= 10 ? 7 : 4, color: '#000', weight: 1, fillColor: scoreColor((r.total_score - lo) / (hi - lo + 1e-9)), fillOpacity: 0.9})
      .bindTooltip(`#${r.rank} ${r.total_score.toFixed(3)} · ${r.flights.map(f => f.reg || f.hex).join(' + ')}`).on('click', () => selectRank(r.rank)).addTo(ptLayer);
  });
  const lay = S.job.mode === 'multi' ? ['combined', ...S.job.events.map((_, k) => 'event' + (k + 1))] : ['event1'];
  $('heatLayer').innerHTML = lay.map(l => `<option>${l}</option>`).join('');
  if (s.truth) map.setView([s.truth.lat, s.truth.lon], 11); else map.setView([top[0].lat, top[0].lon], 9);
}
$('showPts').onchange = renderMap;
$('heatBtn').onclick = async () => {
  if (!S.job) return; $('heatMsg').textContent = 'computing 25 m heat map around the selected match…';
  try { S.heat = await api(`/api/jobs/${S.job.id}/heatmap?rank=${S.selRank}`); drawHeat(); $('heatMsg').textContent = ''; } catch (e) { $('heatMsg').textContent = e.message; }
};
$('heatLayer').onchange = drawHeat;
function drawHeat() {
  if (!S.heat) return; const v = S.heat.layers[$('heatLayer').value]; if (!v) return;
  const ok = v.filter(x => x > -1e8); const mx = Math.max(...ok), mn = ok.sort((a, b) => a - b)[Math.floor(ok.length * 0.5)];
  const pts = []; for (let i = 0; i < v.length; i++) if (v[i] > mn) pts.push([S.heat.lat[i], S.heat.lon[i], ((v[i] - mn) / (mx - mn)) ** 2]);
  if (heatLayer) map.removeLayer(heatLayer);
  heatLayer = L.heatLayer(pts, {radius: 14, blur: 12, max: 1, minOpacity: 0.2}).addTo(map);
  let bi = 0; for (let i = 0; i < v.length; i++) if (v[i] > v[bi]) bi = i;
  $('heatMsg').textContent = `heat-map peak ${S.heat.lat[bi].toFixed(6)}, ${S.heat.lon[bi].toFixed(6)}` + (S.summary.truth ? ` · ${(haversine(S.heat.lat[bi], S.heat.lon[bi], S.summary.truth.lat, S.summary.truth.lon)).toFixed(0)} m from truth` : '');
}
function haversine(a, b, c, d) { const R = 6371008.8, r = Math.PI / 180; const x = Math.sin((c - a) * r / 2) ** 2 + Math.cos(a * r) * Math.cos(c * r) * Math.sin((d - b) * r / 2) ** 2; return 2 * R * Math.asin(Math.sqrt(x)); }

// ---------------------------------------------------------------- scatter / histogram
function drawScatter() {
  const cv = $('scatter'), ctx = cv.getContext('2d'); ctx.clearRect(0, 0, cv.width, cv.height); ctx.fillStyle = '#111'; ctx.fillRect(0, 0, cv.width, cv.height);
  const s = S.summary; ctx.fillStyle = '#aaa'; ctx.font = '11px sans-serif';
  if (s.histogram) {
    const h = s.histogram, mx = Math.max(...h.counts), n = h.counts.length;
    h.counts.forEach((c, i) => { ctx.fillStyle = '#4fc3f7'; const w = (cv.width - 40) / n; ctx.fillRect(30 + i * w, cv.height - 20 - c / mx * (cv.height - 40), w - 1, c / mx * (cv.height - 40)); });
    ctx.fillStyle = '#aaa'; ctx.fillText(`score histogram (−mean |f_model − f_line| Hz): ${h.edges[0].toFixed(1)} … ${h.edges[n].toFixed(2)}`, 30, 12);
    if (s.combos) { const b = s.combos[0].total_score; const x = 30 + (b - h.edges[0]) / (h.edges[n] - h.edges[0]) * (cv.width - 40); ctx.fillStyle = '#76ff03'; ctx.fillRect(x, 16, 2, cv.height - 36); }
    return;
  }
  const p = s.points; if (!p || p.ev.length < 2) return;
  const a = p.ev[0], b = p.ev[1]; const ax = [Math.min(...a), Math.max(...a)], bx = [Math.min(...b), Math.max(...b)];
  const px = x => 35 + (x - ax[0]) / (ax[1] - ax[0] + 1e-9) * (cv.width - 45), py = y => cv.height - 20 - (y - bx[0]) / (bx[1] - bx[0] + 1e-9) * (cv.height - 35);
  for (let i = p.lat.length - 1; i >= 0; i--) { const c = p.combo[i]; ctx.fillStyle = c >= 0 && c < 10 ? COLORS[c % COLORS.length] : '#555'; ctx.fillRect(px(a[i]), py(b[i]), 2, 2); }
  ctx.fillStyle = '#aaa'; ctx.fillText('event 1 score →', cv.width - 110, cv.height - 5); ctx.fillText('↑ event 2 score  (colour = top-10 combos)', 38, 12);
}

// ---------------------------------------------------------------- side-by-side comparison
async function renderCompare() {
  const box = $('compare'); box.innerHTML = '';
  if (!S.job) return;
  for (const rank of S.compare) {
    const r = S.summary.combos[rank - 1], mc = await getCurves(rank);
    const div = document.createElement('div'); div.className = 'cmp';
    div.innerHTML = `<b>#${rank}</b> score ${r.total_score.toFixed(3)} · ${r.lat.toFixed(5)}, ${r.lon.toFixed(5)}${r.dist_to_truth_m != null ? ` · ${(r.dist_to_truth_m / 1000).toFixed(2)} km to truth` : ''} · ${gmaps(r.lat, r.lon)}<br>` +
      r.flights.map((f, k) => `ev${k + 1}: <b>${f.reg || '?'}</b> ${f.type || ''} (hex ${f.hex}) · CPA ${(r.cpa_distance_m[k] / 1000).toFixed(2)} km @ ${fmtUtc(r.cpa_time_utc[k]).slice(11)} · f0 ${r.f0[k].toFixed(2)} · s ${(r.event_scores[k] ?? 0).toFixed(3)}`).join('<br>');
    box.appendChild(div);
    const evs = S.job.mode === 'multi' ? S.job.events : [S.job.events[0]];
    const holder = document.createElement('div'); div.insertBefore(holder, div.firstChild);
    evs.forEach((e, k) => {
      const cv = document.createElement('canvas'); holder.appendChild(cv); cv.width = 420; cv.height = 170;
      const v = {t0: e.t0 - 60, t1: e.t0 + 60, f0: e.f0 * 0.8, f1: e.f0 * 1.25};
      const im = new Image(); im.onload = () => {
        const c = cv.getContext('2d'); c.drawImage(im, 0, 0);
        const x = t => (t - v.t0) / (v.t1 - v.t0) * cv.width, y = f => (v.f1 - f) / (v.f1 - v.f0) * cv.height;
        if (e.line) { c.fillStyle = '#4fc3f7'; for (const [t, f] of e.line) c.fillRect(x(t) - 1, y(f) - 1, 2, 2); }
        const cc = mc.curves[k]; c.strokeStyle = '#76ff03'; c.lineWidth = 1.5; c.beginPath(); cc.t.forEach((t, i) => { const X_ = x(t), Y_ = y(cc.f[i]); i ? c.lineTo(X_, Y_) : c.moveTo(X_, Y_); }); c.stroke();
        c.fillStyle = '#fff'; c.font = '11px sans-serif'; c.fillText(`event ${k + 1} (#${e.id}) — predicted (green) vs traced (blue)`, 4, 12);
      };
      im.src = `/api/projects/${S.job.pid}/spec.png?t0=${v.t0}&t1=${v.t1}&f0=${v.f0}&f1=${v.f1}&w=420&h=170&kind=z&hi=5`;
    });
  }
}

// ---------------------------------------------------------------- init
(async () => {
  S.info = await api('/api/info');
  $('daySel').innerHTML = S.info.days.map(d => `<option>${d}</option>`).join('');
  const tzs = Intl.supportedValuesOf ? Intl.supportedValuesOf('timeZone') : ['UTC', 'America/New_York', 'America/Los_Angeles'];
  $('tzSel').innerHTML = ['UTC', ...tzs.filter(z => z !== 'UTC')].map(z => `<option>${z}</option>`).join('');
  buildAdv(S.info.params);
  $('regionSel').insertAdjacentHTML('beforeend', '<option value="preset">preset region (example)</option>');
  await loadProjects();
})();
